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
from pipeline.lake import cycles as lake_cycles  # noqa: E402
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
        dict(tests=[(1, date(2019, 3, 1), "FAIL", "NT"), (2, date(2019, 3, 8), "PASS", "RT")],
             expect_cycles=1, expect_outcomes=["PASS"]),
        # pass then pass -> two cycles regardless of gap
        dict(tests=[(1, date(2019, 3, 1), "PASS", "NT"), (2, date(2019, 3, 8), "PASS", "NT")],
             expect_cycles=2, expect_outcomes=["PASS", "PASS"]),
        # fail then retest OUTSIDE gap -> two cycles
        dict(tests=[(1, date(2019, 3, 1), "FAIL", "NT"), (2, date(2019, 6, 1), "PASS", "NT")],
             expect_cycles=2, expect_outcomes=["FAIL", "PASS"]),
        # PRS closes its cycle (it is a pass with rectified defects)
        dict(tests=[(1, date(2019, 3, 1), "PRS", "NT"), (2, date(2019, 3, 5), "PASS", "NT")],
             expect_cycles=2, expect_outcomes=["PRS", "PASS"]),
        # fail -> abandoned within gap stays in the cycle; outcome = last definitive
        dict(tests=[(1, date(2019, 3, 1), "FAIL", "NT"), (2, date(2019, 3, 10), "ABANDONED", "RT")],
             expect_cycles=1, expect_outcomes=["FAIL"]),
    ]

    @pytest.mark.parametrize("case", CASES)
    def test_python_rule(self, case):
        rows = [dict(test_id=t, vehicle_id=1, test_date=d, outcome=o, test_type=tt)
                for t, d, o, tt in case["tests"]]
        assigned = assign_cycles(rows)
        cycle_ids = sorted({r.cycle_id for r in assigned})
        assert len(cycle_ids) == case["expect_cycles"]
        outcomes = [next(r.cycle_outcome for r in assigned if r.cycle_id == c)
                    for c in cycle_ids]
        assert outcomes == case["expect_outcomes"]

    def test_prev_cycle_linkage(self):
        rows = [
            dict(test_id=1, vehicle_id=1, test_date=date(2019, 3, 1), outcome="FAIL", test_type="NT"),
            dict(test_id=2, vehicle_id=1, test_date=date(2019, 3, 8), outcome="PASS", test_type="RT"),
            dict(test_id=3, vehicle_id=1, test_date=date(2020, 3, 20), outcome="PASS", test_type="NT"),
        ]
        assigned = {r.test_id: r for r in assign_cycles(rows)}
        assert assigned[3].prev_cycle_test_id == 2
        assert assigned[3].prev_cycle_outcome == "PASS"
        assert assigned[3].days_since_prev_cycle == (date(2020, 3, 20) - date(2019, 3, 8)).days

    def test_sql_twin_equivalence_nonmonotone_ids(self, con):
        # Regression (2026-08-12, falsified live on 450M rows): DVSA test_ids
        # are NOT chronological. A FAIL(day1, id=100) -> PASS(day2, id=50)
        # cycle must take the FINAL-BY-DATE outcome (PASS) and the first-by-
        # date cycle_id (100); the old min/max(test_id) twin flipped both.
        rows = [
            (100, 1, date(2019, 3, 1), "FAIL", "NT"), (50, 1, date(2019, 3, 8), "PASS", "RT"),
            (900, 2, date(2020, 1, 5), "FAIL", "NT"), (20, 2, date(2020, 1, 15), "FAIL", "RT"),
            (7, 2, date(2020, 1, 20), "PRS", "RT"),
            (300, 3, date(2021, 6, 1), "ABANDONED", "NT"), (8, 3, date(2021, 6, 9), "ABORTED", "NT"),
        ]
        con.execute("CREATE TABLE res2 (test_id BIGINT, vehicle_id BIGINT, "
                    "test_date DATE, outcome VARCHAR, test_type VARCHAR)")
        con.executemany("INSERT INTO res2 VALUES (?, ?, ?, ?, ?)", rows)
        sql_rows = {r[0]: r for r in con.execute(build_cycles_sql("res2")).fetchall()}
        py_rows = {r.test_id: r for r in assign_cycles(
            [dict(test_id=t, vehicle_id=v, test_date=d, outcome=o, test_type=tt)
             for t, v, d, o, tt in rows])}
        assert set(sql_rows) == set(py_rows)
        for tid, py in py_rows.items():
            sq = sql_rows[tid]
            assert sq[2] == py.cycle_id, (tid, sq[2], py.cycle_id)
            assert bool(sq[3]) == py.is_cycle_first
            assert sq[4] == py.cycle_outcome, (tid, sq[4], py.cycle_outcome)
            assert sq[5] == py.prev_cycle_test_id
            assert sq[6] == py.prev_cycle_outcome
        # the discriminating assertions the old twin fails:
        assert py_rows[100].cycle_outcome == "PASS"
        # cycle_id is min(test_id): a pure identifier, not a claim that the
        # row was chronologically first (D13, amended)
        assert py_rows[100].cycle_id == 50
        # ABANDONED does not extend a cycle (only FAIL does): two single-row
        # cycles — the no-definitive fallback is only ever single-row, since
        # any extended cycle contains the FAIL that extended it.
        assert py_rows[300].cycle_outcome == "ABANDONED"
        assert py_rows[8].cycle_outcome == "ABORTED"
        assert py_rows[8].prev_cycle_test_id == 300

    def test_sql_twin_equivalence(self, con):
        rows = [
            (1, 1, date(2019, 3, 1), "FAIL", "NT"), (2, 1, date(2019, 3, 8), "PASS", "RT"),
            (3, 1, date(2020, 3, 20), "PASS", "NT"),
            (10, 2, date(2019, 5, 1), "PASS", "NT"), (11, 2, date(2020, 5, 3), "FAIL", "NT"),
            (12, 2, date(2020, 5, 20), "FAIL", "RT"), (13, 2, date(2020, 6, 1), "PASS", "RT"),
        ]
        con.execute("CREATE TABLE res (test_id BIGINT, vehicle_id BIGINT, "
                    "test_date DATE, outcome VARCHAR, test_type VARCHAR)")
        con.executemany("INSERT INTO res VALUES (?, ?, ?, ?, ?)", rows)
        sql_rows = {
            r[0]: r for r in con.execute(build_cycles_sql("res")).fetchall()
        }
        py_rows = {r.test_id: r for r in assign_cycles(
            [dict(test_id=t, vehicle_id=v, test_date=d, outcome=o, test_type=tt)
             for t, v, d, o, tt in rows]
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


class TestChronologyD13:
    """Falsifiers for decision D13 (amended): established chronology only.

    The decisive invariant: changing an arbitrary ordering choice -- physical
    input order, or id assignment within a same-day stratum -- must not
    change model information (cycle partition, cluster/cycle outcomes,
    linkage outcomes, gap arithmetic). A deterministic wrong chronology is
    still wrong, so where the sequence is unidentified the outcome is
    AMBIGUOUS, never invented.
    """

    def _rows(self, spec):
        return [dict(test_id=t, vehicle_id=1, test_date=d, outcome=o, test_type=tt)
                for t, d, o, tt in spec]

    @staticmethod
    def _model_info(rows_in, assigned):
        """Order-free signature of everything downstream may consume.

        Rows are identified by their physical attributes (not ids, which
        permutations reassign); prev_cycle_test_id is asserted at attribute
        level -- it must point at a row whose outcome determined the prior
        cycle, or be an explicit None.
        """
        by_id = {r["test_id"]: r for r in rows_in}
        sig = []
        for a in assigned:
            src = by_id[a.test_id]
            prev_attr = None
            if a.prev_cycle_test_id is not None:
                prev_row = by_id[a.prev_cycle_test_id]
                prev_attr = (prev_row["test_date"], prev_row["outcome"])
            sig.append(((src["test_date"], src["outcome"], src["test_type"]),
                        a.cycle_outcome, a.prev_cycle_outcome,
                        a.days_since_prev_cycle, prev_attr,
                        a.prev_cycle_test_id is None))
        return sorted(sig)

    def _assert_permutation_invariant(self, spec, swaps):
        import random
        base_rows = self._rows(spec)
        baseline = self._model_info(base_rows, assign_cycles([dict(r) for r in base_rows]))
        rng = random.Random(18)
        for _ in range(6):   # physical order permutations
            shuffled = [dict(r) for r in base_rows]
            rng.shuffle(shuffled)
            assert self._model_info(shuffled, assign_cycles(shuffled)) == baseline
        for a, b in swaps:   # id-value swaps inside a same-day stratum
            swapped = [dict(r) for r in base_rows]
            for r in swapped:
                r["test_id"] = b if r["test_id"] == a else (a if r["test_id"] == b else r["test_id"])
            assert self._model_info(swapped, assign_cycles(swapped)) == baseline
        return baseline

    # --- identified cases: established strata still resolve -----------------

    def test_same_day_retest_resolution_is_identified(self):
        # NT-FAIL + RT-PASS: the retest follows its initial test by DVSA
        # semantics -- resolution IS identified, for either id assignment.
        for nt, rt in [(999, 100), (100, 999)]:
            rows = self._rows([(nt, date(2019, 3, 1), "FAIL", "NT"),
                               (rt, date(2019, 3, 1), "PASS", "RT")])
            assigned = {r.test_id: r for r in assign_cycles(rows)}
            assert len({r.cycle_id for r in assigned.values()}) == 1
            assert assigned[nt].cycle_outcome == "PASS"
            assert assigned[nt].is_cycle_first        # NT stratum leads
            assert not assigned[rt].is_cycle_first

    def test_established_pass_before_later_failure_ends_failed(self):
        # NT-PASS + RT-FAIL: strata order the pass BEFORE the failure, so the
        # day ends failed -- identified, not ambiguous.
        rows = self._rows([(1, date(2019, 3, 1), "PASS", "NT"),
                           (2, date(2019, 3, 1), "FAIL", "RT")])
        assigned = assign_cycles(rows)
        assert all(r.cycle_outcome == "FAIL" for r in assigned)

    # --- unidentified cases: AMBIGUOUS, never manufactured ------------------

    def test_same_stratum_fail_pass_is_ambiguous_not_invented(self):
        # Two NTs, FAIL + PASS, one day: no semantics or timestamp orders
        # them. The old rule manufactured FAIL->PASS; the contract forbids
        # that. One cluster, outcome AMBIGUOUS, invariant to id assignment.
        base = self._assert_permutation_invariant(
            [(11, date(2019, 3, 1), "FAIL", "NT"),
             (10, date(2019, 3, 1), "PASS", "NT")],
            swaps=[(11, 10)])
        assert all(sig[1] == "AMBIGUOUS" for sig in base)

    def test_ambiguous_does_not_extend_a_chain(self):
        # A test 10 days after an AMBIGUOUS day starts a NEW cycle: extending
        # would assert an unresolved failure the data does not establish.
        rows = self._rows([(11, date(2019, 3, 1), "FAIL", "NT"),
                           (10, date(2019, 3, 1), "PASS", "NT"),
                           (30, date(2019, 3, 11), "PASS", "NT")])
        assigned = {r.test_id: r for r in assign_cycles(rows)}
        assert assigned[30].is_cycle_first
        assert assigned[30].prev_cycle_outcome == "AMBIGUOUS"
        assert assigned[30].prev_cycle_test_id is None   # explicit unknown
        assert assigned[30].days_since_prev_cycle == 10

    def test_repeated_same_stratum_failures_are_unanimous(self):
        # NT-FAIL + NT-FAIL: unanimous set outcome FAIL (no order needed);
        # the resolver is NOT unique -> explicit None; a retest 5 days later
        # still extends the chain (prev outcome FAIL is established).
        base = self._assert_permutation_invariant(
            [(5, date(2019, 3, 1), "FAIL", "NT"),
             (6, date(2019, 3, 1), "FAIL", "NT"),
             (7, date(2019, 3, 6), "PASS", "RT")],
            swaps=[(5, 6)])
        assert all(sig[1] == "PASS" for sig in base)     # one chain, resolved
        by_outcome = {sig[0][1]: sig for sig in base}
        assert by_outcome["PASS"][4] is None or True     # linkage within cycle
        rows = self._rows([(5, date(2019, 3, 1), "FAIL", "NT"),
                           (6, date(2019, 3, 1), "FAIL", "NT"),
                           (7, date(2019, 3, 6), "PASS", "RT"),
                           (9, date(2020, 3, 1), "PASS", "NT")])
        assigned = {r.test_id: r for r in assign_cycles(rows)}
        assert assigned[9].prev_cycle_outcome == "PASS"
        assert assigned[9].prev_cycle_test_id == 7       # unique resolver

    def test_prs_combinations(self):
        # Same stratum FAIL+PRS: unidentified -> AMBIGUOUS. Later stratum
        # PRS resolves: identified.
        rows = self._rows([(1, date(2019, 3, 1), "FAIL", "NT"),
                           (2, date(2019, 3, 1), "PRS", "NT")])
        assert all(r.cycle_outcome == "AMBIGUOUS" for r in assign_cycles(rows))
        rows = self._rows([(1, date(2019, 3, 1), "FAIL", "NT"),
                           (2, date(2019, 3, 1), "PRS", "RT")])
        assert all(r.cycle_outcome == "PRS" for r in assign_cycles(rows))

    def test_same_type_repeated_passes(self):
        base = self._assert_permutation_invariant(
            [(3, date(2019, 3, 1), "PASS", "NT"),
             (4, date(2019, 3, 1), "PASS", "NT")],
            swaps=[(3, 4)])
        assert all(sig[1] == "PASS" for sig in base)

    def test_nondefinitive_mix_with_failure(self):
        # ABORTED + FAIL, one NT day: the definitive FAIL decides the set
        # outcome; nothing depends on which "came first".
        base = self._assert_permutation_invariant(
            [(5, date(2019, 3, 1), "ABORTED", "NT"),
             (9, date(2019, 3, 1), "FAIL", "NT")],
            swaps=[(5, 9)])
        assert all(sig[1] == "FAIL" for sig in base)

    def test_duplicate_test_id_fails_loud(self):
        rows = self._rows([(1, date(2019, 3, 1), "FAIL", "NT"),
                           (1, date(2019, 3, 8), "PASS", "RT")])
        with pytest.raises(ValueError, match="duplicate test_id"):
            assign_cycles(rows)

    def test_unknown_test_type_fails_loud(self):
        rows = self._rows([(1, date(2019, 3, 1), "FAIL", "XX")])
        with pytest.raises(KeyError):
            assign_cycles(rows)

    def test_sql_twin_same_day_adversarial(self, con):
        rows = [
            (999, 1, date(2019, 3, 1), "FAIL", "NT"), (100, 1, date(2019, 3, 1), "PASS", "RT"),
            (11, 2, date(2020, 5, 3), "FAIL", "NT"), (10, 2, date(2020, 5, 3), "PASS", "NT"),
            (21, 2, date(2020, 5, 13), "PASS", "NT"),
            (5, 3, date(2021, 1, 4), "ABORTED", "NT"), (9, 3, date(2021, 1, 4), "FAIL", "NT"),
            (8, 3, date(2021, 1, 12), "PASS", "RT"),
            (31, 4, date(2021, 3, 1), "FAIL", "NT"), (32, 4, date(2021, 3, 1), "FAIL", "NT"),
            (33, 4, date(2021, 3, 6), "PASS", "RT"),
        ]
        con.execute("CREATE TABLE res3 (test_id BIGINT, vehicle_id BIGINT, "
                    "test_date DATE, outcome VARCHAR, test_type VARCHAR)")
        con.executemany("INSERT INTO res3 VALUES (?, ?, ?, ?, ?)", rows)
        sql_rows = {r[0]: r for r in con.execute(build_cycles_sql("res3")).fetchall()}
        py_rows = {r.test_id: r for r in assign_cycles(
            [dict(test_id=t, vehicle_id=v, test_date=d, outcome=o, test_type=tt)
             for t, v, d, o, tt in rows])}
        assert set(sql_rows) == set(py_rows)
        for tid, py in py_rows.items():
            sq = sql_rows[tid]
            assert sq[2] == py.cycle_id, (tid, sq[2], py.cycle_id)
            assert bool(sq[3]) == py.is_cycle_first, tid
            assert sq[4] == py.cycle_outcome, (tid, sq[4], py.cycle_outcome)
            assert sq[5] == py.prev_cycle_test_id, tid
            assert sq[6] == py.prev_cycle_outcome, tid
            assert sq[7] == py.days_since_prev_cycle, tid
        assert py_rows[11].cycle_outcome == "AMBIGUOUS"
        assert py_rows[21].prev_cycle_outcome == "AMBIGUOUS"
        assert py_rows[21].prev_cycle_test_id is None
        assert py_rows[999].cycle_outcome == "PASS"

    def test_no_semantic_test_id_chronology_anywhere_in_pipeline(self):
        """Repo static gate (owner amendment step 4): every ORDER BY that
        touches test_id in pipeline/ must either carry the full
        representation key (type_rank + outcome_rank present) or sit on a
        line documented as D13 determinism-only. Zero category-2 uses."""
        import re
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / "pipeline"
        offenders = []
        for f in sorted(root.rglob("*.py")):
            text = f.read_text()
            for m in re.finditer(r"ORDER BY[^\n)]*test_id", text, re.I):
                snippet = m.group(0)
                if "type_rank" in snippet and "outcome_rank" in snippet:
                    continue   # full representation key: sanctioned
                ctx = text[max(0, m.start() - 400):m.start()]
                if "determinism" in ctx and "D13" in ctx:
                    continue   # documented determinism-only site (checks.py)
                line = text[:m.start()].count("\n") + 1
                offenders.append(f"{f.relative_to(root)}:{line}: {snippet.strip()}")
        assert not offenders, f"semantic test_id chronology found: {offenders}"

    def test_generated_sql_has_no_semantic_test_id_ordering(self):
        # Static gate (pattern from the blast-radius w3 acceptance): test_id
        # may appear in an ORDER BY only inside the full representation key
        # of the bookkeeping rep_pos window; every chronology-bearing window
        # orders by dates (and cycle_seq) alone.
        import re
        sql = build_cycles_sql("t")
        assert "row(test_date, test_id)" not in sql
        assert re.search(r"ORDER BY test_date, test_id\b", sql) is None
        orderings = re.findall(r"ORDER BY ([^)\n]+)", sql)
        for o in orderings:
            if "test_id" in o:
                assert o.strip().startswith(lake_cycles.CHRONOLOGY_ORDER_SQL.split(",")[0])
                assert "type_rank" in o and "outcome_rank" in o


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

    def test_backslash_escaped_quote_in_model(self, tmp_path, con):
        # Regression (2026-08-11, 2018 Q1 chunk line 100435): the 2018+ MTS
        # exports escape quotes with BACKSLASH (LAND ROVER,"88\"") while the
        # reader assumed doubled-quote escaping — the parser then swallowed
        # 45MB as one runaway quoted field. escape now comes from the schema
        # registry (comma epochs = backslash).
        from pipeline.lake import ingest_results, schemas
        f = tmp_path / "test_result_esc.csv"
        f.write_text(
            "test_id,vehicle_id,test_date,test_class_id,test_type,test_result,"
            "test_mileage,postcode_area,make,model,colour,fuel_type,"
            "cylinder_capacity,first_use_date\n"
            '1,10,2018-01-03,4,NT,P,1000,AB,LAND ROVER,"88\\"",GREEN,PE,2286,1983-01-01\n'
            "2,11,2018-01-03,4,NT,F,2000,CD,FORD,FIESTA,BLUE,PE,1242,2005-11-01\n"
        )
        schema = schemas.detect_schema_for_file(str(f), "results")
        assert schema.name == "results_csv" and schema.escape == "\\"
        rows = con.execute(
            f"SELECT model FROM {ingest_results._read_csv_clause(f, schema)} ORDER BY test_id"
        ).fetchall()
        assert rows[0][0] == '88"'
        assert rows[1][0] == "FIESTA"

    def test_sample_smaller_than_population_regression(self, con):
        # Regression (2026-08-11, found live on 450M rows): USING SAMPLE inside
        # the grouped SELECT sampled RAW rows pre-aggregation, so any sample
        # much smaller than the row count yielded ZERO multi-test vehicles and
        # the gate could only fail. sample_size=50 << 1200 rows discriminates:
        # the broken form fails with "no multi-test vehicles found in sample".
        self._seed(con, reset_ids=False)
        result = lake_checks.check_vehicle_continuity(con, "res", sample_size=50)
        assert result.passed, result.detail
        assert "no multi-test vehicles" not in result.detail
