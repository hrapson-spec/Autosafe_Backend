"""INC-2026-08-13 remediation: builder/consumer capability + the three states.

Fixtures only. Every test here is a falsifier for one clause of
PREREG_CUBE_v2 §4/§5, and each names the mechanism it closes:

    M1  atoms.py LEFT JOIN answering the availability question
    M2  atoms.py coalesce(sum(x), 0)
    M3  state.py DayAtom.item(default=0)      <- the second, independent coalesce
    M4  atoms.py packet coalesces -> state.py prior_tests n_items `or 0`
    C   the missing builder/consumer declaration (the incident itself)
"""
import inspect
import json
import os
from datetime import date

import pytest

from conftest import full_ladder, make_config
from factory import atoms, blocks, capability, emit, gates
from factory import observability as obs
from factory import packets
from factory import state as fstate
from factory.fixtures import (FixtureLake, ItemRow, TestRow,
                              write_item_coverage_ledger)
from factory.runners import b0_module_runner as b0

RECIPE = emit.WindowRecipe("all", date(2005, 1, 1), date(2024, 1, 1))


# --- helpers ----------------------------------------------------------------

def build_lake(lake: FixtureLake, tmp_path, *, write_parquet: bool = False,
               years=None, **config_overrides):
    """Preflight + prepare + (scan | build). Returns (factory, frames, packets)."""
    inputs = lake.write()
    config = make_config(tmp_path, **config_overrides)
    factory = emit.Factory(inputs, config)
    factory.connect()
    years = years or sorted({t.test_date.year for t in lake.tests})
    preflight = factory.preflight(years, [RECIPE])
    prepare = factory.prepare(years, [RECIPE])
    if write_parquet:
        manifest = factory.build(years, [RECIPE], full_ladder(), preflight, prepare)
        return factory, manifest, None
    frames, packet_rows = [], []
    for row, prows in factory.scan(RECIPE):
        frames.append(row)
        packet_rows.extend(prows)
    return factory, frames, packet_rows


def two_day_vehicle(lake: FixtureLake, vehicle_id: int, prior_day: date,
                    target_day: date, *, prior_outcome: str = "PASS",
                    n_prior_items: int = 0, schema_epoch: str = "results_mts",
                    base_test_id: int = 0) -> int:
    """A vehicle with exactly one prior test-day and one target."""
    prior_id = base_test_id + 1
    lake.add_test(TestRow(test_id=prior_id, vehicle_id=vehicle_id,
                          test_date=prior_day, outcome=prior_outcome,
                          schema_epoch=schema_epoch))
    for k in range(n_prior_items):
        lake.add_item(ItemRow(test_id=prior_id, rfr_id="20001",
                              rfr_type_code=("F" if k == 0 else "A")))
    target_id = base_test_id + 2
    lake.add_test(TestRow(test_id=target_id, vehicle_id=vehicle_id,
                          test_date=target_day, outcome="PASS",
                          schema_epoch=schema_epoch))
    return target_id


# =============================================================================
# C -- the incident: builder/consumer capability
# =============================================================================

def test_incident_reproduces_on_the_old_consumer_expression():
    """The pre-fix consumer turned an UNKNOWN into 'no defects'. Verbatim."""
    old_expression = lambda raw: json.loads(raw) if raw else []   # noqa: E731
    # queue.txt:167 built with --defect-detail counts, so every defects_json
    # was NULL. queue.txt:168 then ran this line over 5,560,040 rows.
    assert old_expression(None) == [], (
        "this IS the defect: a NULL payload became an empty defect list, i.e. "
        "'this vehicle had no defects'")

    unknown = {"p_test_id": 7, "defects_json": None,
               "p_items_observability": obs.UNAVAILABLE}
    with pytest.raises(b0.UnobservedDefectDetail):
        b0.decode_defects(unknown, "section", b0.POLICY_FAIL)

    # ... and the middle state is NOT collapsed with it: an observed test with
    # no defect items still decodes to an honest empty list.
    observed_zero = {"p_test_id": 8, "defects_json": packets.EMPTY_PAYLOAD,
                     "p_items_observability": obs.PRESENT_ZERO_DEFECTS}
    assert b0.decode_defects(observed_zero, "section", b0.POLICY_FAIL) == []


def test_preflight_refuses_a_defect_reading_consumer_over_a_counts_build(tmp_path):
    """C: the exact queue.txt:167+168 pair, refused BEFORE packet creation."""
    lake = FixtureLake(str(tmp_path / "lake"))
    two_day_vehicle(lake, 1, date(2019, 4, 1), date(2020, 4, 1), n_prior_items=2)
    with pytest.raises(capability.CapabilityMismatch) as exc:
        build_lake(lake, tmp_path / "run", defect_detail="counts",
                   consumers=("b0_module:section",))
    message = str(exc.value)
    assert "per-item defect detail" in message
    assert "counts" in message
    assert "INC-2026-08-13" in message
    assert isinstance(exc.value, gates.GateFailure), (
        "capability refusals must be GateFailures so build.py returns rc=3")
    # and nothing was staged: the refusal precedes prepare()
    assert not os.path.exists(str(tmp_path / "run" / "stage" / "vehicle_day"))


@pytest.mark.parametrize("mode", ["counts", "none"])
def test_every_payload_free_mode_is_refused(tmp_path, mode):
    lake = FixtureLake(str(tmp_path / "lake"))
    two_day_vehicle(lake, 1, date(2019, 4, 1), date(2020, 4, 1), n_prior_items=1)
    with pytest.raises(capability.CapabilityMismatch):
        build_lake(lake, tmp_path / f"run_{mode}", defect_detail=mode,
                   consumers=("b0_module:section",))


def test_the_same_consumer_is_admitted_over_a_rows_build(tmp_path):
    """The gate is not a blanket refusal: the compatible pair proceeds."""
    lake = FixtureLake(str(tmp_path / "lake"))
    two_day_vehicle(lake, 1, date(2019, 4, 1), date(2020, 4, 1), n_prior_items=2)
    _factory, frames, _packets = build_lake(
        lake, tmp_path / "run", defect_detail="rows",
        consumers=("b0_module:section",))
    assert len(frames) == 2


def test_a_payload_free_build_is_still_allowed_when_nothing_needs_the_payload(tmp_path):
    """--defect-detail counts is not banned; consuming it wrongly is."""
    lake = FixtureLake(str(tmp_path / "lake"))
    two_day_vehicle(lake, 1, date(2019, 4, 1), date(2020, 4, 1), n_prior_items=2)
    _f, frames, packet_rows = build_lake(
        lake, tmp_path / "run", defect_detail="counts",
        consumers=("packets_only",))
    assert len(frames) == 2
    assert all(p["defects_json"] is None for p in packet_rows)


def test_declaring_consumers_with_no_packets_is_refused(tmp_path):
    lake = FixtureLake(str(tmp_path / "lake"))
    two_day_vehicle(lake, 1, date(2019, 4, 1), date(2020, 4, 1))
    with pytest.raises(capability.CapabilityMismatch):
        build_lake(lake, tmp_path / "run", emit_packets=False,
                   consumers=("b0_module:section",))


def test_unregistered_consumer_specs_are_refused(tmp_path):
    with pytest.raises(capability.CapabilityMismatch):
        capability.requirement_for("b0_module:freetext")


# --- the consumer-side gate, over real packet parquet -----------------------

def test_b0_runner_refuses_a_payload_free_packet_set(tmp_path):
    """C: even if a payload-free set is BUILT, the consumer refuses it."""
    lake = FixtureLake(str(tmp_path / "lake"))
    two_day_vehicle(lake, 1, date(2019, 4, 1), date(2020, 4, 1), n_prior_items=2)
    _f, manifest, _ = build_lake(lake, tmp_path / "run", write_parquet=True,
                                 defect_detail="counts")
    glob = os.path.join(manifest_output_dir(manifest, tmp_path / "run"), "*.parquet")
    with pytest.raises(capability.CapabilityMismatch) as exc:
        b0.assert_consumer_capability(glob, "section")
    assert "defect_payload_mode='counts'" in str(exc.value)


def test_b0_runner_admits_a_rows_packet_set(tmp_path):
    lake = FixtureLake(str(tmp_path / "lake"))
    two_day_vehicle(lake, 1, date(2019, 4, 1), date(2020, 4, 1), n_prior_items=2)
    _f, manifest, _ = build_lake(lake, tmp_path / "run", write_parquet=True,
                                 defect_detail="rows")
    glob = os.path.join(manifest_output_dir(manifest, tmp_path / "run"), "*.parquet")
    cap = b0.assert_consumer_capability(glob, "section")
    assert cap.defect_payload_mode == "rows"
    assert cap.capability_source == "declared"


def test_a_legacy_packet_set_without_a_sidecar_is_measured_not_assumed(tmp_path):
    """The fullpop artifacts have no sidecar. Measurement must still refuse them.

    0 of N non-null `defects_json` is exactly what the fullpop r1m set measured
    (0 of 5,560,040), so this is the incident's own artifact shape.
    """
    lake = FixtureLake(str(tmp_path / "lake"))
    two_day_vehicle(lake, 1, date(2019, 4, 1), date(2020, 4, 1), n_prior_items=2)
    _f, manifest, _ = build_lake(lake, tmp_path / "run", write_parquet=True,
                                 defect_detail="counts")
    packets_dir = manifest_output_dir(manifest, tmp_path / "run")
    os.remove(os.path.join(packets_dir, capability.CAPABILITY_FILENAME))
    glob = os.path.join(packets_dir, "*.parquet")
    assert capability.load_capability(glob) is None
    with pytest.raises(capability.CapabilityMismatch) as exc:
        b0.assert_consumer_capability(glob, "section")
    assert "measured" in str(exc.value)


def test_packet_capability_sidecar_records_every_required_field(tmp_path):
    """PREREG_CUBE_v2 §5 enumerates six things the metadata MUST express."""
    lake = FixtureLake(str(tmp_path / "lake"))
    two_day_vehicle(lake, 1, date(2019, 4, 1), date(2020, 4, 1), n_prior_items=2)
    ledger = write_item_coverage_ledger(
        str(tmp_path / "cov.csv"), [["", "", "", "covered", "unknown", "default"]])
    _f, manifest, _ = build_lake(lake, tmp_path / "run", write_parquet=True,
                                 item_coverage_csv=ledger)
    path = os.path.join(manifest_output_dir(manifest, tmp_path / "run"),
                        capability.CAPABILITY_FILENAME)
    payload = json.load(open(path, encoding="utf-8"))

    assert payload["defect_payload_mode"] == "rows"                 # 1
    assert set(payload["items_observability_states"]) == set(obs.ALL_STATES)  # 2
    assert payload["source_partition_availability"]                 # 3
    assert payload["item_join"]["total"]["items_observed"] > 0       # 4
    assert payload["packet_schema_version"] == capability.PACKET_SCHEMA_VERSION
    assert payload["publisher_schema_epochs"] == ["results_mts"]    # 5
    assert payload["build_config"]["defect_detail"] == "rows"        # 6
    assert payload["source_sha256"]["build_config_sha256"]
    assert payload["code_sha256"]
    assert payload["items_coverage_mode"] == obs.COVERAGE_CERTIFIED
    assert payload["item_coverage_ledger"]["sha256"]


def test_certified_coverage_can_be_required_and_is_refused_when_assumed(tmp_path):
    lake = FixtureLake(str(tmp_path / "lake"))
    two_day_vehicle(lake, 1, date(2019, 4, 1), date(2020, 4, 1), n_prior_items=2)
    _f, manifest, _ = build_lake(lake, tmp_path / "run", write_parquet=True)
    glob = os.path.join(manifest_output_dir(manifest, tmp_path / "run"), "*.parquet")
    b0.assert_consumer_capability(glob, "section")            # assumed is fine
    with pytest.raises(capability.CapabilityMismatch) as exc:
        b0.assert_consumer_capability(glob, "section",
                                      require_certified_coverage=True)
    assert "assumed_covered" in str(exc.value)


def manifest_output_dir(manifest: dict, run_root) -> str:
    rung = manifest["results"]["recipes"][RECIPE.name]["rungs"]["all"]
    return os.path.dirname(rung["packet_capability"])


# =============================================================================
# The three states, end to end (PREREG_CUBE_v2 §4)
# =============================================================================

def test_three_states_are_distinguishable_in_the_packets_view(tmp_path):
    """populated | '[]' + observed status | NULL + explicit status."""
    lake = FixtureLake(str(tmp_path / "lake"))
    # 1 items present   2 observed, no items   3 cell declared dark
    t1 = two_day_vehicle(lake, 1, date(2019, 4, 1), date(2020, 4, 1),
                         n_prior_items=2, base_test_id=100)
    t2 = two_day_vehicle(lake, 2, date(2019, 4, 1), date(2020, 4, 1),
                         n_prior_items=0, base_test_id=200)
    t3 = two_day_vehicle(lake, 3, date(2019, 6, 6), date(2020, 6, 6),
                         n_prior_items=0, base_test_id=300)
    ledger = write_item_coverage_ledger(str(tmp_path / "cov.csv"), [
        ["2019-06-06", "", "", "unavailable", "publication_short", "dark day"],
        ["", "", "", "covered", "unknown", "default"],
    ])
    _f, _frames, packet_rows = build_lake(lake, tmp_path / "run",
                                          item_coverage_csv=ledger)
    by_target = {p["tgt_id"]: p for p in packet_rows if p["p_test_id"] is not None}

    populated = by_target[t1]
    assert populated["p_items_observability"] == obs.PRESENT_WITH_DEFECTS
    assert json.loads(populated["defects_json"]), "state 3: populated"
    assert populated["p_n_items"] == 2

    observed_zero = by_target[t2]
    assert observed_zero["p_items_observability"] == obs.PRESENT_ZERO_DEFECTS
    assert observed_zero["defects_json"] == "[]", "state 2: empty WITH observed status"
    assert observed_zero["p_n_items"] == 0, "an observed zero keeps its zero"

    unavailable = by_target[t3]
    assert unavailable["p_items_observability"] == obs.UNAVAILABLE
    assert unavailable["defects_json"] is None, "state 1: NULL + explicit status"
    assert unavailable["p_n_items"] is None, "M4: NEVER 0 when unobservable"


def test_fail_bearing_with_zero_items_is_expected_missing_without_any_ledger(tmp_path):
    """The row-grain evidence rule: a FAIL cannot have zero reasons-for-rejection."""
    lake = FixtureLake(str(tmp_path / "lake"))
    failed = two_day_vehicle(lake, 1, date(2019, 4, 1), date(2020, 4, 1),
                             prior_outcome="FAIL", n_prior_items=0, base_test_id=100)
    passed = two_day_vehicle(lake, 2, date(2019, 4, 1), date(2020, 4, 1),
                             prior_outcome="PASS", n_prior_items=0, base_test_id=200)
    _f, frames, packet_rows = build_lake(lake, tmp_path / "run")
    by_target = {p["tgt_id"]: p for p in packet_rows if p["p_test_id"] is not None}
    assert by_target[failed]["p_items_observability"] == obs.EXPECTED_MISSING
    assert by_target[failed]["p_n_items"] is None
    # ... while the undecidable PASS keeps its zero, marked as an ASSUMPTION.
    assert by_target[passed]["p_items_observability"] == obs.ASSUMED_ZERO_DEFECTS
    assert by_target[passed]["p_n_items"] == 0

    rows = {r["tgt_id"]: r for r in frames}
    assert rows[failed]["b2_n_items_total"] is None
    assert rows[failed]["b2_item_observability_status"] == "none"
    assert rows[passed]["b2_n_items_total"] == 0
    assert rows[passed]["b2_item_observability_status"] == "full"


def test_certified_and_assumed_zeros_are_separable(tmp_path):
    """Same value, different evidence grade -- and the difference is emitted."""
    lake = FixtureLake(str(tmp_path / "lake"))
    target = two_day_vehicle(lake, 1, date(2019, 4, 1), date(2020, 4, 1))
    _f, _frames, without = build_lake(lake, tmp_path / "assumed")
    ledger = write_item_coverage_ledger(
        str(tmp_path / "cov.csv"), [["", "", "", "covered", "unknown", "default"]])
    _f2, _frames2, with_ledger = build_lake(lake, tmp_path / "certified",
                                            item_coverage_csv=ledger)
    a = {p["tgt_id"]: p for p in without if p["p_test_id"] is not None}[target]
    b = {p["tgt_id"]: p for p in with_ledger if p["p_test_id"] is not None}[target]
    assert a["p_items_observability"] == obs.ASSUMED_ZERO_DEFECTS
    assert b["p_items_observability"] == obs.PRESENT_ZERO_DEFECTS
    assert a["p_n_items"] == b["p_n_items"] == 0, "the VALUE is identical"
    assert a["defects_json"] == b["defects_json"] == "[]"


# =============================================================================
# The damage-ratio guard (PREREG_CUBE_v2 §4: 325:1)
# =============================================================================

def test_a_clean_pass_keeps_its_honest_zero(tmp_path):
    """ITEMS_PRESENT_ZERO_DEFECTS is 49.9-62.1% of passes. It must NOT go NULL."""
    lake = FixtureLake(str(tmp_path / "lake"))
    target = two_day_vehicle(lake, 1, date(2019, 4, 1), date(2020, 4, 1),
                             prior_outcome="PASS", n_prior_items=0)
    _f, frames, _p = build_lake(lake, tmp_path / "run")
    row = {r["tgt_id"]: r for r in frames}[target]
    zeroed = ["b2_n_items_total", "b2_n_catalogue_miss_items", "b2_breadth_categories",
              "b3_n_prs_items", "b3_n_fail_items_initial", "b3_n_fail_items_final",
              "b3_n_advisory_items", "b4_n_adv_to_fail_transitions",
              "b4_n_recurrence_after_repair", "b2_brakes_n_days"]
    for name in zeroed:
        assert row[name] == 0, f"{name} went NULL: that is the 325:1 catastrophe"
    assert row["b2_n_prior_days_items_observed"] == 1
    assert row["b2_n_prior_days_items_unobserved"] == 0
    assert row["b2_n_prior_days_items_zero_defects"] == 1


def test_a_vehicle_with_no_priors_keeps_certain_zeros(tmp_path):
    """No prior days => no prior items. That zero is a certainty, not a guess."""
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2019, 4, 1)))
    _f, frames, _p = build_lake(lake, tmp_path / "run")
    row = frames[0]
    assert row["b1_n_prior_test_days"] == 0
    assert row["b2_n_items_total"] == 0
    assert row["b3_n_advisory_items"] == 0
    assert row["b2_item_observability_status"] == "no_priors"


def test_the_repair_nulls_only_the_unobservable_minority(tmp_path):
    """Population-level guard: NULLs track dark days, not zeros."""
    lake = FixtureLake(str(tmp_path / "lake"))
    dark_day = date(2019, 6, 6)
    n_clean, n_dark = 12, 2
    targets_clean, targets_dark = [], []
    for i in range(n_clean):
        targets_clean.append(two_day_vehicle(
            lake, 100 + i, date(2019, 4, 1), date(2020, 4, 1), base_test_id=1000 + 10 * i))
    for i in range(n_dark):
        targets_dark.append(two_day_vehicle(
            lake, 200 + i, dark_day, date(2020, 4, 1), base_test_id=5000 + 10 * i))
    ledger = write_item_coverage_ledger(str(tmp_path / "cov.csv"), [
        [dark_day.isoformat(), "", "", "unavailable", "publication_short", ""],
        ["", "", "", "covered", "unknown", "default"],
    ])
    _f, frames, _p = build_lake(lake, tmp_path / "run", item_coverage_csv=ledger)
    rows = {r["tgt_id"]: r for r in frames}
    n_null = sum(1 for t in targets_clean + targets_dark
                 if rows[t]["b2_n_items_total"] is None)
    assert n_null == n_dark, (
        f"{n_null} rows went NULL but only {n_dark} are unobservable -- a "
        f"blanket null would destroy the observed-zero majority")
    assert all(rows[t]["b2_n_items_total"] == 0 for t in targets_clean)


# =============================================================================
# M3 -- the second, independent coalesce in state.py
# =============================================================================

def test_day_atom_item_has_no_zero_default_any_more():
    """M3: the signature itself is the guard.

    `DayAtom.item(self, name, default=0)` was a second coalesce that would have
    silently undone the SQL repair. It must not be reachable by omission.
    """
    signature = inspect.signature(fstate.DayAtom.item)
    assert list(signature.parameters) == ["self", "name"], (
        "DayAtom.item regained a defaulted parameter: M3 is re-armed")
    for parameter in signature.parameters.values():
        assert parameter.default is inspect.Parameter.empty


def _day(items, **kwargs) -> fstate.DayAtom:
    base = dict(vehicle_id=1, test_date=date(2019, 4, 1), n_tests_day=1,
                n_initial_day=1, n_definitive_day=1, n_nonresult_day=0,
                has_pass=True, has_fail=False, has_prs=False, has_nonresult=False,
                n_distinct_outcomes=1, max_valid_mileage_day=100,
                min_valid_mileage_day=100, n_valid_mileage_day=1,
                mileage_conflict=False, severity_observable=True,
                first_use_date=date(2010, 1, 1), items=items,
                tests=[{"ttype": "NT", "outcome": "PASS", "n_items": None,
                        "items_obs": obs.UNAVAILABLE, "test_id": 1}])
    base.update(kwargs)
    return fstate.DayAtom(**base)


def test_day_atom_item_is_null_preserving():
    day = _day({"n_items": None, "cat_brakes_n": None})
    assert day.item("n_items") is None
    assert day.cat_n("brakes") is None
    observed = _day({"n_items": 0, "cat_brakes_n": 0}, n_tests_items_observed=1)
    assert observed.item("n_items") == 0
    assert observed.cat_n("brakes") == 0


def test_an_unobservable_day_adds_nothing_to_the_running_state():
    """M3 end-to-end: a dark day must not fold a confident 0 into the state."""
    state = fstate.AsOfState(vehicle_id=1)
    dark = _day({name: None for name in atoms.SCALAR_ITEM_COLUMNS},
                n_tests_items_unobserved=1, n_tests_items_unavailable=1)
    state.update(dark)
    assert state.n_days == 1, "the TEST history is still fully observed (B1/B5)"
    assert state.n_days_items_observed == 0
    assert state.n_days_items_unavailable == 1
    assert state.slope_n == 0, "a dark day contributed a false 0 to the slope"
    assert list(state.burden) == [], "a dark day entered the burden window"
    assert state.last_day_n_items is None
    assert state.last_day_categories is None, "empty set would assert 'no defects'"
    assert state.n_severity_observable_days == 0, (
        "the b3 denominator counted a day whose items are dark")


def test_an_observed_zero_day_does_fold_in():
    state = fstate.AsOfState(vehicle_id=1)
    clean = _day({name: 0 for name in atoms.SCALAR_ITEM_COLUMNS},
                 n_tests_items_observed=1, n_tests_items_zero_defects=1,
                 tests=[{"ttype": "NT", "outcome": "PASS", "n_items": 0,
                         "items_obs": obs.PRESENT_ZERO_DEFECTS, "test_id": 1}])
    clean.items.update({f"cat_{k}_n": 0 for k in ("brakes", "tyres")})
    state.update(clean)
    assert state.n_days_items_observed == 1
    assert state.slope_n == 1
    assert list(state.burden) == [0]
    assert state.last_day_n_items == 0
    assert state.last_day_categories == set()


def test_prior_tests_carry_null_not_zero(tmp_path):
    """M4 at state.py:118 (`int(t.get("n_items") or 0)`)."""
    dark = _day({name: None for name in atoms.SCALAR_ITEM_COLUMNS},
                n_tests_items_unobserved=1, n_tests_items_unavailable=1)
    prior = dark.prior_tests()[0]
    assert prior.n_items is None
    assert prior.items_observability == obs.UNAVAILABLE
    assert not prior.items_observed


def test_a_stale_staged_atom_without_the_observability_columns_raises():
    """The columns are mandatory: a stale staging dir must not silently pass."""
    with pytest.raises(KeyError) as exc:
        fstate.day_atom_from_row(
            {"vehicle_id": 1, "test_date": date(2019, 1, 1), "n_tests_day": 1,
             "n_initial_day": 1, "n_definitive_day": 1, "n_nonresult_day": 0,
             "has_pass": True, "has_fail": False, "has_prs": False,
             "has_nonresult": False, "n_distinct_outcomes": 1,
             "max_valid_mileage_day": None, "min_valid_mileage_day": None,
             "n_valid_mileage_day": 0, "mileage_conflict": False,
             "severity_observable": True, "first_use_date": None, "tests": []},
            ["n_items"])
    assert "INC-2026-08-13" in str(exc.value)


# =============================================================================
# M1/M2 -- covered vs uncovered partitions, same physical history
# =============================================================================

def test_same_physical_history_in_covered_vs_dark_cells_emits_different_values(tmp_path):
    """The whole point of the repair, in one assertion.

    Two vehicles with IDENTICAL recorded history -- same dates, same outcomes,
    same (zero) item rows -- separated ONLY by the publisher partition they
    came from, one of which the ledger declares dark. Their TEST-history
    features must be bit-identical; their ITEM-history features must NOT be.
    """
    lake = FixtureLake(str(tmp_path / "lake"))
    covered = two_day_vehicle(lake, 1, date(2019, 4, 1), date(2020, 4, 1),
                              schema_epoch="results_mts", base_test_id=100)
    dark = two_day_vehicle(lake, 2, date(2019, 4, 1), date(2020, 4, 1),
                           schema_epoch="results_extracts", base_test_id=200)
    ledger = write_item_coverage_ledger(str(tmp_path / "cov.csv"), [
        ["", "results_extracts", "", "unavailable", "publication_short", "dark cell"],
        ["", "", "", "covered", "unknown", "default"],
    ])
    _f, frames, _p = build_lake(lake, tmp_path / "run", item_coverage_csv=ledger)
    rows = {r["tgt_id"]: r for r in frames}
    a, b = rows[covered], rows[dark]

    test_history = [c.name for c in blocks.ALL_COLUMNS if c.block in ("B1", "B5")]
    differing = [n for n in test_history if a[n] != b[n]
                 and not (a[n] is None and b[n] is None)]
    assert differing == [], (
        f"B1/B5 must be untouched by the item repair; differing: {differing}")

    assert a["b2_n_items_total"] == 0 and b["b2_n_items_total"] is None
    assert a["b3_n_advisory_items"] == 0 and b["b3_n_advisory_items"] is None
    assert a["b4_n_adv_to_fail_transitions"] == 0
    assert b["b4_n_adv_to_fail_transitions"] is None
    assert a["b2_item_observability_status"] == "full"
    assert b["b2_item_observability_status"] == "none"
    assert b["b2_n_prior_days_items_unavailable"] == 1
    assert a["b3_n_days_fine_severity_observable"] == 1
    assert b["b3_n_days_fine_severity_observable"] == 0, (
        "the severity denominator counted a day whose items are dark")
    assert a["b4_deterioration_slope_n_days"] == 1
    assert b["b4_deterioration_slope_n_days"] == 0


def test_a_structurally_dark_outcome_class_is_honoured(tmp_path):
    """The measured 2024-25 cliff: non-definitive outcomes lost items entirely."""
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2019, 4, 1),
                          outcome="ABANDONED", schema_epoch="results_extracts"))
    lake.add_test(TestRow(test_id=2, vehicle_id=1, test_date=date(2019, 5, 1),
                          outcome="PASS", schema_epoch="results_extracts"))
    lake.add_test(TestRow(test_id=3, vehicle_id=1, test_date=date(2020, 4, 1),
                          outcome="PASS", schema_epoch="results_extracts"))
    ledger = write_item_coverage_ledger(str(tmp_path / "cov.csv"), [
        ["", "results_extracts", "non_definitive", "unavailable",
         "publication_short", "items lost at the results_extracts boundary"],
        ["", "", "", "covered", "unknown", "default"],
    ])
    _f, _frames, packet_rows = build_lake(lake, tmp_path / "run",
                                          item_coverage_csv=ledger)
    by_prior = {p["p_test_id"]: p for p in packet_rows
                if p["tgt_id"] == 3 and p["p_test_id"] is not None}
    assert by_prior[1]["p_items_observability"] == obs.UNAVAILABLE
    assert by_prior[1]["p_n_items"] is None
    assert by_prior[2]["p_items_observability"] == obs.PRESENT_ZERO_DEFECTS
    assert by_prior[2]["p_n_items"] == 0


def test_a_partially_dark_day_keeps_its_observed_half(tmp_path):
    """A day with one observable and one dark test is neither 0 nor all-NULL."""
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2019, 4, 1),
                          outcome="ABANDONED", schema_epoch="results_extracts"))
    lake.add_test(TestRow(test_id=2, vehicle_id=1, test_date=date(2019, 4, 1),
                          outcome="FAIL", test_type="RT",
                          schema_epoch="results_extracts"))
    for rfr in ("20001", "20002"):
        lake.add_item(ItemRow(test_id=2, rfr_id=rfr, rfr_type_code="F"))
    lake.add_test(TestRow(test_id=3, vehicle_id=1, test_date=date(2020, 4, 1),
                          outcome="PASS", schema_epoch="results_extracts"))
    ledger = write_item_coverage_ledger(str(tmp_path / "cov.csv"), [
        ["", "results_extracts", "non_definitive", "unavailable", "publication_short", ""],
        ["", "", "", "covered", "unknown", "default"],
    ])
    _f, frames, _p = build_lake(lake, tmp_path / "run", item_coverage_csv=ledger)
    row = {r["tgt_id"]: r for r in frames}[3]
    assert row["b2_n_items_total"] == 2, "the observed half must survive"
    assert row["b2_n_prior_days_items_observed"] == 1
    assert row["b2_n_prior_days_items_unobserved"] == 1
    assert row["b2_item_observability_status"] == "partial"


# =============================================================================
# Ledger integrity
# =============================================================================

def test_a_ledger_that_contradicts_the_lake_refuses_the_build(tmp_path):
    """A cell rule outranks the join, so a WRONG rule would destroy real data."""
    lake = FixtureLake(str(tmp_path / "lake"))
    two_day_vehicle(lake, 1, date(2019, 4, 1), date(2020, 4, 1), n_prior_items=2)
    ledger = write_item_coverage_ledger(str(tmp_path / "cov.csv"), [
        ["2019-04-01", "", "", "unavailable", "publication_short", "wrong"],
    ])
    with pytest.raises(gates.GateFailure) as exc:
        build_lake(lake, tmp_path / "run", item_coverage_csv=ledger)
    assert "declares DARK do carry item rows" in str(exc.value)


def test_an_ambiguous_ledger_is_rejected_at_load(tmp_path):
    path = write_item_coverage_ledger(str(tmp_path / "cov.csv"), [
        ["2019-04-01", "", "", "unavailable", "publication_short", ""],
        ["", "results_mts", "", "covered", "unknown", ""],
    ])
    with pytest.raises(obs.LedgerError) as exc:
        obs.load_coverage_ledger(path)
    assert "ambiguous" in str(exc.value)


def test_a_missing_ledger_file_is_never_read_as_everything_is_covered(tmp_path):
    with pytest.raises(obs.LedgerError):
        obs.load_coverage_ledger(str(tmp_path / "absent.csv"))
    empty = obs.load_coverage_ledger(None)
    assert empty.coverage_mode == obs.COVERAGE_ASSUMED


def test_a_ledger_without_a_default_rule_stays_assumed(tmp_path):
    path = write_item_coverage_ledger(str(tmp_path / "cov.csv"), [
        ["2019-06-06", "", "", "unavailable", "publication_short", ""],
    ])
    assert obs.load_coverage_ledger(path).coverage_mode == obs.COVERAGE_ASSUMED


def test_an_unknown_coverage_value_is_rejected(tmp_path):
    path = write_item_coverage_ledger(str(tmp_path / "cov.csv"), [
        ["", "", "", "probably_fine", "unknown", ""],
    ])
    with pytest.raises(obs.LedgerError):
        obs.load_coverage_ledger(path)


# =============================================================================
# No emitter may infer "no defect" from a NULL payload
# =============================================================================

def test_defects_json_never_invents_an_observation():
    assert packets.defects_json(None, obs.UNAVAILABLE) is None
    assert packets.defects_json(None, obs.EXPECTED_MISSING) is None
    assert packets.defects_json(None, obs.PRESENT_ZERO_DEFECTS) == "[]"
    assert packets.defects_json(None, obs.ASSUMED_ZERO_DEFECTS) == "[]"
    payload = packets.defects_json([{"rfr": "20001"}], obs.PRESENT_WITH_DEFECTS)
    assert json.loads(payload)[0]["rfr"] == "20001"
    # a payload-free BUILD nulls everything, whatever the row state says
    assert packets.defects_json([{"rfr": "20001"}], obs.PRESENT_WITH_DEFECTS,
                                capability.PAYLOAD_COUNTS) is None


@pytest.mark.parametrize("policy,expected", [
    (b0.POLICY_DROP_PRIOR, None), (b0.POLICY_EMPTY, [])])
def test_unobserved_defect_degradations_must_be_asked_for(policy, expected):
    row = {"p_test_id": 1, "defects_json": None,
           "p_items_observability": obs.EXPECTED_MISSING}
    assert b0.decode_defects(row, "section", policy) == expected
    with pytest.raises(b0.UnobservedDefectDetail):
        b0.decode_defects(row, "section", b0.POLICY_FAIL)


def test_an_observed_prior_with_a_null_payload_is_a_contract_violation():
    row = {"p_test_id": 1, "defects_json": None,
           "p_items_observability": obs.PRESENT_ZERO_DEFECTS}
    with pytest.raises(b0.UnobservedDefectDetail):
        b0.decode_defects(row, "section", b0.POLICY_FAIL)


def _module_of_record_available() -> bool:
    try:
        b0.import_module_of_record()
        return True
    except Exception:                                    # pragma: no cover
        return False


@pytest.mark.skipif(not _module_of_record_available(),
                    reason="feature_engineering_v55 module of record not importable")
def test_b0_run_refuses_an_unobservable_prior_end_to_end(tmp_path):
    """The whole consumer path, not just the decoder: fail-closed by default."""
    lake = FixtureLake(str(tmp_path / "lake"))
    # a prior FAIL with zero item rows -> expected_missing by the evidence rule
    two_day_vehicle(lake, 1, date(2019, 4, 1), date(2020, 4, 1),
                    prior_outcome="FAIL", n_prior_items=0, base_test_id=100)
    two_day_vehicle(lake, 2, date(2019, 4, 1), date(2020, 4, 1),
                    prior_outcome="PASS", n_prior_items=2, base_test_id=200)
    _f, manifest, _ = build_lake(lake, tmp_path / "run", write_parquet=True)
    glob = os.path.join(manifest_output_dir(manifest, tmp_path / "run"), "*.parquet")

    with pytest.raises(b0.UnobservedDefectDetail):
        b0.run(glob, str(tmp_path / "b0_fail.parquet"), text_source="section")

    out = b0.run(glob, str(tmp_path / "b0_ok.parquet"), text_source="section",
                 unobserved_policy=b0.POLICY_DROP_PRIOR)
    assert out["unobserved_defect_policy"] == b0.POLICY_DROP_PRIOR
    assert out["item_observability_seen"][obs.EXPECTED_MISSING] == 1
    assert out["item_observability_seen"][obs.PRESENT_WITH_DEFECTS] == 1
    assert out["packet_capability"]["defect_payload_mode"] == "rows"
    assert out["packet_capability"]["capability_source"] == "declared"


def test_the_synthetic_population_contains_no_impossible_state(tmp_path):
    """A FAIL/PRS with zero items cannot exist; the fixture must not fake one.

    It is the lake's strongest missing-item detector (ITEM_OBSERVABILITY_DESIGN
    §1.2); a fixture that manufactured it would make the detector untestable.
    """
    from factory.fixtures import default_population

    lake = default_population(str(tmp_path / "lake"), n_vehicles=40, seed=3,
                              start_year=2016, n_years=5)
    with_items = {i.test_id for i in lake.items}
    impossible = [t.test_id for t in lake.tests
                  if t.outcome in ("FAIL", "PRS") and t.test_id not in with_items]
    assert impossible == []


def test_decode_counts_the_states_it_saw():
    counters = {}
    b0.decode_defects({"p_test_id": 1, "defects_json": "[]",
                       "p_items_observability": obs.PRESENT_ZERO_DEFECTS},
                      "section", b0.POLICY_FAIL, counters)
    b0.decode_defects({"p_test_id": 2, "defects_json": None,
                       "p_items_observability": obs.UNAVAILABLE},
                      "section", b0.POLICY_EMPTY, counters)
    assert counters == {obs.PRESENT_ZERO_DEFECTS: 1, obs.UNAVAILABLE: 1}
