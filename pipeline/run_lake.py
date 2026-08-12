#!/usr/bin/env python3
"""Lake orchestrator: DVSA anonymised bulk -> canonical parquet lake.

Workstation entry point (see docs/v58/RUNBOOK_DATA.md for the full recipe,
disk budget, and download identity). Subcommands:

    ingest-results  --source-dir DIR --lake-dir DIR [--force]
    ingest-items    --source-dir DIR --lake-dir DIR --rfr-detail F --rfr-group F [--force]
    build-cycles    --lake-dir DIR [--gap-days N]
    check           --lake-dir DIR [--gate continuity|results|items|all]
    all             (ingest-results -> continuity gate -> ingest-items -> cycles -> checks)

Day-1 rule (riskiest assumption of the program): after ingest-results, run
`check --gate continuity` BEFORE investing in anything else. If the
publication's vehicle_id space is not consistent across the full depth,
stop and renegotiate the window (docs/v58/DECISIONS.md D1).

Exit code is non-zero when any gate fails. Requires requirements-train.txt
(duckdb) -- this module never runs in the serving environment.
"""
import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional

from pipeline.lake import checks as lake_checks
from pipeline.lake import cycles as lake_cycles
from pipeline.lake.ingest_items import ITEMS_DATASET, ingest_items_file, register_rfr_category_table
from pipeline.lake.ingest_results import RESULTS_DATASET, ingest_results_file
from pipeline.lake.manifest import LakeManifest
from pipeline.lake.rfr_mapping import load_rfr_mapping

logger = logging.getLogger("pipeline.run_lake")

CYCLES_DATASET = "cycles"


def _connect(memory_limit: str, threads: Optional[int],
             max_temp: Optional[str] = None):
    import duckdb  # lazy: requirements-train only

    con = duckdb.connect()
    con.execute(f"SET memory_limit='{memory_limit}'")
    if threads:
        con.execute(f"SET threads={threads}")
    if max_temp:
        # hard spill ceiling: fail loud at the cap instead of consuming the
        # volume (2026-08-12 invariant after two spill-exhaustion incidents)
        con.execute(f"PRAGMA max_temp_directory_size='{max_temp}'")
    return con


def _relation(lake_dir: Path, dataset: str) -> str:
    glob = (Path(lake_dir) / dataset / "**" / "*.parquet").as_posix()
    return f"read_parquet('{glob}', hive_partitioning=true)"


def _source_files(source_dir: Path, patterns: List[str]) -> List[Path]:
    files: List[Path] = []
    for pattern in patterns:
        files.extend(sorted(Path(source_dir).rglob(pattern)))
    return [f for f in files if f.is_file()]


def _load_or_new_manifest(lake_dir: Path, args: argparse.Namespace) -> LakeManifest:
    manifest = LakeManifest.load(lake_dir)
    if manifest is None:
        manifest = LakeManifest.new(config={
            "gap_days": getattr(args, "gap_days", lake_cycles.DEFAULT_CYCLE_GAP_DAYS),
        })
    return manifest


def cmd_ingest_results(args: argparse.Namespace) -> int:
    con = _connect(args.memory_limit, args.threads, getattr(args, "max_temp", None))
    lake_dir = Path(args.lake_dir)
    manifest = _load_or_new_manifest(lake_dir, args)
    files = _source_files(Path(args.source_dir), ["test_result*.csv", "test_result*.txt"])
    if not files:
        logger.error("no test_result* files under %s", args.source_dir)
        return 2
    total = 0
    for path in files:
        rows = ingest_results_file(con, path, lake_dir, manifest, force=args.force)
        total += rows or 0
        manifest.save(lake_dir)
    logger.info("results ingest complete: %s new rows from %s files", total, len(files))
    return 0


def cmd_ingest_items(args: argparse.Namespace) -> int:
    con = _connect(args.memory_limit, args.threads, getattr(args, "max_temp", None))
    lake_dir = Path(args.lake_dir)
    manifest = _load_or_new_manifest(lake_dir, args)
    rfr_map = load_rfr_mapping(args.rfr_detail, args.rfr_group, args.test_class)
    if rfr_map is None:
        logger.warning(
            "RfR lookup tables not found (%s / %s): item categories will be "
            "NULL and check_category_coverage WILL fail. Download the "
            "publication's lookup-table archive.", args.rfr_detail, args.rfr_group,
        )
    register_rfr_category_table(con, rfr_map)
    results_rel = _relation(lake_dir, RESULTS_DATASET)
    if getattr(args, "results_years", None):
        # Year-scoped join hint (2026-08-12): the per-file items JOIN against
        # the FULL results relation spills >7GiB at full depth. Items rows
        # join their archive year +/-1 (adjacent-year spillover is real;
        # wider crossing has never been observed); rows outside the scope
        # fall into the documented orphan path and surface in reconciliation.
        years = ",".join(str(int(y)) for y in args.results_years.split(","))
        results_rel = f"(SELECT * FROM {results_rel} WHERE test_year IN ({years}))"
    files = _source_files(Path(args.source_dir), ["test_item*.csv", "test_item*.txt"])
    if not files:
        logger.error("no test_item* files under %s", args.source_dir)
        return 2
    total = 0
    for path in files:
        rows = ingest_items_file(con, path, lake_dir, results_rel, manifest, force=args.force)
        total += rows or 0
        manifest.save(lake_dir)
    logger.info("items ingest complete: %s new rows from %s files", total, len(files))
    return 0


def cmd_build_cycles(args: argparse.Namespace) -> int:
    con = _connect(args.memory_limit, args.threads, getattr(args, "max_temp", None))
    lake_dir = Path(args.lake_dir)
    manifest = _load_or_new_manifest(lake_dir, args)
    out_dir = lake_dir / CYCLES_DATASET
    out_dir.mkdir(parents=True, exist_ok=True)
    sql = lake_cycles.build_cycles_sql(_relation(lake_dir, RESULTS_DATASET), args.gap_days)
    con.execute(f"""
        COPY ({sql}) TO '{out_dir.as_posix()}'
        (FORMAT parquet, COMPRESSION zstd, PARTITION_BY (test_year),
         OVERWRITE_OR_IGNORE true)
    """)
    rows = con.execute(
        f"SELECT count(*) FROM {_relation(lake_dir, CYCLES_DATASET)}"
    ).fetchone()[0]
    manifest.config["gap_days"] = args.gap_days
    manifest.save(lake_dir)
    logger.info("cycles built: %s rows (gap_days=%s)", rows, args.gap_days)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    con = _connect(args.memory_limit, args.threads, getattr(args, "max_temp", None))
    lake_dir = Path(args.lake_dir)
    manifest = _load_or_new_manifest(lake_dir, args)
    results_rel = _relation(lake_dir, RESULTS_DATASET)
    results: List[lake_checks.CheckResult] = []

    if args.gate in ("continuity", "results", "all"):
        if args.gate == "continuity":
            results.append(lake_checks.check_vehicle_continuity(con, results_rel))
        else:
            results.extend(lake_checks.run_results_checks(con, results_rel))
    if args.gate in ("items", "all"):
        items_rel = _relation(lake_dir, ITEMS_DATASET)
        results.extend(lake_checks.run_items_checks(con, items_rel, results_rel))

    failed = 0
    for r in results:
        manifest.record_check(r.name, r.passed, r.detail)
        status = "PASS" if r.passed else "FAIL"
        print(f"{status} [{r.name}] {r.detail}")
        failed += 0 if r.passed else 1
    manifest.save(lake_dir)
    if failed:
        print(f"\n{failed} of {len(results)} lake checks FAILED", file=sys.stderr)
        return 1
    print(f"\nall {len(results)} lake checks passed")
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    rc = cmd_ingest_results(args)
    if rc:
        return rc
    gate_args = argparse.Namespace(**{**vars(args), "gate": "continuity"})
    rc = cmd_check(gate_args)
    if rc:
        print(
            "\nSTOP: vehicle continuity failed. The full-depth premise does "
            "not hold for this download -- renegotiate WINDOW_START "
            "(docs/v58/DECISIONS.md D1) before ingesting anything else.",
            file=sys.stderr,
        )
        return rc
    rc = cmd_ingest_items(args)
    if rc:
        return rc
    rc = cmd_build_cycles(args)
    if rc:
        return rc
    return cmd_check(argparse.Namespace(**{**vars(args), "gate": "all"}))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--memory-limit", default="8GB")
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--max-temp", default=None,
                        help="hard duckdb spill ceiling, e.g. 7GiB (fail-loud)")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, source=False, rfr=False):
        p.add_argument("--lake-dir", required=True)
        if source:
            p.add_argument("--source-dir", required=True)
            p.add_argument("--force", action="store_true")
        if rfr:
            p.add_argument("--rfr-detail", required=True,
                           help="pipe-delimited RfR detail lookup table")
            p.add_argument("--results-years", default=None,
                           help="comma list scoping the results join (spill control)")
            p.add_argument("--rfr-group", required=True,
                           help="pipe-delimited test-item group lookup table")
            p.add_argument("--test-class", default="4")

    p = sub.add_parser("ingest-results"); common(p, source=True)
    p.set_defaults(func=cmd_ingest_results)
    p = sub.add_parser("ingest-items"); common(p, source=True, rfr=True)
    p.set_defaults(func=cmd_ingest_items)
    p = sub.add_parser("build-cycles"); common(p)
    p.add_argument("--gap-days", type=int, default=lake_cycles.DEFAULT_CYCLE_GAP_DAYS)
    p.set_defaults(func=cmd_build_cycles)
    p = sub.add_parser("check"); common(p)
    p.add_argument("--gate", choices=["continuity", "results", "items", "all"], default="all")
    p.set_defaults(func=cmd_check)
    p = sub.add_parser("all"); common(p, source=True, rfr=True)
    p.add_argument("--gap-days", type=int, default=lake_cycles.DEFAULT_CYCLE_GAP_DAYS)
    p.set_defaults(func=cmd_all)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
