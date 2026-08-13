#!/usr/bin/env python3
"""Factory CLI. Every path, fence and memory knob is an argument.

    # what WOULD be built (prints the BUILD_MANIFEST, writes nothing)
    python -m factory.build --dry-run \
        --results '/path/panel/results_*.parquet' \
        --items   '/path/panel/items_panel.parquet' \
        --item-detail-csv /path/item_detail.csv \
        --item-group-csv  /path/item_group.csv \
        --years 2005-2023 --recipe train_2015_2023:2015-01-01:2024-01-01 \
        --rung r250k:0.0031 --rung r500k:0.0062 \
        --p4-certification out/p4_crosstab_certification.json \
        --staging-dir /tmp/factory_stage --output-dir out/frames

    # measure the thresholds that hit 250k/500k/1M/2M, then pin them
    python -m factory.build --calibrate ... (same inputs)

Exit codes: 0 ok, 2 usage/build error, 3 a gate refused the build.
"""
import argparse
import json
import sys
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from . import blocks, emit, gates, observability, sampling, sources


def parse_years(text: str) -> List[int]:
    years: List[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            years.extend(range(int(lo), int(hi) + 1))
        else:
            years.append(int(part))
    return sorted(set(years))


def parse_recipe(text: str) -> emit.WindowRecipe:
    parts = text.split(":")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            f"--recipe expects name:start:end (inclusive:exclusive), got {text!r}")
    name, start, end = parts
    return emit.WindowRecipe(name=name, start=date.fromisoformat(start),
                             end=date.fromisoformat(end))


def parse_rung(text: str) -> Tuple[str, float, float]:
    parts = text.split(":")
    if len(parts) not in (2, 3):
        raise argparse.ArgumentTypeError(
            f"--rung expects name:base[:enriched], got {text!r}")
    name = parts[0]
    base = float(parts[1])
    enriched = float(parts[2]) if len(parts) == 3 else base
    return name, base, enriched


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="factory.build", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_argument_group("inputs (injected; nothing is defaulted)")
    src.add_argument("--results", required=True)
    src.add_argument("--items", required=True)
    src.add_argument("--item-detail-csv", required=True)
    src.add_argument("--item-group-csv", required=True)
    src.add_argument("--completed-ts", default=None,
                     help="OPTIONAL 2024-25 sidecar; diagnostics only, never required")
    src.add_argument("--location-csv", default=None,
                     help="mdr_rfr_location.csv for B6; absent -> B6 NULLs + status")

    win = ap.add_argument_group("population + windows")
    win.add_argument("--years", required=True, help="e.g. 2005-2023 or 2015,2016")
    win.add_argument("--recipe", action="append", default=[], type=parse_recipe,
                     help="name:start:end (inclusive:exclusive); repeatable")
    win.add_argument("--target-classes", default="3,4",
                     help="TARGET population classes (D7 rule = 3&4); 'all' for no filter")
    win.add_argument("--history-classes", default="all",
                     help="HISTORY classes; default 'all' = UNFILTERED (a class-3/5/7 "
                          "prior test is still this vehicle's history)")
    win.add_argument("--eval-slice", action="store_true",
                     help="permit target dates >= 2024-01-01")
    win.add_argument("--build-confirmation", action="store_true",
                     help="owner-only: build the sealed 2025-H2 slice")
    win.add_argument("--confirmation-prereg-sha", default=None)
    win.add_argument("--confirmation-prereg-path", default=None)

    smp = ap.add_argument_group("sampling")
    smp.add_argument("--rung", action="append", default=[], type=parse_rung,
                     help="name:base[:enriched] as unit-interval thresholds; repeatable")
    smp.add_argument("--calibrate", action="store_true",
                     help="measure the thresholds hitting 250k/500k/1M/2M and stop")

    run = ap.add_argument_group("run knobs (owner sets these)")
    run.add_argument("--staging-dir", required=True)
    run.add_argument("--output-dir", required=True)
    run.add_argument("--memory-limit", default="3GB")
    run.add_argument("--stage-max-u", type=float, default=None,
                     help="full-pop pushdown: stage only vehicles with "
                          "unit_hash(vehicle_id, recipe.salt) < this value "
                          "(single-recipe builds only; superset of the rung)")
    run.add_argument("--temp-dir", default=None)
    run.add_argument("--max-temp", default="1.5GiB")
    run.add_argument("--threads", type=int, default=None)
    run.add_argument("--n-buckets", type=int, default=64)
    run.add_argument("--fail-basis", choices=("final", "initial"), default="final")
    run.add_argument("--defect-detail", choices=("rows", "counts", "none"), default="rows",
                     help="packets defect payload. THIS IS A CAPABILITY: 'counts'/"
                          "'none' emit no per-item payload, so any consumer that "
                          "reads defect items must be refused. Declare consumers "
                          "with --for-consumer and preflight will do exactly that.")
    run.add_argument("--item-coverage-csv", default=None,
                     help="owner-produced item-coverage cell ledger "
                          "(test_date|schema_epoch|outcome_class|coverage|"
                          "attribution|note). Absent -> items_coverage_mode="
                          "'assumed_covered'; never 'everything is covered'.")
    run.add_argument("--for-consumer", action="append", default=[], dest="consumers",
                     metavar="SPEC",
                     help="a registered downstream consumer this build promises to "
                          "serve (e.g. b0_module:section). Repeatable. Incompatible "
                          "builder/consumer pairs are REFUSED in preflight, before "
                          "any packet is created (PREREG_CUBE_v2 §5).")
    run.add_argument("--max-priors", type=int, default=None)
    run.add_argument("--no-packets", action="store_true")
    run.add_argument("--write-batch-rows", type=int, default=50_000)
    run.add_argument("--p4-certification", default=None)
    run.add_argument("--serve-view-classes", default=None)
    run.add_argument("--dry-run", action="store_true",
                     help="print the BUILD_MANIFEST that WOULD be produced")
    return ap


def _classes(text: str) -> Optional[Sequence[str]]:
    if text.strip().lower() == "all":
        return None
    return tuple(c.strip() for c in text.split(",") if c.strip())


def make_factory(args: argparse.Namespace) -> emit.Factory:
    inputs = sources.Inputs(
        results=args.results, items=args.items,
        item_detail_csv=args.item_detail_csv, item_group_csv=args.item_group_csv,
        completed_ts=args.completed_ts, location_csv=args.location_csv)
    config = emit.BuildConfig(
        staging_dir=args.staging_dir, output_dir=args.output_dir,
        memory_limit=args.memory_limit, temp_directory=args.temp_dir,
        max_temp_directory_size=args.max_temp, threads=args.threads,
        n_buckets=args.n_buckets, target_classes=_classes(args.target_classes),
        history_classes=_classes(args.history_classes), fail_basis=args.fail_basis,
        defect_detail=args.defect_detail, emit_packets=not args.no_packets,
        max_priors=args.max_priors, write_batch_rows=args.write_batch_rows,
        p4_certification_path=args.p4_certification,
        serve_view_classes_path=args.serve_view_classes,
        item_coverage_csv=args.item_coverage_csv,
        consumers=tuple(args.consumers))
    return emit.Factory(inputs, config)


def ladder_from_args(rungs: Sequence[Tuple[str, float, float]]) -> sampling.RungLadder:
    if not rungs:
        raise SystemExit("no --rung given: pin thresholds (use --calibrate to measure them)")
    fractions: Dict[str, Tuple[float, float]] = {n: (b, e) for n, b, e in rungs}
    return sampling.ladder_from_fractions(fractions, sampling.DEFAULT_RUNG_TARGETS)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    years = parse_years(args.years)
    recipes: List[emit.WindowRecipe] = list(args.recipe)
    if not recipes:
        raise SystemExit("no --recipe given: a build must name its target window")
    if args.eval_slice:
        recipes = [emit.WindowRecipe(r.name, r.start, r.end, eval_slice=True,
                                     salt=sampling.EVAL_SALT) for r in recipes]
    if args.build_confirmation:
        recipes = [emit.WindowRecipe(r.name, r.start, r.end, confirmation=True,
                                     salt=sampling.CONFIRMATION_SALT) for r in recipes]

    factory = make_factory(args)
    factory.connect()
    try:
        preflight = factory.preflight(
            years, recipes, build_confirmation=args.build_confirmation,
            confirmation_prereg_sha=args.confirmation_prereg_sha,
            confirmation_prereg_path=args.confirmation_prereg_path)
    except (gates.GateFailure, sources.MissingYears, sources.MissingInput,
            observability.LedgerError) as exc:
        print(f"WOULD REFUSE: {exc}" if args.dry_run else f"REFUSED: {exc}",
              file=sys.stderr)
        return 3

    if args.dry_run:
        ladder = (ladder_from_args(args.rung) if args.rung
                  else sampling.RungLadder([sampling.Rung("dry_run", 1.0)]))
        manifest = factory.build_manifest(
            years, recipes, ladder, preflight,
            {"staged": "not run (--dry-run)"},
            {"recipes": {r.name: {"window": r.as_manifest(), "rungs": "not built"}
                         for r in recipes}},
            dry_run=True)
        print(json.dumps(manifest, indent=1, default=str))
        print(f"\n-- columns: meta {len(blocks.META_COLUMNS)}, new B1-B6 "
              f"{blocks.n_new_columns()} (cap {blocks.NEW_COLUMN_CAP})", file=sys.stderr)
        return 0

    prepare_report = factory.prepare(years, recipes,
                                     restrict_max_u=args.stage_max_u)
    if args.calibrate:
        for recipe in recipes:
            print(json.dumps(factory.calibrate(recipe), indent=1, default=str))
        return 0

    ladder = ladder_from_args(args.rung)
    manifest = factory.build(years, recipes, ladder, preflight, prepare_report)
    print(json.dumps(manifest["results"], indent=1, default=str))
    print(f"BUILD_MANIFEST written to {args.output_dir}/BUILD_MANIFEST.json",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
