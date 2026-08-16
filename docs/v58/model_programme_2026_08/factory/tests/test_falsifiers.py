"""The 10 contract falsifiers. Each MUST fail if its defect is introduced.

Fixtures only -- these never touch the real lake.
"""
import os
import re
from datetime import date, timedelta

import duckdb
import pytest

from conftest import run_factory
from factory import atoms, blocks, emit, gates, packets, sampling, severity, sources
from factory.fixtures import FixtureLake, ItemRow, TestRow, write_p4_certification

RECIPE = emit.WindowRecipe("all", date(2005, 1, 1), date(2024, 1, 1))

#: Columns that are identifiers, not information: a within-day test_id swap
#: permutes them by construction, so D13 invariance is asserted on everything else.
IDENTIFIER_COLUMNS = {"tgt_id", "sample_bucket"}


def _feature_projection(row: dict) -> tuple:
    return tuple(sorted((k, str(v)) for k, v in row.items()
                        if k not in IDENTIFIER_COLUMNS))


# --- F1: as-of violation ----------------------------------------------------

def _two_year_vehicle(root: str, plant_future_item: bool) -> FixtureLake:
    lake = FixtureLake(root)
    lake.add_test(TestRow(test_id=1, vehicle_id=10, test_date=date(2019, 3, 1),
                          outcome="FAIL", test_mileage=50_000))
    lake.add_item(ItemRow(test_id=1, rfr_id="20001", rfr_type_code="F"))
    lake.add_test(TestRow(test_id=2, vehicle_id=10, test_date=date(2020, 3, 1),
                          outcome="PASS", test_mileage=60_000))
    lake.add_item(ItemRow(test_id=2, rfr_id="20003", rfr_type_code="A"))
    lake.add_test(TestRow(test_id=3, vehicle_id=10, test_date=date(2021, 3, 1),
                          outcome="FAIL", test_mileage=70_000))
    if plant_future_item:
        # A DANGEROUS item on the LAST test. Nothing about the 2019/2020 targets
        # may move because of it.
        lake.add_item(ItemRow(test_id=3, rfr_id="20002", rfr_type_code="F",
                              dangerous_mark="D"))
    return lake


def test_f1_as_of_violation_future_item_cannot_move_earlier_targets(tmp_path):
    _, base_rows, base_packets = run_factory(
        _two_year_vehicle(str(tmp_path / "base"), False), tmp_path / "b", [RECIPE])
    _, planted_rows, planted_packets = run_factory(
        _two_year_vehicle(str(tmp_path / "plant"), True), tmp_path / "p", [RECIPE])

    earlier = {2019, 2020}
    base_by_id = {r["tgt_id"]: r for r in base_rows if r["tgt_date"].year in earlier}
    plant_by_id = {r["tgt_id"]: r for r in planted_rows if r["tgt_date"].year in earlier}
    assert base_by_id and set(base_by_id) == set(plant_by_id)
    for tgt_id, row in base_by_id.items():
        assert _feature_projection(row) == _feature_projection(plant_by_id[tgt_id]), (
            f"target {tgt_id} moved when a FUTURE item was planted")

    base_pk = sorted(packets.d13_invariant_projection(p) for p in base_packets
                     if p["tgt_date"].year in earlier)
    plant_pk = sorted(packets.d13_invariant_projection(p) for p in planted_packets
                      if p["tgt_date"].year in earlier)
    assert base_pk == plant_pk, "packet contents moved under a future-dated item"


# --- F2: D13 permutation ----------------------------------------------------

def _same_day_pair(root: str, swap_ids: bool) -> FixtureLake:
    """Two NT+definitive tests on ONE day, plus a later target."""
    lake = FixtureLake(root)
    id_a, id_b = (101, 102) if not swap_ids else (102, 101)
    rows = [
        TestRow(test_id=id_a, vehicle_id=55, test_date=date(2019, 5, 2),
                outcome="FAIL", test_mileage=40_000),
        TestRow(test_id=id_b, vehicle_id=55, test_date=date(2019, 5, 2),
                test_type="RT", outcome="PASS", test_mileage=40_010),
    ]
    if swap_ids:
        rows = list(reversed(rows))          # physical order shuffled too
    for row in rows:
        lake.add_test(row)
    lake.add_item(ItemRow(test_id=id_a, rfr_id="20001", rfr_type_code="F"))
    lake.add_item(ItemRow(test_id=id_b, rfr_id="20003", rfr_type_code="A"))
    lake.add_test(TestRow(test_id=200, vehicle_id=55, test_date=date(2020, 5, 3),
                          outcome="PASS", test_mileage=50_000))
    return lake


def test_f2_d13_permutation_leaves_features_identical(tmp_path):
    _, rows_a, packets_a = run_factory(
        _same_day_pair(str(tmp_path / "a"), False), tmp_path / "ra", [RECIPE])
    _, rows_b, packets_b = run_factory(
        _same_day_pair(str(tmp_path / "b"), True), tmp_path / "rb", [RECIPE])

    assert sorted(_feature_projection(r) for r in rows_a) == \
        sorted(_feature_projection(r) for r in rows_b)
    assert sorted(packets.d13_invariant_projection(p) for p in packets_a) == \
        sorted(packets.d13_invariant_projection(p) for p in packets_b)

    later_a = [r for r in rows_a if r["tgt_date"].year == 2020][0]
    later_b = [r for r in rows_b if r["tgt_date"].year == 2020][0]
    assert later_a["tgt_id"] == later_b["tgt_id"] == 200
    assert _feature_projection(later_a) == _feature_projection(later_b)
    # the same-day pair is one AMBIGUOUS-free day cluster resolved in a later
    # stratum: it must count as exactly ONE prior test-day with two records
    assert later_a["b1_n_prior_test_days"] == 1
    assert later_a["b1_n_prior_tests"] == 2
    assert later_a["b5_n_prior_multi_test_days"] == 1


# --- F3: severity cross-tab -------------------------------------------------

CROSSTAB = [
    ("pre_2018", "F", None, "F", severity.SEVERITY_UNGRADED),
    ("pre_2018", "f", None, "F", severity.SEVERITY_UNGRADED),
    ("pre_2018", "P", None, "P", severity.SEVERITY_UNGRADED),
    ("pre_2018", "p", None, "P", severity.SEVERITY_UNGRADED),
    ("pre_2018", "A", None, "A", severity.SEVERITY_UNGRADED),
    ("pre_2018", "a", None, "A", severity.SEVERITY_UNGRADED),
    ("post_2018", "A", None, "A", severity.SEVERITY_ADVISORY),
    ("post_2018", "M", None, "M", severity.SEVERITY_MINOR),
    ("post_2018", "F", None, "F", severity.SEVERITY_MAJOR),
    ("post_2018", "P", None, "P", severity.SEVERITY_MAJOR),
    ("post_2018", "F", "D", "F", severity.SEVERITY_DANGEROUS),
    ("post_2018", "P", "D", "P", severity.SEVERITY_DANGEROUS),
    # AMENDED 2026-08-15 (severity fail-gating repair). This row previously expected
    # SEVERITY_DANGEROUS and so PINNED THE DEFECT: classify_severity tested
    # dangerous_mark BEFORE disposition, grading non-failing items dangerous. Under DVSA
    # rules a dangerous defect is a failure, so only F/P can be dangerous.
    ("post_2018", "M", "D", "M", severity.SEVERITY_MINOR),
    # COVERAGE GAP CLOSED: A+D was never in this crosstab, yet it is the case that
    # actually occurs -- 73,814 items post-2018 lake-wide (M+D occurs 0 times).
    ("post_2018", "A", "D", "A", severity.SEVERITY_ADVISORY),
]

RAISERS = [("post_2018", "m"), ("post_2018", "D"), ("post_2018", "X"),
           ("post_2018", ""), ("pre_2018", "M"), ("pre_2018", "m"),
           ("pre_2018", "Z")]


@pytest.mark.parametrize("era,code,mark,disposition,expected", CROSSTAB)
def test_f3_severity_crosstab_rule(era, code, mark, disposition, expected):
    assert severity.classify_disposition(code, era) == disposition
    assert severity.classify_severity(code, mark, era) == expected


@pytest.mark.parametrize("era,code", RAISERS)
def test_f3_unknown_codes_raise(era, code):
    with pytest.raises(severity.UnknownDispositionCode):
        severity.classify_disposition(code, era)


def test_f3_sql_twin_matches_the_rule():
    con = duckdb.connect()
    rows = [(era, code, mark) for era, code, mark, _d, _s in CROSSTAB]
    values = ", ".join(f"('{e}', '{c}', {'NULL' if m is None else repr(m)})"
                       for e, c, m in rows)
    disp = severity.disposition_expr("code", "era")
    sev = severity.severity_expr("code", "mark", "era")
    got = con.execute(
        f"SELECT era, code, mark, {disp}, {sev} FROM (VALUES {values}) t(era, code, mark)"
    ).fetchall()
    for (era, code, mark, sql_disp, sql_sev), (_e, _c, _m, want_d, want_s) in zip(got, CROSSTAB):
        assert sql_disp == want_d, (era, code, mark)
        assert sql_sev == want_s, (era, code, mark)


def test_f3_unknown_code_raises_at_build_time(tmp_path):
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2019, 1, 5)))
    lake.add_item(ItemRow(test_id=1, rfr_id="20001", rfr_type_code="m"))
    with pytest.raises(gates.GateFailure, match="rfr_type_code"):
        run_factory(lake, tmp_path / "run", [RECIPE])


# --- F4: emit-before-update -------------------------------------------------

def test_f4_emit_before_update(tmp_path):
    """Two consecutive test days: day-2 must reflect day-1 exactly, not day-2."""
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=7, test_date=date(2019, 4, 1),
                          outcome="FAIL", test_mileage=30_000))
    for rfr in ("20001", "20002", "20003"):
        lake.add_item(ItemRow(test_id=1, rfr_id=rfr, rfr_type_code="F"))
    lake.add_test(TestRow(test_id=2, vehicle_id=7, test_date=date(2019, 4, 2),
                          outcome="PASS", test_mileage=30_005))
    lake.add_item(ItemRow(test_id=2, rfr_id="20006", rfr_type_code="A"))

    _, rows, _ = run_factory(lake, tmp_path / "run", [RECIPE])
    day1 = [r for r in rows if r["tgt_id"] == 1][0]
    day2 = [r for r in rows if r["tgt_id"] == 2][0]

    assert day1["b1_n_prior_test_days"] == 0
    assert day1["b2_n_items_total"] == 0
    assert day1["b1_first_prior_date"] is None

    assert day2["b1_n_prior_test_days"] == 1
    assert day2["b1_last_prior_date"] == date(2019, 4, 1)
    assert day2["b2_n_items_total"] == 3, "day-2 saw its OWN items (leak)"
    assert day2["b3_n_fail_items_final"] == 3
    assert day2["b2_visibility_n_days"] == 0, "day-2's own advisory leaked backwards"
    assert day2["b5_days_since_prior_day"] == 1


# --- F5: censoring ----------------------------------------------------------

@pytest.mark.parametrize("first_use,expected_status,expected_censored", [
    # UPDATED for D-1. These cases are now stated against the BUILD's resolved
    # result floor, not the absolute 2005 bound. The fixture loads 2015+, so a
    # 2010 first-use is censored BY THIS BUILD -- which is precisely the
    # 2005-2015 cohort the old flag silently reported as `observed`. The
    # pre-D-1 expectations pinned the defect, so they had to move.
    (date(1998, 6, 1), "left_censored_2005", True),
    (date(2010, 6, 1), "left_censored_2005", True),
    (None, "first_use_missing", False),
])
def test_f5_censoring(tmp_path, first_use, expected_status, expected_censored):
    lake = FixtureLake(str(tmp_path / f"lake_{expected_status}_{first_use}"))
    lake.add_test(TestRow(test_id=1, vehicle_id=3, test_date=date(2015, 1, 1),
                          first_use_date=first_use))
    lake.add_test(TestRow(test_id=2, vehicle_id=3, test_date=date(2020, 1, 1),
                          first_use_date=first_use))
    _, rows, _ = run_factory(lake, tmp_path / f"run_{expected_status}_{first_use}",
                             [RECIPE])
    row = [r for r in rows if r["tgt_id"] == 2][0]

    assert row["b1_observable_years_status"] == expected_status
    assert row["b1_left_censor_flag"] is expected_censored
    assert row["b1_first_use_missing_flag"] is (first_use is None)
    # never zero-filled
    assert row["b1_observable_years"] > 0
    if first_use is None:
        assert row["b1_age_at_target_years"] is None
    else:
        assert row["b1_age_at_target_years"] == pytest.approx(
            (date(2020, 1, 1) - first_use).days / 365.25)
    # D-1: the window opens at the BUILD's resolved result floor, which the
    # fixture derives from its loaded years (2015+), not at the absolute 2005
    # bound. Under the old rule this vehicle claimed 15.0 observable years
    # against 5 loadable ones -- a ~3x inflated exposure denominator on exactly
    # the oldest vehicles.
    build_floor = date(2015, 1, 1)
    start = max(first_use or build_floor, build_floor)
    assert row["b1_observable_years"] == pytest.approx(
        (date(2020, 1, 1) - start).days / 365.25)
    assert row["b1_observable_years"] < 15.0, (
        "the corrected floor must not claim exposure the build cannot observe")


# --- F6: enrichment as-of ---------------------------------------------------

def test_f6_enrichment_stratum_is_as_of(tmp_path):
    """A vehicle that only becomes deep-history in 2023 is not enriched in 2016."""
    lake = FixtureLake(str(tmp_path / "lake"))
    test_id = 0
    for year in range(2015, 2024):
        test_id += 1
        lake.add_test(TestRow(test_id=test_id, vehicle_id=42,
                              test_date=date(year, 6, 1), outcome="PASS",
                              test_mileage=20_000 + 8_000 * (year - 2015)))
    _, rows, _ = run_factory(lake, tmp_path / "run", [RECIPE])
    by_year = {r["tgt_date"].year: r for r in rows}

    assert by_year[2016]["b1_n_prior_test_days"] == 1
    assert by_year[2016]["enrichment_stratum"] == sampling.NO_STRATUM
    assert by_year[2023]["b1_n_prior_test_days"] == 8
    assert by_year[2023]["enrichment_stratum"] == "deep_history"
    # the rule itself, exercised directly
    assert sampling.enrichment_stratum(0, None, 1) == sampling.NO_STRATUM
    assert sampling.enrichment_stratum(0, None, 6) == "deep_history"
    assert sampling.enrichment_stratum(1, None, 0) == "dangerous_prior"
    assert sampling.enrichment_stratum(0, 100, 0) == "recent_fail"
    assert sampling.enrichment_stratum(0, 900, 0) == sampling.NO_STRATUM


# --- F7: nesting ------------------------------------------------------------

def test_f7_rungs_nest_on_a_synthetic_population():
    con = duckdb.connect()
    vehicles = list(range(1, 20_001))
    us = sampling.unit_hash(con, vehicles, sampling.SAMPLE_SALT)
    ladder = sampling.ladder_from_fractions({
        "r250k": (0.05, 0.06), "r500k": (0.10, 0.12),
        "r1m": (0.20, 0.24), "r2m": (0.40, 0.48)})
    strata = ["none" if i % 3 else "deep_history" for i, _ in enumerate(vehicles)]

    selected = {}
    for rung in ladder.rungs:
        selected[rung.name] = {v for v, u, s in zip(vehicles, us, strata)
                               if rung.selects(u, s)}
    assert selected["r250k"] <= selected["r500k"] <= selected["r1m"] <= selected["r2m"]
    assert 0 < len(selected["r250k"]) < len(selected["r2m"])

    # a non-nesting ladder must be refused, not silently accepted
    with pytest.raises(sampling.RungError):
        sampling.RungLadder([sampling.Rung("big", 0.4), sampling.Rung("small", 0.1)])


def test_f7_salt_is_not_the_bare_hash():
    con = duckdb.connect()
    vehicles = list(range(1, 2001))
    salted = sampling.unit_hash(con, vehicles, sampling.SAMPLE_SALT)
    bare = [float(r[0]) for r in con.execute(
        "SELECT CAST(hash(v) AS DOUBLE)/18446744073709551616.0 FROM "
        f"(VALUES {', '.join(f'({v})' for v in vehicles)}) t(v)").fetchall()]
    overlap = sum(1 for a, b in zip(salted, bare) if (a < 0.1) and (b < 0.1))
    expected = 0.01 * len(vehicles)
    assert overlap < 3 * expected, "salted selection tracks the bare hash"
    assert sampling.unit_hash(con, vehicles, sampling.EVAL_SALT) != salted


# --- F8: leakage property test (honest denominator) -------------------------

def test_f8_n_prior_matches_an_independent_count(tmp_path):
    from factory.fixtures import default_population

    lake = default_population(str(tmp_path / "lake"), n_vehicles=60, seed=11,
                              start_year=2015, n_years=6)
    _, rows, _ = run_factory(lake, tmp_path / "run", [RECIPE])

    dates_by_vehicle = {}
    for test in lake.tests:
        dates_by_vehicle.setdefault(test.vehicle_id, set()).add(test.test_date)

    checked = 0
    for row in rows:
        truth = sum(1 for d in dates_by_vehicle[row["vehicle_id"]] if d < row["tgt_date"])
        if truth == 0:
            continue                      # honest denominator: rows WITH priors only
        checked += 1
        assert row["b1_n_prior_test_days"] == truth, (
            f"vehicle {row['vehicle_id']} target {row['tgt_date']}: "
            f"emitted {row['b1_n_prior_test_days']} != independent {truth}")
    assert checked > 100, "the property test must not be vacuous"


def test_f8_packets_are_strictly_prior(tmp_path):
    from factory.fixtures import default_population

    lake = default_population(str(tmp_path / "lake"), n_vehicles=40, seed=3,
                              start_year=2016, n_years=5)
    _, rows, packet_rows = run_factory(lake, tmp_path / "run", [RECIPE])
    assert packets.assert_strictly_prior(packet_rows) == 0
    n_targets = len({p["tgt_id"] for p in packet_rows})
    assert n_targets == len(rows), "every target appears exactly once in packets"


# --- F9: no-bare-test_id AST/source gate ------------------------------------

PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FORBIDDEN_SOURCE = {
    "order_by_test_id": re.compile(r"order\s+by[^\n]*test_id", re.I),
    "extremum_on_test_id": re.compile(r"\b(min|max|arg_min|arg_max|first|last)\s*\(\s*[\w.]*test_id", re.I),
    "python_sort_on_test_id": re.compile(r"\bsort(ed)?\s*\([^\n]*test_id", re.I),
    "lambda_key_test_id": re.compile(r"key\s*=\s*lambda[^\n]*test_id", re.I),
    "list_sort_test_id": re.compile(r"list_sort[^\n]*test_id", re.I),
    "row_number_test_id": re.compile(r"row_number\s*\(\s*\)[^\n]*test_id", re.I),
}

#: The ONLY sanctioned determinism tiebreak, frozen. Anything else fails.
D13_ALLOWLIST = {
    ("atoms.vehicle_day_atom_sql", "list_sort_test_id"),
}
ALLOW_MARKER = "D13-ALLOW"


def _package_sources():
    for name in sorted(os.listdir(PACKAGE_DIR)):
        if name.endswith(".py") and name != "__init__.py":
            yield name, open(os.path.join(PACKAGE_DIR, name), encoding="utf-8").read()


def _docstring_ranges(text: str):
    """Line spans occupied by module/class/function docstrings."""
    import ast

    spans = []
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            spans.append((first.lineno, first.end_lineno or first.lineno))
    return spans


def executable_lines(text: str):
    """(lineno, code) with COMMENTS and DOCSTRINGS removed.

    This is the AST half of falsifier 9: prose that merely MENTIONS test_id
    ordering must not trip the gate, and code that performs it must not be able
    to hide inside a docstring.
    """
    import io
    import tokenize

    spans = _docstring_ranges(text)
    lines = {}
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        kind, value, (srow, _scol), _end, _line = token
        if kind == tokenize.COMMENT:
            continue
        if kind == tokenize.STRING and any(lo <= srow <= hi for lo, hi in spans):
            continue
        if kind in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
            continue
        for offset, chunk in enumerate(value.splitlines() or [value]):
            lines.setdefault(srow + offset, []).append(chunk)
    return [(no, " ".join(parts)) for no, parts in sorted(lines.items())]


def test_f9_source_has_no_unmarked_test_id_ordering():
    violations = []
    marked = []
    for name, text in _package_sources():
        for lineno, line in executable_lines(text):
            for pattern_name, pattern in FORBIDDEN_SOURCE.items():
                if not pattern.search(line):
                    continue
                (marked if ALLOW_MARKER in line else violations).append(
                    (name, lineno, pattern_name, line.strip()[:110]))
    assert not violations, (
        "unmarked test_id ordering in the factory package (D13 violation): "
        f"{violations}")
    assert not marked, (
        "inline D13-ALLOW markers are not used; the sanctioned tiebreak is "
        f"asserted on the RENDERED SQL instead: {marked}")


def test_f9_gate_detects_a_planted_violation():
    """The gate itself must be falsifiable: plant the defect, see it caught."""
    planted = ('"""A docstring that mentions ORDER BY test_id harmlessly."""\n'
               'SQL = "SELECT * FROM t ORDER BY test_id"\n'
               'ROWS = sorted(rows, key=lambda r: r.test_id)\n')
    hits = set()
    for _lineno, line in executable_lines(planted):
        for pattern_name, pattern in FORBIDDEN_SOURCE.items():
            if pattern.search(line):
                hits.add(pattern_name)
    assert "order_by_test_id" in hits
    assert "lambda_key_test_id" in hits or "python_sort_on_test_id" in hits
    clean = '"""Mentions ORDER BY test_id in prose only."""\nX = 1\n'
    assert not any(p.search(line) for _n, line in executable_lines(clean)
                   for p in FORBIDDEN_SOURCE.values())


def test_f9_rendered_sql_has_only_the_frozen_tiebreak(tmp_path):
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2019, 1, 1)))
    inputs = lake.write()
    years = [2019]
    rendered = {
        "atoms.item_test_atom_sql": atoms.item_test_atom_sql(inputs, years, ("4",), True),
        "atoms.vehicle_day_atom_sql": atoms.vehicle_day_atom_sql(
            inputs, years, ("4",), True, 4, "rows"),
        "atoms.events_sql": atoms.events_sql(inputs, "2019-01-01", "2020-01-01",
                                             ("4",), 4, sampling.SAMPLE_SALT),
        "atoms.unknown_vocabulary_count_sql": atoms.unknown_vocabulary_count_sql(
            inputs, years, ("4",)),
        "atoms.unknown_disposition_sample_sql": atoms.unknown_disposition_sample_sql(
            inputs, years, ("4",)),
    }
    factory = emit.Factory(inputs, emit.BuildConfig(
        staging_dir=str(tmp_path / "s"), output_dir=str(tmp_path / "o")))
    rendered["emit.Factory._scan_sql"] = factory._scan_sql("all", 0)

    found = set()
    detail = []
    for source, sql in rendered.items():
        for line in sql.splitlines():
            for pattern_name, pattern in FORBIDDEN_SOURCE.items():
                if pattern.search(line):
                    found.add((source, pattern_name))
                    detail.append((source, pattern_name, line.strip()[:110]))
    assert found == D13_ALLOWLIST, (
        f"rendered SQL ordering changed. found={sorted(found)} "
        f"allow={sorted(D13_ALLOWLIST)} detail={detail}")


# --- F10: year-list fail-loud -----------------------------------------------

def test_f10_missing_year_raises_before_any_output(tmp_path):
    lake = FixtureLake(str(tmp_path / "lake"))
    for year in (2012, 2014):
        lake.add_test(TestRow(test_id=year, vehicle_id=1, test_date=date(year, 5, 1)))
    inputs = lake.write()
    out_dir = tmp_path / "out"
    config = emit.BuildConfig(staging_dir=str(tmp_path / "stage"),
                              output_dir=str(out_dir), n_buckets=2,
                              p4_certification_path=write_p4_certification(
                                  str(tmp_path / "cert.json")))
    factory = emit.Factory(inputs, config)
    factory.connect()
    recipe = emit.WindowRecipe("w", date(2012, 1, 1), date(2015, 1, 1))
    with pytest.raises(sources.MissingYears, match="2013"):
        factory.preflight([2012, 2013, 2014], [recipe])
    assert not os.path.exists(out_dir), "output directory created before the gate ran"
    assert not os.path.exists(tmp_path / "stage")


def test_f11_class3_priors_count_as_history_for_a_class4_target(tmp_path):
    """Owner ruling 2026-08-12: TARGETS are classes 3&4; HISTORY IS UNFILTERED.

    A class-4 target vehicle whose priors include class-3 (and class-7) tests
    must count those priors in B1 depth and carry them in packets. Their defect
    items must be counted too -- resolved to a category where the class-4
    catalogue applies, and to a catalogue miss where it does not. Never dropped.
    """
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=77, test_date=date(2018, 6, 1),
                          test_class_id="3", outcome="FAIL", test_mileage=20_000))
    lake.add_item(ItemRow(test_id=1, rfr_id="20001", rfr_type_code="F"))
    lake.add_test(TestRow(test_id=2, vehicle_id=77, test_date=date(2019, 6, 1),
                          test_class_id="7", outcome="PASS", test_mileage=30_000))
    lake.add_item(ItemRow(test_id=2, rfr_id="20003", rfr_type_code="A"))
    lake.add_test(TestRow(test_id=3, vehicle_id=77, test_date=date(2020, 6, 1),
                          test_class_id="4", outcome="PASS", test_mileage=40_000))
    lake.add_test(TestRow(test_id=4, vehicle_id=77, test_date=date(2021, 6, 1),
                          test_class_id="4", outcome="FAIL", test_mileage=50_000))

    _, rows, packet_rows = run_factory(lake, tmp_path / "run", [RECIPE])
    by_id = {r["tgt_id"]: r for r in rows}

    # class-3 and class-7 rows are TARGETS only if their class is in the target
    # set: 3 is, 7 is not.
    assert set(by_id) == {1, 3, 4}, "targets must be classes 3&4 only"

    final = by_id[4]
    assert final["b1_n_prior_test_days"] == 3, "class-3/7 priors dropped from depth"
    assert final["b1_n_prior_tests"] == 3
    assert final["b1_first_prior_date"] == date(2018, 6, 1)
    assert final["b2_brakes_n_days"] == 1, "class-3 prior's items dropped"
    assert final["b2_tyres_n_days"] == 1, "class-7 prior's items dropped"
    assert final["b2_n_items_total"] == 2
    assert final["b3_n_fail_items_final"] == 1
    assert final["b4_mileage_band"] == "30k-60k"

    priors = {p["p_test_id"] for p in packet_rows if p["tgt_id"] == 4}
    assert priors == {1, 2, 3}, "packets must carry the out-of-class priors"

    # and the class-4-only target at 2020 still sees both out-of-class priors
    assert by_id[3]["b1_n_prior_test_days"] == 2


def test_f11_history_filter_knob_is_available_but_off_by_default(tmp_path):
    """The ruling is a DEFAULT, not a removal: an explicit filter still works."""
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=88, test_date=date(2019, 6, 1),
                          test_class_id="7", outcome="PASS"))
    lake.add_test(TestRow(test_id=2, vehicle_id=88, test_date=date(2020, 6, 1),
                          test_class_id="4", outcome="PASS"))
    _, unfiltered, _ = run_factory(lake, tmp_path / "a", [RECIPE])
    _, filtered, _ = run_factory(lake, tmp_path / "b", [RECIPE],
                                 history_classes=("4",))
    assert emit.BuildConfig(staging_dir="x", output_dir="y").history_classes is None
    assert emit.BuildConfig(staging_dir="x", output_dir="y").target_classes == ("3", "4")
    assert [r for r in unfiltered if r["tgt_id"] == 2][0]["b1_n_prior_test_days"] == 1
    assert [r for r in filtered if r["tgt_id"] == 2][0]["b1_n_prior_test_days"] == 0


def _capped_depth_vehicle(root: str, swap_same_day_ids: bool = False) -> FixtureLake:
    """Priors at 1y / 3y / 6y before a 2022-06-01 target."""
    lake = FixtureLake(root)
    target = date(2022, 6, 1)
    offsets = {365: 11, 1096: 12, 2192: 13}     # 1y, 3y, 6y
    for days, test_id in offsets.items():
        day = target - timedelta(days=days)
        lake.add_test(TestRow(test_id=test_id, vehicle_id=99, test_date=day,
                              outcome="FAIL" if days == 1096 else "PASS",
                              test_mileage=20_000 + days * 10))
        lake.add_item(ItemRow(test_id=test_id, rfr_id="20001", rfr_type_code="F"))
    # a same-day co-test on the 1y prior, so D13 permutation has something to bite
    a, b = (14, 15) if not swap_same_day_ids else (15, 14)
    lake.add_test(TestRow(test_id=a, vehicle_id=99,
                          test_date=target - timedelta(days=365),
                          test_type="RT", outcome="PASS", test_mileage=24_000))
    lake.add_test(TestRow(test_id=b, vehicle_id=99,
                          test_date=target - timedelta(days=365),
                          test_type="RT", outcome="PASS", test_mileage=24_001))
    lake.add_test(TestRow(test_id=100, vehicle_id=99, test_date=target,
                          outcome="PASS", test_mileage=90_000))
    return lake


def test_f12_depth_caps_use_a_trailing_window(tmp_path):
    """Owner ruling 2026-08-12 (schedule risk H3): trailing-cap variants are
    computed in the SAME scan. Priors at 1y/3y/6y -> cap2y sees the 1y prior
    only, cap5y sees 1y+3y, uncapped sees all three."""
    _, rows, _ = run_factory(_capped_depth_vehicle(str(tmp_path / "lake")),
                             tmp_path / "run", [RECIPE])
    row = [r for r in rows if r["tgt_id"] == 100][0]

    assert row["b1_n_prior_test_days"] == 3
    assert row["b1_n_prior_test_days_cap2y"] == 1
    assert row["b1_n_prior_test_days_cap5y"] == 2

    # the 1y day carries three records (1 NT + 2 RT); initials count records
    assert row["b1_n_prior_initials"] == 3
    assert row["b1_n_prior_initials_cap2y"] == 1
    assert row["b1_n_prior_initials_cap5y"] == 2

    assert row["b1_history_years"] == pytest.approx(2192 / 365.25)
    assert row["b1_history_years_cap5y"] == pytest.approx(1096 / 365.25)
    assert row["b1_history_years_cap2y"] == pytest.approx(365 / 365.25), \
        "capped span must be measured to the earliest IN-WINDOW prior, not min(span, cap)"

    # B2 per-category counts follow the same window
    assert row["b2_brakes_n_days"] == 3
    assert row["b2_brakes_n_days_cap2y"] == 1
    assert row["b2_brakes_n_days_cap5y"] == 2
    assert row["b2_tyres_n_days_cap5y"] == 0

    # a vehicle with no prior inside the window reports NULL span, not zero
    assert row["b1_history_years_cap2y"] is not None
    first = [r for r in rows if r["tgt_id"] == 13][0]
    assert first["b1_n_prior_test_days_cap2y"] == 0
    assert first["b1_history_years_cap2y"] is None


def test_f12_capped_columns_are_d13_invariant(tmp_path):
    """D13 permutation must hold for the capped columns too."""
    _, rows_a, _ = run_factory(_capped_depth_vehicle(str(tmp_path / "a"), False),
                               tmp_path / "ra", [RECIPE])
    _, rows_b, _ = run_factory(_capped_depth_vehicle(str(tmp_path / "b"), True),
                               tmp_path / "rb", [RECIPE])
    capped = [c for c in blocks.COLUMN_NAMES if c.endswith(("_cap2y", "_cap5y"))]
    # Derived from the registry, not a literal: the guard's job is to prove the
    # compared projection is non-empty and covers every capped column, and a
    # hardcoded census makes adding one look like an invariance failure.
    registry_capped = [c.name for c in blocks.ALL_COLUMNS
                       if c.name.endswith(("_cap2y", "_cap5y"))]
    assert capped and capped == registry_capped, (
        "the capped family must be in the compared projection")
    a = {r["tgt_id"]: r for r in rows_a}[100]
    b = {r["tgt_id"]: r for r in rows_b}[100]
    assert [a[c] for c in capped] == [b[c] for c in capped]
    assert sorted(_feature_projection(r) for r in rows_a) == \
        sorted(_feature_projection(r) for r in rows_b)


def test_f10_present_years_pass(tmp_path):
    lake = FixtureLake(str(tmp_path / "lake"))
    for year in (2012, 2013, 2014):
        lake.add_test(TestRow(test_id=year, vehicle_id=1, test_date=date(year, 5, 1)))
    _, rows, _ = run_factory(lake, tmp_path / "run",
                             [emit.WindowRecipe("w", date(2012, 1, 1), date(2015, 1, 1))],
                             years=[2012, 2013, 2014])
    assert len(rows) == 3
