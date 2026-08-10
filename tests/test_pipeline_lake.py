"""Lake pipeline tests: schema detection, normalization twins, cycle
reconstruction (python rule-of-record vs DuckDB SQL equivalence), ingest
end-to-end on fixture files, manifest idempotency, and the continuity gate
detecting a per-file vehicle_id reset (riskiest-assumption tripwire).

Requires duckdb (requirements-train.txt; installed by the CI test job).
"""
import os
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

duckdb = pytest.importorskip("duckdb")

from pipeline.lake import checks as lake_checks  # noqa: E402
from pipeline.lake import normalize, schemas  # noqa: E402
from pipeline.lake.cycles import assign_cycles, build_cycles_sql  # noqa: E402
from pipeline.lake.ingest_items import (  # noqa: E402
    ingest_items_file,
    register_rfr_category_table,
)
from pipeline.lake.ingest_results import ingest_results_file  # noqa: E402
from pipeline.lake.manifest import LakeManifest  # noqa: E402

RESULTS_HEADER = (
    "test_id|vehicle_id|test_date|test_class_id|test_type|test_result|"
    "test_mileage|postcode_area|make|model|colour|fuel_type|"
    "cylinder_capacity|first_use_date"
)
ITEMS_HEADER = "test_id|rfr_id|rfr_type_code|location_id|dangerous_mark"


def _results_row(test_id, vehicle_id, test_date, result, mileage="45000",
                 make="FORD", model="FOCUS", first_use="2012-06-15", klass="4"):
    return (f"{test_id}|{vehicle_id}|{test_date}|{klass}|NT|{result}|{mileage}"
            f"|AB|{make}|{model}|BLUE|PE|1600|{first_use}")


@pytest.fixture()
def con():
    connection = duckdb.connect()
    yield connection
    connection.close()


class TestSchemas:
    def test_detects_pipe_results(self):
        delim, fields = schemas.sniff_header(RESULTS_HEADER)
        assert delim == "|"
        assert schemas.detect_schema(fields, "results").name == "results_mts"

    def test_detects_comma_results(self):
        header = RESULTS_HEADER.replace("|", ",")
        delim, fields = schemas.sniff_header(header)
        assert delim == ","
        assert schemas.detect_schema(fields, "results", delim).name == "results_csv"

    def test_detects_items_with_and_without_dangerous_mark(self):
        _, fields = schemas.sniff_header(ITEMS_HEADER)
        assert schemas.detect_schema(fields, "items").name == "items_mts"
        _, legacy = schemas.sniff_header("test_id|rfr_id|rfr_type_code|location_id")
        assert schemas.detect_schema(legacy, "items").name == "items_legacy"

    def test_unknown_header_fails_loud_with_diff(self):
        _, fields = schemas.sniff_header(RESULTS_HEADER + "|surprise_column")
        with pytest.raises(schemas.SchemaDetectionError, match="surprise_column"):
            schemas.detect_schema(fields, "results")


class TestNormalize:
    def test_clean_id_tolerates_float_contamination_only(self):
        assert normalize.clean_id("12345") == 12345
        assert normalize.clean_id("12345.0") == 12345  # the check_match.py defect
        assert normalize.clean_id("  77 ") == 77
        assert normalize.clean_id("") is None
        with pytest.raises(ValueError):
            normalize.clean_id("12345.5")
        with pytest.raises(ValueError):
            normalize.clean_id("ABC123")

    def test_model_id_construction(self):
        assert normalize.build_model_id(" Ford ", "Focus  Zetec") == "FORD FOCUS ZETEC"
        assert normalize.build_model_id("FORD", None) == "FORD"
        assert normalize.build_model_id(None, None) is None

    def test_first_use_sanitization(self):
        good = date(2012, 6, 15)
        assert normalize.sanitize_first_use(good, date(2019, 3, 1)) == good
        # sentinel pre-1900 -> None
        assert normalize.sanitize_first_use(date(1899, 1, 1), date(2019, 3, 1)) is None
        # first use after test date (backfilled import) -> None
        assert normalize.sanitize_first_use(date(2020, 1, 1), date(2019, 3, 1)) is None

    def test_outcomes_and_era(self):
        assert normalize.canonical_outcome("F") == "FAIL"
        assert normalize.canonical_outcome("prs") == "PRS"
        assert normalize.canonical_outcome("???") == "UNKNOWN"
        assert normalize.taxonomy_era(date(2018, 5, 19)) == "pre_2018"
        assert normalize.taxonomy_era(date(2018, 5, 20)) == "post_2018"

    def test_sql_twins_match_python(self, con):
        rows = con.execute(
            f"SELECT {normalize.clean_id_expr(chr(39) + '123.0' + chr(39))},"
            f" {normalize.outcome_expr(chr(39) + 'f' + chr(39))}"
        ).fetchone()
        assert rows == (123, "FAIL")


class TestCycles:
    CASES = [
        # fail then retest inside gap -> one cycle, outcome of the retest
        dict(tests=[(1, date(2019, 3, 1), "FAIL"), (2, date(2019, 3, 8), "PASS")],
             expect_cycles=1, expect_outcomes=["PASS"]),
        # pass then pass -> two cycles regardless of gap
        dict(tests=[(1, date(2019, 3, 1), "PASS"), (2, date(2019, 3, 8), "PASS")],
             expect_cycles=2, expect_outcomes=["PASS", "PASS"]),
        # fail then retest OUTSIDE gap -> two cycles
        dict(tests=[(1, date(2019, 3, 1), "FAIL"), (2, date(2019, 6, 1), "PASS")],
             expect_cycles=2, expect_outcomes=["FAIL", "PASS"]),
        # PRS closes its cycle (it is a pass with rectified defects)
        dict(tests=[(1, date(2019, 3, 1), "PRS"), (2, date(2019, 3, 5), "PASS")],
             expect_cycles=2, expect_outcomes=["PRS", "PASS"]),
        # fail -> abandoned within gap stays in the cycle; outcome = last definitive
        dict(tests=[(1, date(2019, 3, 1), "FAIL"), (2, date(2019, 3, 10), "ABANDONED")],
             expect_cycles=1, expect_outcomes=["FAIL"]),
    ]

    @pytest.mark.parametrize("case", CASES)
    def test_python_rule(self, case):
        rows = [dict(test_id=t, vehicle_id=1, test_date=d, outcome=o)
                for t, d, o in case["tests"]]
        assigned = assign_cycles(rows)
        cycle_ids = sorted({r.cycle_id for r in assigned})
        assert len(cycle_ids) == case["expect_cycles"]
        outcomes = [next(r.cycle_outcome for r in assigned if r.cycle_id == c)
                    for c in cycle_ids]
        assert outcomes == case["expect_outcomes"]

    def test_prev_cycle_linkage(self):
        rows = [
            dict(test_id=1, vehicle_id=1, test_date=date(2019, 3, 1), outcome="FAIL"),
            dict(test_id=2, vehicle_id=1, test_date=date(2019, 3, 8), outcome="PASS"),
            dict(test_id=3, vehicle_id=1, test_date=date(2020, 3, 20), outcome="PASS"),
        ]
        assigned = {r.test_id: r for r in assign_cycles(rows)}
        assert assigned[3].prev_cycle_test_id == 2
        assert assigned[3].prev_cycle_outcome == "PASS"
        assert assigned[3].days_since_prev_cycle == (date(2020, 3, 20) - date(2019, 3, 8)).days

    def test_sql_twin_equivalence(self, con):
        rows = [
            (1, 1, date(2019, 3, 1), "FAIL"), (2, 1, date(2019, 3, 8), "PASS"),
            (3, 1, date(2020, 3, 20), "PASS"),
            (10, 2, date(2019, 5, 1), "PASS"), (11, 2, date(2020, 5, 3), "FAIL"),
            (12, 2, date(2020, 5, 20), "FAIL"), (13, 2, date(2020, 6, 1), "PASS"),
        ]
        con.execute("CREATE TABLE res (test_id BIGINT, vehicle_id BIGINT, "
                    "test_date DATE, outcome VARCHAR)")
        con.executemany("INSERT INTO res VALUES (?, ?, ?, ?)", rows)
        sql_rows = {
            r[0]: r for r in con.execute(build_cycles_sql("res")).fetchall()
        }
        py_rows = {r.test_id: r for r in assign_cycles(
            [dict(test_id=t, vehicle_id=v, test_date=d, outcome=o)
             for t, v, d, o in rows]
        )}
        assert set(sql_rows) == set(py_rows)
        for test_id, py in py_rows.items():
            sql = sql_rows[test_id]
            assert sql[2] == py.cycle_id, test_id
            assert bool(sql[3]) == py.is_cycle_first, test_id
            assert sql[4] == py.cycle_outcome, test_id
            assert sql[5] == py.prev_cycle_test_id, test_id
            assert sql[6] == py.prev_cycle_outcome, test_id
            assert sql[7] == py.days_since_prev_cycle, test_id


class TestIngestEndToEnd:
    def _write_sources(self, src_dir: Path):
        src_dir.mkdir(parents=True, exist_ok=True)
        results = src_dir / "test_result_2019.csv"
        results.write_text("\n".join([
            RESULTS_HEADER,
            _results_row(101, 9001, "2019-03-01", "F"),
            _results_row("102.0", 9001, "2019-03-08", "P", mileage="45010.0"),
            _results_row(201, 9002, "2017-06-01", "P", first_use="1888-01-01"),
            _results_row(301, 9003, "2019-07-01", "XYZ"),  # unknown outcome
        ]) + "\n")
        items = src_dir / "test_item_2019.csv"
        items.write_text("\n".join([
            ITEMS_HEADER,
            "101|1001|M|0|",        # post-2018 major on the FAIL test -> Brakes
            "101|1003|m|0|",        # minor: NOT a fail item
            "201|1001|F|0|",        # pre-2018 fail (test dated 2017)
            "101|9999|A|0|",        # advisory with unmapped rfr -> category NULL
            "101|1001|Z|0|",        # unregistered code -> NULL classification
        ]) + "\n")
        return results, items

    def test_ingest_classify_and_manifest(self, con, tmp_path):
        src = tmp_path / "src"
        lake = tmp_path / "lake"
        results_file, items_file = self._write_sources(src)
        manifest = LakeManifest.new(config={})

        rows = ingest_results_file(con, results_file, lake, manifest)
        assert rows == 4
        # idempotent: unchanged source skips
        assert ingest_results_file(con, results_file, lake, manifest) is None
        # changed source under the same name refuses without --force
        results_file.write_text(results_file.read_text() + "\n")
        with pytest.raises(RuntimeError, match="changed content"):
            ingest_results_file(con, results_file, lake, manifest)

        results_rel = (f"read_parquet('{(lake / 'results' / '**' / '*.parquet').as_posix()}',"
                       f" hive_partitioning=true)")
        got = {r[0]: r for r in con.execute(
            f"SELECT test_id, vehicle_id, outcome, test_mileage, first_use_date,"
            f" age_source, model_id, taxonomy_era FROM {results_rel}"
        ).fetchall()}
        assert got[102][1] == 9001 and got[102][2] == "PASS"     # '.0' cleaned
        assert got[102][3] == 45010                              # mileage cleaned too
        assert got[201][4] is None and got[201][5] == "missing"  # 1888 sentinel nulled
        assert got[101][6] == "FORD FOCUS"
        assert got[201][7] == "pre_2018" and got[101][7] == "post_2018"
        assert got[301][2] == "UNKNOWN"

        register_rfr_category_table(con, {"1001": "Brakes", "1003": "Road Wheels"})
        item_rows = ingest_items_file(con, items_file, lake, results_rel, manifest)
        assert item_rows == 5
        items_rel = (f"read_parquet('{(lake / 'items' / '**' / '*.parquet').as_posix()}',"
                     f" hive_partitioning=true)")
        classified = con.execute(f"""
            SELECT rfr_id, rfr_type_code, is_fail_item, component_category, taxonomy_era
            FROM {items_rel} ORDER BY rfr_id, rfr_type_code
        """).fetchall()
        by_key = {(r[0], r[1]): r for r in classified}
        assert by_key[("1001", "M")][2] is True
        assert by_key[("1001", "M")][3] == "Brakes"
        assert by_key[("1003", "m")][2] is False          # minor never fails
        assert by_key[("1003", "m")][3] == "Tyres"        # Road Wheels -> Tyres
        assert by_key[("1001", "F")][4] == "pre_2018"     # era from parent test date
        assert by_key[("9999", "A")][3] is None           # unmapped rfr -> NULL category
        assert by_key[("1001", "Z")][2] is None           # unregistered code -> NULL, not guess

        saved = manifest.save(lake)
        reloaded = LakeManifest.load(lake)
        assert saved.exists() and reloaded is not None
        assert {s.path for s in reloaded.sources} == {str(results_file), str(items_file)}
        assert all(len(s.sha256) == 64 for s in reloaded.sources)


class TestContinuityGate:
    def _seed(self, con, reset_ids: bool):
        con.execute("CREATE TABLE res (test_id BIGINT, vehicle_id BIGINT, "
                    "test_date DATE, first_use_date DATE, test_class_id VARCHAR)")
        rows = []
        test_id = 0
        for v in range(1, 401):
            for year in (2018, 2019, 2020):
                test_id += 1
                # a per-file reset re-issues ids per year: same numeric id
                # space every year means no vehicle appears to span years.
                vehicle_id = v + (year * 1_000_000 if reset_ids else 0)
                rows.append((test_id, vehicle_id, date(year, 6, 1),
                             date(2010, 1, 1), "4"))
        con.executemany("INSERT INTO res VALUES (?, ?, ?, ?, ?)", rows)

    def test_consistent_ids_pass(self, con):
        self._seed(con, reset_ids=False)
        result = lake_checks.check_vehicle_continuity(con, "res", sample_size=1000)
        assert result.passed, result.detail

    def test_per_year_reset_fails(self, con):
        self._seed(con, reset_ids=True)
        result = lake_checks.check_vehicle_continuity(con, "res", sample_size=1000)
        assert not result.passed, result.detail
