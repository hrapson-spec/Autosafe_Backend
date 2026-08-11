#!/usr/bin/env python3
"""Aggregate regeneration orchestrator (workstation; Phase 2 of v58).

Builds the comparison artifact from the lake, runs the enforced audit,
gates against the OLD artifact's vocabulary/match-rate, and exports the
canonical prod_data_clean.csv.gz + provenance sidecar.

    python -m pipeline.run_aggregates \
        --lake-dir ~/autosafe_lake \
        --coverage-start 2021-01-01 --coverage-end 2025-12-31 \
        --old-artifact prod_data_clean.csv.gz \
        --expect-rate 0.269139638817903 --reconciliation-tolerance 0.02 \
        --out /tmp/prod_data_clean.csv.gz

Reconciliation discipline (decision D7): FIRST run with a coverage window
equivalent to the old artifact's period and the old rate as --expect-rate;
only after that reconciles do you build the real 5-year window. The
exported artifact + report_contract constants + public copy + fixtures
must land in ONE commit (see the approved plan's P2 atomic-landing list);
this tool prints the constants block to paste.

Exit is non-zero on any gate failure. Requires requirements-train.txt.
"""
import argparse
import logging
import sys
from datetime import date
from pathlib import Path
from typing import List, Optional

from pipeline.aggregates.audit_aggregates import run_audit
from pipeline.aggregates.build_aggregates import (
    AggregateConfig,
    build_segment_counts,
    compute_aggregate_frame,
)
from pipeline.aggregates.export_prod_artifact import (
    export_artifact,
    report_contract_constants,
)
from pipeline.aggregates.match_rate_check import run_match_rate_checks
from pipeline.lake.manifest import MANIFEST_NAME, sha256_file

logger = logging.getLogger("pipeline.run_aggregates")


def _relation(lake_dir: Path, dataset: str) -> str:
    glob = (Path(lake_dir) / dataset / "**" / "*.parquet").as_posix()
    return f"read_parquet('{glob}', hive_partitioning=true)"


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lake-dir", required=True)
    parser.add_argument("--coverage-start", required=True, type=date.fromisoformat)
    parser.add_argument("--coverage-end", required=True, type=date.fromisoformat)
    parser.add_argument("--out", required=True)
    parser.add_argument("--old-artifact", default=None,
                        help="current prod_data_clean.csv.gz for the match-rate gate")
    parser.add_argument("--expect-rate", type=float, default=None,
                        help="expected dataset-wide failure rate (reconciliation gate)")
    parser.add_argument("--reconciliation-tolerance", type=float, default=0.02)
    parser.add_argument("--max-brier", type=float, default=0.01)
    parser.add_argument("--memory-limit", default="8GB")
    args = parser.parse_args(argv)

    import duckdb  # lazy: requirements-train only

    con = duckdb.connect()
    con.execute(f"SET memory_limit='{args.memory_limit}'")
    lake_dir = Path(args.lake_dir)
    config = AggregateConfig(coverage_start=args.coverage_start,
                             coverage_end=args.coverage_end)

    logger.info("building segment counts (%s -> %s)...",
                args.coverage_start, args.coverage_end)
    seg = build_segment_counts(
        con,
        _relation(lake_dir, "results"),
        _relation(lake_dir, "cycles"),
        _relation(lake_dir, "items"),
        config,
    )
    logger.info("segments: %s rows; dropped overlong model_ids: %s",
                len(seg), config.dropped_long_model_ids_logged)
    frame = compute_aggregate_frame(seg, config)

    failed = 0
    for check in run_audit(frame, expected_rate=args.expect_rate,
                           reconciliation_tolerance=args.reconciliation_tolerance,
                           max_brier=args.max_brier):
        print(f"{'PASS' if check.passed else 'FAIL'} [{check.name}] {check.detail}")
        failed += 0 if check.passed else 1
    if failed:
        print(f"\n{failed} audit gate(s) FAILED -- artifact NOT exported", file=sys.stderr)
        return 1

    manifest_path = lake_dir / MANIFEST_NAME
    lake_sha = sha256_file(manifest_path) if manifest_path.exists() else None
    provenance = export_artifact(frame, Path(args.out), config,
                                 lake_manifest_sha256=lake_sha)
    print(f"\nexported {args.out} ({provenance['rows']} rows, "
          f"sha256 {provenance['artifact_sha256'][:12]}...)")

    if args.old_artifact:
        for result in run_match_rate_checks(Path(args.old_artifact), Path(args.out)):
            print(f"{'PASS' if result.passed else 'FAIL'} [{result.name}] {result.detail}")
            failed += 0 if result.passed else 1
        if failed:
            print("\nmatch-rate gate FAILED -- do not land this artifact", file=sys.stderr)
            return 1

    print("\n--- report_contract.py constants for the atomic commit ---")
    print(report_contract_constants(provenance))
    print("Remember: artifact + constants + public copy + fixtures + tests land "
          "in ONE commit; run scripts/claim_sweep.py before pushing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
