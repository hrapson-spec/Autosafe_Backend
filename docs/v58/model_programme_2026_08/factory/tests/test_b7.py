"""B7 falsifiers F13-F22 + the split physical/featureset invariants.

Fixtures only, per FACTORY_CONTRACT: the owner runs real builds. Every test
here is written to FAIL against a plausible wrong implementation, not to
confirm the one that exists.
"""
from datetime import date, timedelta

import pytest

from conftest import run_factory
from factory import blocks, day_outcomes as do, rates
from factory import state as fstate
from factory.fixtures import FixtureLake, ItemRow, TestRow
from factory import emit

# Ends before 2024-01-01: the emission gate refuses training windows reaching
# into the selection year or the sealed 2025-H2 confirmation slice, and these
# fixtures have no business anywhere near either.
RECIPE = emit.WindowRecipe("all", date(2005, 1, 1), date(2024, 1, 1))
TARGET = date(2022, 6, 1)


def _row(rows, tgt_id):
    return [r for r in rows if r["tgt_id"] == tgt_id][0]


def _vehicle(root, *, swap_ids=False, extra=None):
    """One prior FAIL day, one prior PASS day, then a target."""
    lake = FixtureLake(root)
    a, b = (2, 1) if swap_ids else (1, 2)
    lake.add_test(TestRow(test_id=a, vehicle_id=7, test_date=TARGET - timedelta(days=730),
                          outcome="FAIL"))
    lake.add_item(ItemRow(test_id=a, rfr_id="20001", rfr_type_code="F"))
    lake.add_test(TestRow(test_id=b, vehicle_id=7, test_date=TARGET - timedelta(days=365),
                          outcome="PASS"))
    if extra:
        extra(lake)
    lake.add_test(TestRow(test_id=90, vehicle_id=7, test_date=TARGET, outcome="PASS"))
    return lake


B7_COLUMNS = [c.name for c in blocks.ALL_COLUMNS if c.block in ("B7D", "B7R")]


# --- F13: as-of -------------------------------------------------------------

def test_f13_a_later_event_cannot_change_an_earlier_target(tmp_path):
    """Appending a future FAIL must not move any B7 value on an earlier target."""
    base = _vehicle(str(tmp_path / "a"))
    _, rows_a, _ = run_factory(base, tmp_path / "ra", [RECIPE])

    later = _vehicle(str(tmp_path / "b"))
    later.add_test(TestRow(test_id=99, vehicle_id=7,
                           test_date=TARGET + timedelta(days=200), outcome="FAIL"))
    later.add_item(ItemRow(test_id=99, rfr_id="20001", rfr_type_code="F"))
    _, rows_b, _ = run_factory(later, tmp_path / "rb", [RECIPE])

    a, b = _row(rows_a, 90), _row(rows_b, 90)
    drift = {c: (a[c], b[c]) for c in B7_COLUMNS if a[c] != b[c]}
    assert not drift, f"future event leaked backwards into: {drift}"


# --- F14: D13 same-day invariance -------------------------------------------

def test_f14_same_day_test_id_permutation_is_bit_identical(tmp_path):
    """Within-day test_id order must not reach any B7 value.

    Measured within-day id-order agreement with truth is 49.91% -- chance -- so
    any feature that moves under this permutation is reading noise.
    """
    def same_day_pair(lake):
        day = TARGET - timedelta(days=200)
        lake.add_test(TestRow(test_id=40, vehicle_id=7, test_date=day, outcome="FAIL"))
        lake.add_test(TestRow(test_id=41, vehicle_id=7, test_date=day, outcome="PASS"))

    def same_day_pair_swapped(lake):
        day = TARGET - timedelta(days=200)
        lake.add_test(TestRow(test_id=41, vehicle_id=7, test_date=day, outcome="FAIL"))
        lake.add_test(TestRow(test_id=40, vehicle_id=7, test_date=day, outcome="PASS"))

    _, rows_a, _ = run_factory(_vehicle(str(tmp_path / "a"), extra=same_day_pair),
                               tmp_path / "ra", [RECIPE])
    _, rows_b, _ = run_factory(_vehicle(str(tmp_path / "b"), extra=same_day_pair_swapped),
                               tmp_path / "rb", [RECIPE])
    a, b = _row(rows_a, 90), _row(rows_b, 90)
    drift = {c: (a[c], b[c]) for c in B7_COLUMNS if a[c] != b[c]}
    assert not drift, f"B7 depends on within-day id order: {drift}"


# --- F22: ambiguity is excluded from BOTH sides -----------------------------

def test_f22_ambiguous_day_leaves_both_numerator_and_denominator(tmp_path):
    """One FAIL, one PASS, one AMBIGUOUS prior day -> share is 1/2, never 1/3.

    Counting an unresolvable day in the denominator alone would assert "this
    day was not a failure" -- the unavailable-to-zero conflation. This is the
    single assertion the whole Lane D estimand rests on.
    """
    def ambiguous_day(lake):
        day = TARGET - timedelta(days=200)
        # same stratum (both NT) -> sequence unidentified -> AMBIGUOUS
        lake.add_test(TestRow(test_id=40, vehicle_id=7, test_date=day, outcome="FAIL"))
        lake.add_test(TestRow(test_id=41, vehicle_id=7, test_date=day, outcome="PASS"))

    _, rows, _ = run_factory(_vehicle(str(tmp_path / "l"), extra=ambiguous_day),
                             tmp_path / "r", [RECIPE])
    row = _row(rows, 90)

    assert row["b7d_n_prior_fail_days"] == 1
    assert row["b7d_n_prior_pass_days"] == 1
    assert row["b7d_n_prior_outcome_ambiguous_days"] == 1
    assert row["b7d_n_prior_outcome_observable_days"] == 2, (
        "the ambiguous day must not enter the denominator")
    assert row["b7d_outcome_history_status"] == "partial"

    prior = rates.PROVISIONAL_PRIORS.beta["fail_day_share"]
    assert row["b7d_fail_days_per_outcome_observable_day"] == pytest.approx(
        (1 + prior.a) / (2 + prior.a + prior.b)), "denominator is not n=2"


# --- F16: cold start --------------------------------------------------------

def test_f16_no_history_is_null_and_never_zero_burden(tmp_path):
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=3, test_date=TARGET, outcome="PASS"))
    _, rows, _ = run_factory(lake, tmp_path / "run", [RECIPE])
    row = _row(rows, 1)

    assert row["b7d_prev_day_outcome"] == do.NO_HISTORY
    assert row["b7r_prev_initial_outcome"] == do.NO_HISTORY
    assert row["b7d_outcome_history_status"] == "no_priors"
    assert row["b7d_days_since_fail_day"] is None
    assert row["b7d_last_fail_day_n_items"] is None, "no failure != a clean failure"
    assert row["b7d_fail_days_per_outcome_observable_day"] is None, (
        "a rate with no denominator must be NULL, not the prior mean")
    assert row["b7r_initial_fail_decay_num_hl1y"] is None
    assert row["b7r_initial_opportunity_decay_den_hl1y"] is None


# --- F17: a retest must not rewrite the initial presentation ----------------

def test_f17_retest_pass_does_not_undo_the_initial_failure(tmp_path):
    """An RT pass closes nothing in Lane R: the NT still FAILED, and the
    initial-failure streak survives. This is the whole reason Lane R exists."""
    def retest_next_day(lake):
        fail_day = TARGET - timedelta(days=400)
        lake.add_test(TestRow(test_id=50, vehicle_id=8, test_date=fail_day,
                              outcome="FAIL", test_type="NT"))
        lake.add_test(TestRow(test_id=51, vehicle_id=8, test_date=fail_day + timedelta(days=5),
                              outcome="PASS", test_type="RT"))

    lake = FixtureLake(str(tmp_path / "lake"))
    retest_next_day(lake)
    lake.add_test(TestRow(test_id=90, vehicle_id=8, test_date=TARGET, outcome="PASS"))
    _, rows, _ = run_factory(lake, tmp_path / "run", [RECIPE])
    row = _row(rows, 90)

    assert row["b7r_prev_initial_outcome"] == do.FAIL, (
        "the RT pass overwrote the initial presentation's outcome")
    assert row["b7r_n_prior_initial_fail_days"] == 1
    assert row["b7r_current_initial_fail_streak"] == 1, (
        "a retest pass broke an initial-failure streak")
    # The day-grain lane DOES see the retest -- that is the deployable/ceiling
    # gap, and it must be visible rather than smoothed away.
    assert row["b7d_n_prior_pass_days"] == 1


# --- F15: prior pinning -----------------------------------------------------

def test_f15_downstream_priors_must_match_training_exactly():
    train = rates.PriorSet(surface_rule=rates.PRIOR_SURFACE_RULE, surface_rows=100,
                           beta={"fail_day_share": rates.BetaPrior(1.0, 4.0)}, gamma={})
    same = rates.PriorSet(surface_rule=rates.PRIOR_SURFACE_RULE, surface_rows=100,
                          beta={"fail_day_share": rates.BetaPrior(1.0, 4.0)}, gamma={})
    rates.assert_priors_match(train, same)

    refit = rates.PriorSet(surface_rule=rates.PRIOR_SURFACE_RULE, surface_rows=100,
                           beta={"fail_day_share": rates.BetaPrior(1.1, 4.0)}, gamma={})
    with pytest.raises(AssertionError, match="drifted"):
        rates.assert_priors_match(train, refit)

    resized = rates.PriorSet(surface_rule=rates.PRIOR_SURFACE_RULE, surface_rows=101,
                             beta={"fail_day_share": rates.BetaPrior(1.0, 4.0)}, gamma={})
    with pytest.raises(AssertionError, match="surface size drift"):
        rates.assert_priors_match(train, resized)


def test_f15_placeholder_priors_cannot_reach_a_real_build():
    with pytest.raises(AssertionError, match="PROVISIONAL_PRIORS"):
        rates.assert_fitted(rates.PROVISIONAL_PRIORS)


def test_prior_surface_is_one_deterministic_row_per_vehicle():
    rows = [
        {"vehicle_id": 1, "tgt_date": date(2021, 5, 1), "tgt_id": 30},
        {"vehicle_id": 1, "tgt_date": date(2020, 5, 1), "tgt_id": 20},
        {"vehicle_id": 1, "tgt_date": date(2020, 5, 1), "tgt_id": 10},
        {"vehicle_id": 2, "tgt_date": date(2019, 1, 1), "tgt_id": 5},
    ]
    picked = rates.select_prior_surface(rows)
    assert [r["tgt_id"] for r in picked] == [10, 5], (
        "earliest date, lowest tgt_id as the deterministic tiebreak")
    assert rates.select_prior_surface(list(reversed(rows))) == picked, (
        "the prior surface must not depend on input order")


def test_rate_estimators_match_their_denominator_type():
    """A bounded share and a per-year rate are different estimands."""
    beta = rates.BetaPrior(a=1.0, b=4.0)
    gamma = rates.GammaPrior(a=1.0, b=4.0)
    assert rates.smoothed_proportion(1, 2, beta) == pytest.approx(2.0 / 7.0)
    assert rates.smoothed_rate_per_year(1, 2.0, gamma) == pytest.approx(2.0 / 6.0)
    # A proportion is bounded by construction; a per-year rate is not.
    assert 0.0 <= rates.smoothed_proportion(2, 2, beta) <= 1.0
    assert rates.smoothed_proportion(0, 0, beta) is None
    assert rates.smoothed_rate_per_year(0, 0.0, gamma) is None
    with pytest.raises(ValueError):
        rates.BetaPrior(a=0.0, b=1.0)


# --- F19 / F20: history floors ----------------------------------------------

def test_f19_floors_are_per_channel_and_carry_provenance():
    derived = blocks.HistoryFloors.from_input_years([2015, 2016, 2023])
    assert derived.result == date(2015, 1, 1)
    assert derived.source == "build_year_list"
    assert blocks.DEFAULT_FLOORS.result == blocks.OBSERVABLE_FLOOR
    assert blocks.DEFAULT_FLOORS.source == "digital_records_2005"
    # The absolute bound still wins over a nonsensically early year list.
    assert blocks.HistoryFloors.from_input_years([1998]).result == blocks.OBSERVABLE_FLOOR
    assert "result" in derived.to_manifest() and "source" in derived.to_manifest()


def test_f20_denominator_correction_changes_only_b1_denominator_columns():
    """D-1, isolated: the same state under two floors differs ONLY in the
    denominator-derived columns -- never in depth, counts or recency."""
    state = fstate.AsOfState(vehicle_id=1)
    state.first_date = date(2016, 3, 1)
    state.n_days = 6
    state.day_dates = [date(2016 + i, 3, 1) for i in range(6)]
    state.initial_prefix = [0, 1, 2, 3, 4, 5, 6]
    first_use = date(2008, 6, 1)
    tgt = date(2022, 6, 1)

    legacy = blocks.emit_b1(state, tgt, first_use, blocks.DEFAULT_FLOORS)
    corrected = blocks.emit_b1(state, tgt, first_use,
                               blocks.HistoryFloors.from_input_years([2015]))

    changed = {k for k in legacy if legacy[k] != corrected[k]}
    allowed = {"b1_observable_years", "b1_observable_years_status",
               "b1_density_per_observable_year", "b1_opportunity_adjusted_density",
               "b1_history_coverage_grade", "b1_left_censor_flag"}
    assert changed <= allowed, f"floor change leaked into {changed - allowed}"
    assert changed, "the corrected floor must actually move the denominator"
    assert corrected["b1_observable_years"] < legacy["b1_observable_years"], (
        "a 2015-basis build cannot observe more years than a 2005-basis one")
    # The 2005-2015 cohort is the one the old flag silently missed.
    assert legacy["b1_left_censor_flag"] is False
    assert corrected["b1_left_censor_flag"] is True


# --- serve_class / screen_only enforcement ----------------------------------

def test_research_only_input_cannot_be_declared_deployable():
    with pytest.raises(ValueError, match="only go DOWN"):
        blocks.ColumnSpec("b7r_x", "B7R", "BIGINT", "Test.",
                          blocks.ERA_RESEARCH, blocks.SERVE_DEPLOYABLE)


def test_lane_r_is_entirely_research_only():
    assert blocks.BLOCK_COLUMNS["B7R"], "B7R must not be empty"
    leaked = [c.name for c in blocks.BLOCK_COLUMNS["B7R"]
              if c.serve_class != blocks.SERVE_RESEARCH]
    assert not leaked, f"Lane R columns claiming deployability: {leaked}"


def test_deployable_set_excludes_research_and_screen_only():
    names = {c.name for c in blocks.deployable_columns()}
    assert not any(n.startswith("b7r_") for n in names)
    assert "b6_location_map_status" not in names
    assert "b7d_n_prior_prs_days" not in names, (
        "PRS has no confirmed live representation (SERVE_VIEW invariant 3)")
    assert "b7d_prev_day_outcome" in names


def test_emission_is_independent_of_serve_eligibility(tmp_path):
    """A later 'PRS is serveable' ruling must be a METADATA change, never a
    packet rebuild trigger (owner ruling 8, 2026-08-14).

    That holds only while physical emission ignores serve_class entirely. If
    anyone ever makes emission conditional on eligibility, a reclassification
    starts requiring a rebuild -- and the rebuild cycle this programme is trying
    to avoid re-arms itself silently. Pinned here.
    """
    prs = "b7d_n_prior_prs_days"
    spec = {c.name: c for c in blocks.ALL_COLUMNS}[prs]

    # research-only TODAY ...
    assert spec.serve_class == blocks.SERVE_RESEARCH
    assert prs not in {c.name for c in blocks.deployable_columns()}
    # ... yet PHYSICALLY EMITTED regardless.
    assert prs in blocks.COLUMN_NAMES, (
        "a research-only column must still be emitted; eligibility is a "
        "featureset question, not an emission question")

    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=5, test_date=TARGET - timedelta(days=400),
                          outcome="PRS"))
    lake.add_test(TestRow(test_id=2, vehicle_id=5, test_date=TARGET, outcome="PASS"))
    _, rows, _ = run_factory(lake, tmp_path / "run", [RECIPE])
    assert prs in rows[0], "the column vanished from the emitted frame"

    # The whole emitted schema must be independent of the serve-class split:
    # every registered column is present whatever its eligibility.
    emitted = set(rows[0])
    for column in blocks.ALL_COLUMNS:
        assert column.name in emitted, (
            f"{column.name} ({column.serve_class}) is registered but not "
            f"emitted -- emission must not consult serve_class")


def test_adopted_cap_is_unchanged_and_headroom_is_reported():
    """Opening B7 must not raise the ADOPTED cap: research-only candidates
    never consumed it. The physical candidate cap is a separate budget."""
    assert blocks.ADOPTED_COLUMN_CAP == 150
    assert blocks.n_new_columns() <= blocks.PHYSICAL_CANDIDATE_CAP
    incumbent = [c for c in blocks.deployable_columns()
                 if not c.name.startswith("b7")]
    assert len(incumbent) == 123, (
        "deployable incumbent should be 143 B1-B6 minus 20 research-only")


def test_serve_class_enforcement_bites_at_the_runner():
    """A tag the runner does not enforce is documentation, not a contract."""
    from factory.runners import fit_contract as fc

    deployable = ["b7d_prev_day_outcome", "b1_n_prior_test_days"]
    research = deployable + ["b7r_initial_fail_share"]

    census = fc.serve_class_census(deployable)
    assert census["is_deployable"] and census["deployable"] == 2
    fc.assert_deployable(deployable)

    census = fc.serve_class_census(research)
    assert not census["is_deployable"]
    assert census["research_only"] == ["b7r_initial_fail_share"]
    with pytest.raises(AssertionError, match="cannot be served"):
        fc.assert_deployable(research)

    # B0-104 and generated interactions are outside this registry and must be
    # counted as unregistered rather than silently assumed servable.
    census = fc.serve_class_census(["n_prior_tests", "advisory_trend"])
    assert census["unregistered"] == 2 and census["deployable"] == 0


def test_featureset_hash_is_order_insensitive_and_content_addressed():
    from factory.runners import fit_contract as fc

    a = fc.featureset_hash(["b", "a", "c"])
    assert a == fc.featureset_hash(["c", "b", "a"]), "reordering is the same set"
    assert a == fc.featureset_hash(["a", "b", "c", "a"]), "duplicates are the same set"
    assert a != fc.featureset_hash(["a", "b"]), "a smaller set must hash differently"


def test_pinned_vocabulary_validates_membership_not_coverage():
    """A frame must not invent a level; it need not observe every level.

    Requiring coverage would make a legitimately homogeneous cohort
    un-scoreable, which is a worse failure than the one being prevented.
    """
    assert blocks.HISTORY_COVERAGE_GRADES == (
        "none", "left_censored", "partial", "full")
    # membership passes, including NULL
    assert blocks.assert_in_vocabulary("b1_history_coverage_grade", "full") == "full"
    assert blocks.assert_in_vocabulary("b1_history_coverage_grade", None) is None
    # a stray level is a contract change and must fail loudly
    with pytest.raises(ValueError, match="outside its pinned vocabulary"):
        blocks.assert_in_vocabulary("b1_history_coverage_grade", "mostly")
    # ordering is the contract, not an inference from observed data
    assert [blocks.vocabulary_ordinal("b1_history_coverage_grade", g)
            for g in blocks.HISTORY_COVERAGE_GRADES] == [0, 1, 2, 3]


def test_ordered_vocabulary_is_layout_stable_across_partial_observation():
    """Two frames observing DIFFERENT subsets of the vocabulary must still
    produce identical feature typing -- the failure that broke the fit was a
    rare level falling entirely into one side of a seed-dependent split."""
    from factory.runners import fit_contract as fc

    ordered = fc.blocks_ordered_vocabularies()
    assert "b1_history_coverage_grade" in ordered
    # nominal vocabularies stay categorical; only ordered ones become ordinal
    assert "b1_observable_years_status" not in ordered

    import numpy as np
    a = fc._ordinal_column("b1_history_coverage_grade",
                           np.array(["none", "partial"]))
    b = fc._ordinal_column("b1_history_coverage_grade",
                           np.array(["full", "partial", "left_censored"]))
    assert a.dtype == b.dtype == np.float64, "both frames type identically"
    assert list(a) == [0.0, 2.0] and list(b) == [3.0, 2.0, 1.0]
    na = fc._ordinal_column("b1_history_coverage_grade",
                            np.array([fc.MISSING_CATEGORY]))
    assert np.isnan(na[0]), "NULL stays NULL, never a confident level"


# --- structural invariants --------------------------------------------------

def test_no_b7_module_imports_cycles():
    """'No cycle resurrection' must hold architecturally, not just in prose.

    day_outcomes.py is the single quarantined boundary; nothing else in the B7
    path may reach into a namespace whose substrate has been retired.
    """
    import ast
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    offenders = []
    for name in ("rates.py", "blocks.py", "state.py"):
        tree = ast.parse(open(os.path.join(root, name), encoding="utf-8").read())
        for node in ast.walk(tree):
            mod = (getattr(node, "module", None) or "") if isinstance(
                node, ast.ImportFrom) else ""
            names = [a.name for a in getattr(node, "names", [])] if isinstance(
                node, (ast.Import, ast.ImportFrom)) else []
            if "cycles" in mod or any("cycles" in n for n in names):
                offenders.append(f"{name}:{node.lineno}")
    assert not offenders, (
        f"cycles imported outside factory/day_outcomes.py: {offenders}")


def test_every_rate_ships_its_companions():
    """No orphan ratios: a decay rate without its numerator AND denominator is
    a number the model cannot calibrate."""
    names = set(blocks.COLUMN_NAMES)
    for key in fstate.DECAY_HALF_LIVES:
        for part in ("num", "rate"):
            assert f"b7r_initial_fail_decay_{part}_{key}" in names
        assert f"b7r_initial_opportunity_decay_den_{key}" in names

    companions = {
        "b7d_fail_days_per_outcome_observable_day":
            ("b7d_n_prior_fail_days", "b7d_n_prior_outcome_observable_days",
             "b7d_outcome_history_status"),
        "b7d_severity_escalation_share":
            ("b7d_n_severity_transition_opportunities",
             "b7d_severity_transition_status"),
        "b7r_initial_fail_share":
            ("b7r_n_prior_initial_fail_days", "b7r_n_prior_initial_days",
             "b7r_initial_outcome_history_status"),
    }
    for rate, needed in companions.items():
        assert rate in names
        missing = [c for c in needed if c not in names]
        assert not missing, f"{rate} is an orphan ratio, missing {missing}"


def test_day_state_maps_non_definitive_days_to_unavailable():
    """A mixed non-definitive day carries NO outcome; it is UNAVAILABLE, not
    AMBIGUOUS. Collapsing them would put 'no test result' into the ambiguity
    bucket and misstate every exposure denominator."""
    assert do.day_state([]) == do.UNAVAILABLE
    assert do.day_state([{"ttype": "NT", "outcome": "ABANDONED"}]) == do.UNAVAILABLE
    assert do.day_state([{"ttype": "NT", "outcome": "ABANDONED"},
                         {"ttype": "NT", "outcome": "ABORTED"}]) == do.UNAVAILABLE
    assert do.day_state([{"ttype": "NT", "outcome": "FAIL"},
                         {"ttype": "NT", "outcome": "PASS"}]) == do.AMBIGUOUS
    assert do.day_state([{"ttype": "NT", "outcome": "FAIL"},
                         {"ttype": "RT", "outcome": "PASS"}]) == do.PASS
    assert not do.is_outcome_observable(do.AMBIGUOUS)
    assert not do.is_outcome_observable(do.UNAVAILABLE)


def test_severity_ordinal_is_null_not_zero_when_unobservable():
    assert do.severity_ordinal(None, None, None, None) is None, (
        "pre-2018 severity must be NULL, never a confident 0")
    assert do.severity_ordinal(0, 0, 0, 0) == 0
    assert do.severity_ordinal(0, 0, 0, 1) == 1
    assert do.severity_ordinal(0, 1, 0, 5) == 3
    assert do.severity_ordinal(1, 9, 9, 9) == 4
