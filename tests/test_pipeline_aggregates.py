"""Aggregate-regeneration tests (v58 Phase 2).

Pins the parts that must not drift:
- band SQL twins are equivalent to utils.get_age_band/get_mileage_band
  (the artifact's grain is defined by those functions, not by a second
  authority living in SQL);
- the recovered two-level EB shrinkage (K_GLOBAL=10 toward global,
  K_SEGMENT=5 toward make) reproduces the formula fitted from the
  checked-in artifact;
- artifact export formatting matches what build_db.py / claim_sweep.py
  already parse (13 columns in order, CSV quoting for comma-bearing
  model_ids, gzip);
- every audit gate actually fails on a bad frame (gates are enforced);
- the match-rate gate catches a vocabulary regression -- the silent
  failure mode a regenerated artifact would otherwise ship.
"""
import gzip
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

duckdb = pytest.importorskip("duckdb")

import utils  # noqa: E402
from pipeline.aggregates import audit_aggregates, match_rate_check, parity_pg_sqlite  # noqa: E402
from pipeline.aggregates.build_aggregates import (  # noqa: E402
    ARTIFACT_COLUMNS,
    AGE_BAND_SQL,
    MILEAGE_BAND_SQL,
    AggregateConfig,
    build_segment_counts,
    compute_aggregate_frame,
    coverage_totals,
)
from pipeline.aggregates.export_prod_artifact import (  # noqa: E402
    export_artifact,
    render_artifact_bytes,
)


@pytest.fixture()
def con():
    connection = duckdb.connect()
    yield connection
    connection.close()


@pytest.fixture()
def config():
    return AggregateConfig(coverage_start=date(2021, 1, 1), coverage_end=date(2025, 12, 31))


class TestBandSqlTwins:
    """The SQL bands must agree with utils.* for every boundary value."""

    AGES = [None, -1, 0, 2.9, 3, 5.9, 6, 10.9, 11, 15.9, 16, 40]
    MILEAGES = [None, -1, 0, 29999, 30000, 59999, 60000, 99999, 100000, 500000, 500001]

    def test_age_band_equivalence(self, con):
        for age in self.AGES:
            sql_value = con.execute(
                f"SELECT {AGE_BAND_SQL} FROM (SELECT ? AS age_at_test)", [age]
            ).fetchone()[0]
            assert sql_value == utils.get_age_band(age), f"age={age}"

    def test_mileage_band_equivalence(self, con):
        for miles in self.MILEAGES:
            sql_value = con.execute(
                f"SELECT {MILEAGE_BAND_SQL} FROM (SELECT ? AS test_mileage)", [miles]
            ).fetchone()[0]
            assert sql_value == utils.get_mileage_band(miles), f"miles={miles}"


class TestShrinkage:
    def _seg(self, rows):
        """rows: (model_id, age_band, mileage_band, tests, failures)."""
        frame = pd.DataFrame(rows, columns=[
            "model_id", "age_band", "mileage_band", "tests", "failures"])
        for column in ARTIFACT_COLUMNS[6:]:
            frame[f"fails_{column}"] = 0
        return frame

    def test_recovered_formula(self, config):
        # One make, two segments: shrinkage pulls each segment toward the
        # make rate with K_SEGMENT=5 -- the formula fitted from the
        # checked-in artifact (stdev 0.251).
        seg = self._seg([
            ("FORD FOCUS", "3-5", "0-30k", 100, 20),
            ("FORD FIESTA", "6-10", "30k-60k", 10, 5),
        ])
        frame = compute_aggregate_frame(seg, config)
        global_rate = 25 / 110
        make_totals = {"FORD": (110, 25)}
        tests, failures = make_totals["FORD"]
        make_rate = (failures + config.k_global * global_rate) / (tests + config.k_global)
        expected_focus = (20 + config.k_segment * make_rate) / (100 + config.k_segment)
        got = frame.loc[frame["model_id"] == "FORD FOCUS", "Failure_Risk"].iloc[0]
        assert got == pytest.approx(expected_focus, rel=1e-12)

    def test_sparse_cell_never_hard_zero(self, config):
        # The legacy audit's acceptance criterion: a zero-failure sparse
        # cell must still carry non-zero risk.
        seg = self._seg([
            ("FORD FOCUS", "3-5", "0-30k", 5, 0),
            ("FORD FIESTA", "6-10", "30k-60k", 5000, 1400),
        ])
        frame = compute_aggregate_frame(seg, config)
        sparse = frame[frame["model_id"] == "FORD FOCUS"]
        assert sparse["Failure_Risk"].iloc[0] > 0

    def test_high_volume_cell_tracks_observed(self, config):
        # With K=5 against 100k tests, shrinkage is numerically invisible.
        seg = self._seg([("FORD FOCUS", "3-5", "0-30k", 100_000, 27_000)])
        frame = compute_aggregate_frame(seg, config)
        assert frame["Failure_Risk"].iloc[0] == pytest.approx(0.27, abs=1e-4)

    def test_column_order_is_the_artifact_contract(self, config):
        seg = self._seg([("FORD FOCUS", "3-5", "0-30k", 10, 3)])
        frame = compute_aggregate_frame(seg, config)
        assert list(frame.columns) == ARTIFACT_COLUMNS

    def test_empty_input_raises(self, config):
        with pytest.raises(ValueError, match="no segment counts"):
            compute_aggregate_frame(self._seg([]), config)


class TestSegmentCountsFromLake:
    """End-to-end over a fixture lake: cycle-first filtering, class filter,
    coverage window, and per-category failing-test counts."""

    def _seed(self, con):
        con.execute("""
            CREATE TABLE results (test_id BIGINT, model_id VARCHAR, test_class_id VARCHAR,
                                  test_date DATE, outcome VARCHAR, age_at_test DOUBLE,
                                  test_mileage BIGINT)
        """)
        con.executemany("INSERT INTO results VALUES (?, ?, ?, ?, ?, ?, ?)", [
            # cycle-first FAIL with a brakes defect
            (1, "FORD FOCUS", "4", date(2022, 3, 1), "FAIL", 4.0, 45000),
            # the retest: same cycle, NOT cycle-first -> excluded from denominator
            (2, "FORD FOCUS", "4", date(2022, 3, 8), "PASS", 4.0, 45010),
            # cycle-first PASS in a different age band
            (3, "FORD FOCUS", "4", date(2023, 3, 1), "PASS", 7.0, 55000),
            # out of coverage window
            (4, "FORD FOCUS", "4", date(2019, 3, 1), "FAIL", 1.0, 5000),
            # wrong class
            (5, "HONDA CBR", "1", date(2022, 6, 1), "FAIL", 4.0, 20000),
        ])
        con.execute("CREATE TABLE cycles (test_id BIGINT, is_cycle_first BOOLEAN)")
        con.executemany("INSERT INTO cycles VALUES (?, ?)", [
            (1, True), (2, False), (3, True), (4, True), (5, True)])
        con.execute("""
            CREATE TABLE items (test_id BIGINT, is_fail_item BOOLEAN,
                                component_category VARCHAR)
        """)
        con.executemany("INSERT INTO items VALUES (?, ?, ?)", [
            (1, True, "Brakes"),
            (1, True, "Brakes"),      # duplicate category on one test counts once
            (1, False, "Tyres"),      # non-failing item ignored
            (5, True, "Brakes"),      # wrong class, excluded upstream
        ])

    def test_counts(self, con, config):
        self._seed(con)
        seg = build_segment_counts(con, "results", "cycles", "items", config)
        assert len(seg) == 2  # two (model, age, mileage) segments in window
        row = seg[(seg["age_band"] == "3-5") & (seg["mileage_band"] == "30k-60k")].iloc[0]
        assert row["model_id"] == "FORD FOCUS"
        assert row["tests"] == 1        # retest excluded
        assert row["failures"] == 1
        assert row["fails_Risk_Brakes"] == 1   # de-duplicated
        assert row["fails_Risk_Tyres"] == 0    # advisory/non-fail item ignored
        other = seg[seg["age_band"] == "3-5"].shape[0]
        assert other == 1
        assert seg["tests"].sum() == 2  # 2019 row and class-1 row both excluded


class TestArtifactExport:
    def _frame(self):
        rows = [
            ("FORD FOCUS", "3-5", "0-30k", 100, 27, 0.27) + (0.01,) * 7,
            # model_id containing a comma: must be CSV-quoted, like the
            # ~247 such rows in the current artifact
            ("MERCEDES-BENZ C220, CDI", "6-10", "30k-60k", 50, 10, 0.2) + (0.005,) * 7,
        ]
        return pd.DataFrame(rows, columns=ARTIFACT_COLUMNS)

    def test_render_quotes_and_orders(self):
        payload = render_artifact_bytes(self._frame()).decode()
        lines = payload.splitlines()
        assert lines[0] == ",".join(ARTIFACT_COLUMNS)
        assert '"MERCEDES-BENZ C220, CDI"' in lines[2]
        assert len(lines) == 3

    def test_export_roundtrips_through_csv_reader(self, tmp_path):
        frame = self._frame()
        out = tmp_path / "prod_data_clean.csv.gz"
        config = AggregateConfig(coverage_start=date(2021, 1, 1),
                                 coverage_end=date(2025, 12, 31))
        provenance = export_artifact(frame, out, config)

        rows = match_rate_check.load_artifact(out)
        assert len(rows) == 2
        assert rows[1]["model_id"] == "MERCEDES-BENZ C220, CDI"
        assert provenance["dataset_total_tests"] == 150
        assert provenance["dataset_total_failures"] == 37
        assert provenance["coverage_start"] == "2021-01-01"
        assert len(provenance["artifact_sha256"]) == 64

        sidecar = out.with_name("prod_data_provenance.json")
        assert sidecar.exists()

    def test_export_is_byte_stable(self, tmp_path):
        """Identical input -> identical bytes (gzip mtime pinned), so a
        no-op regeneration produces no diff."""
        frame = self._frame()
        config = AggregateConfig(coverage_start=date(2021, 1, 1),
                                 coverage_end=date(2025, 12, 31))
        first = tmp_path / "a.csv.gz"
        second = tmp_path / "b.csv.gz"
        export_artifact(frame, first, config)
        export_artifact(frame, second, config)
        assert first.read_bytes() == second.read_bytes()

    def test_gzip_is_readable_by_stdlib(self, tmp_path):
        out = tmp_path / "x.csv.gz"
        config = AggregateConfig(coverage_start=date(2021, 1, 1),
                                 coverage_end=date(2025, 12, 31))
        export_artifact(self._frame(), out, config)
        with gzip.open(out, "rt", encoding="utf-8") as f:
            assert f.readline().strip() == ",".join(ARTIFACT_COLUMNS)

    def test_coverage_totals(self):
        assert coverage_totals(self._frame()) == {
            "DATASET_TOTAL_TESTS": 150,
            "DATASET_TOTAL_FAILURES": 37,
            "rows": 2,
        }


class TestAuditGatesEnforce:
    def _good(self):
        return pd.DataFrame(
            [("FORD FOCUS", "3-5", "0-30k", 1000, 270, 0.27) + (0.01,) * 7],
            columns=ARTIFACT_COLUMNS,
        )

    def test_all_pass_on_good_frame(self):
        results = audit_aggregates.run_audit(self._good(), expected_rate=0.27)
        assert all(r.passed for r in results), [r for r in results if not r.passed]

    def test_schema_gate_catches_reordering(self):
        frame = self._good()[list(reversed(ARTIFACT_COLUMNS))]
        assert not audit_aggregates.check_schema(frame).passed

    def test_boundary_gate_catches_hard_zero(self):
        frame = self._good()
        frame.loc[0, "Risk_Brakes"] = 0.0
        assert not audit_aggregates.check_boundaries(frame).passed

    def test_boundary_gate_catches_certainty(self):
        frame = self._good()
        frame.loc[0, "Failure_Risk"] = 1.0
        assert not audit_aggregates.check_boundaries(frame).passed

    def test_sparse_gate_catches_unsmoothed_cell(self):
        frame = pd.DataFrame(
            [("FORD FOCUS", "3-5", "0-30k", 5, 0, 0.1) + (0.0,) * 7],
            columns=ARTIFACT_COLUMNS,
        )
        assert not audit_aggregates.check_sparse_smoothing(frame).passed

    def test_brier_gate_catches_miscalibration(self):
        frame = self._good()
        frame.loc[0, "Failure_Risk"] = 0.9   # observed is 0.27
        assert not audit_aggregates.check_weighted_brier(frame).passed

    def test_reconciliation_gate_is_the_d7_tripwire(self):
        frame = self._good()  # dataset rate 0.27
        assert audit_aggregates.check_reconciliation(frame, 0.269139638817903).passed
        # A cycle/PRS semantics error shifts the rate well beyond tolerance.
        assert not audit_aggregates.check_reconciliation(frame, 0.40).passed
        # No expectation supplied -> informational, never a false green.
        assert audit_aggregates.check_reconciliation(frame, None).passed


class TestMatchRateGate:
    OLD = [
        {"model_id": "FORD FOCUS", "age_band": "3-5", "mileage_band": "0-30k",
         "Total_Tests": "5000"},
        {"model_id": "VAUXHALL CORSA", "age_band": "6-10", "mileage_band": "30k-60k",
         "Total_Tests": "4000"},
    ]

    def test_vocabulary_loss_fails(self):
        new = [self.OLD[0]]
        result = match_rate_check.check_vocabulary_persistence(self.OLD, new)
        assert not result.passed
        assert "VAUXHALL CORSA" in result.detail

    def test_casing_change_is_a_loss(self):
        # The exact failure mode the gate exists for: a regenerated
        # vocabulary that "looks fine" but no longer matches lookups.
        new = [dict(self.OLD[0]), {**self.OLD[1], "model_id": "Vauxhall Corsa"}]
        assert not match_rate_check.check_vocabulary_persistence(self.OLD, new).passed

    def test_identical_vocabulary_passes(self):
        assert match_rate_check.check_vocabulary_persistence(self.OLD, self.OLD).passed

    def test_exact_band_persistence(self):
        assert match_rate_check.check_exact_band_persistence(self.OLD, self.OLD).passed
        dropped = [{**self.OLD[0], "mileage_band": "60k-100k"}, self.OLD[1]]
        assert not match_rate_check.check_exact_band_persistence(self.OLD, dropped).passed


class TestStoreParity:
    ROWS = [("FORD FOCUS", "3-5", "0-30k", 100, 27, 0.27, *([0.01] * 7))]

    def test_identical_row_sets_hash_equal(self):
        assert (parity_pg_sqlite.canonical_hash(self.ROWS)
                == parity_pg_sqlite.canonical_hash(list(self.ROWS)))

    def test_order_independent(self):
        rows = self.ROWS + [("VW GOLF", "6-10", "30k-60k", 50, 10, 0.2, *([0.02] * 7))]
        assert (parity_pg_sqlite.canonical_hash(rows)
                == parity_pg_sqlite.canonical_hash(list(reversed(rows))))

    def test_value_difference_detected(self):
        changed = [(*self.ROWS[0][:5], 0.28, *([0.01] * 7))]
        ok, detail = parity_pg_sqlite.compare("a", self.ROWS, "b", changed)
        assert not ok and "!=" in detail

    def test_artifact_rows_parse(self, tmp_path):
        frame = pd.DataFrame(
            [("FORD FOCUS", "3-5", "0-30k", 100, 27, 0.27) + (0.01,) * 7],
            columns=ARTIFACT_COLUMNS,
        )
        out = tmp_path / "p.csv.gz"
        export_artifact(frame, out, AggregateConfig(coverage_start=date(2021, 1, 1),
                                                    coverage_end=date(2025, 12, 31)))
        rows = parity_pg_sqlite.rows_from_artifact(out)
        assert rows[0][0] == "FORD FOCUS" and rows[0][3] == 100
