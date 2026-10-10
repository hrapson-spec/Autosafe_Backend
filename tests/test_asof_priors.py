"""tests/test_asof_priors.py -- adoption-gate 4 (2026-07-04).

Covers:
  - AsOfPriorStore.lookup() own/parent/global backoff paths
  - month-clip (LEAST(serving_month, max_asof)) arithmetic, both directions
  - canon normalisation (norm_str / canon_make / canon_model_id)
  - the production default (restrict_to_max_asof=True) actually slices
  - model_v55.asof_priors_state() self-check branching
  - AUTOSAFE_ASOF_PRIORS flag-off no-op proof (engineer_features integration)
  - GOLDEN CROSS-CHECK: 200 sampled synthetic rows against an independently
    re-derived reference (plain dict lookups written fresh in this file,
    never calling AsOfPriorStore's own _raw()/lookup() internals) -- mirrors
    the independence-boundary design of
    work/goal_0750/prior_rebuild_v1/scripts/golden_row_parity.py's
    compute_reference(), without importing anything from that repo (a
    separate git repo this product repo cannot depend on at deploy time).
"""
from __future__ import annotations

import logging
import random
import shutil
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from asof_priors import (
    ALL_COLUMNS,
    BACKOFF_PARENT,
    CHANNELS,
    DEFAULT_TABLES_DIR,
    STALE_MONTHS_WARN_THRESHOLD,
    AsOfPriorStore,
    PriorLookup,
    _month_trunc,
    _months_between,
    canon_make,
    canon_model_id,
    norm_str,
    tables_present,
)
import feature_engineering_v55
import model_v55
from dvsa_client import VehicleHistory

# --------------------------------------------------------------------------- #
# Fixture-table writer helpers (test-only; not shared with asof_priors.py)
# --------------------------------------------------------------------------- #

# channel -> (parquet filename stem, own-grain key columns, rate column name)
# Deliberately re-typed here (not imported from asof_priors._TABLE_SPEC) so a
# test fixture bug and a production bug can't share the same wrong constant.
_STEM = {
    "make_fail_rate_smoothed": ("make", ("make",), "make_rate_long"),
    "segment_fail_rate_smoothed": ("segment", ("make", "age_band", "mileage_band"), "segment_rate_long"),
    "model_fail_rate_smoothed": ("model", ("model_id",), "model_rate_long"),
    "make_age_fail_rate_eb": ("make_age", ("make", "age_band"), "make_age_rate_eb"),
    "model_age_fail_rate_eb": ("model_age", ("model_id", "age_band"), "model_age_rate_eb"),
}


def _write_channel_table(out_dir: Path, channel: str, rows: list, max_asof: str, global_rate: float) -> None:
    """Write one channel's table + meta parquet, matching the production
    schema closely enough to exercise the real AsOfPriorStore.load() path
    (real column names, a genuine DATE-typed asof_month column, a meta file
    with at least the 2 columns load() actually reads)."""
    stem, _key_cols, _rate_col = _STEM[channel]
    df = pd.DataFrame(rows)
    df["asof_month"] = pd.to_datetime(df["asof_month"]).dt.date
    df.to_parquet(out_dir / f"{stem}_fail_rate_asof.parquet", engine="pyarrow", index=False)

    meta = pd.DataFrame([{
        "max_asof": pd.to_datetime(max_asof).date(),
        "global_rate_at_max_asof": float(global_rate),
    }])
    meta.to_parquet(out_dir / f"{stem}_meta.parquet", engine="pyarrow", index=False)


def _make_history(make="FORD", model="FOCUS", manufacture_year=2023, mot_tests=None) -> VehicleHistory:
    return VehicleHistory(
        registration="TEST123",
        make=make,
        model=model,
        fuel_type="PE",
        colour="BLUE",
        registration_date=datetime(manufacture_year, 6, 1),
        manufacture_date=datetime(manufacture_year, 6, 1),
        engine_size=1600,
        mot_tests=mot_tests or [],
    )


# --------------------------------------------------------------------------- #
# Small, hand-built multi-month fixture (own/parent/global + month-clip)
# --------------------------------------------------------------------------- #

SMALL_MAX_ASOF = "2025-06-01"
SMALL_HISTORICAL_MONTH = "2024-01-01"
SMALL_GLOBAL_RATE = 0.19


def _build_small_fixture(out_dir: Path) -> None:
    _write_channel_table(out_dir, "make_fail_rate_smoothed", [
        {"asof_month": SMALL_HISTORICAL_MONTH, "make": "FORD", "make_rate_long": 0.20},
        {"asof_month": SMALL_MAX_ASOF, "make": "FORD", "make_rate_long": 0.22},
        {"asof_month": SMALL_MAX_ASOF, "make": "TOYOTA", "make_rate_long": 0.10},
    ], SMALL_MAX_ASOF, SMALL_GLOBAL_RATE)

    _write_channel_table(out_dir, "segment_fail_rate_smoothed", [
        {"asof_month": SMALL_MAX_ASOF, "make": "FORD", "age_band": "0-3",
         "mileage_band": "0-30k", "segment_rate_long": 0.15},
    ], SMALL_MAX_ASOF, SMALL_GLOBAL_RATE)

    _write_channel_table(out_dir, "model_fail_rate_smoothed", [
        {"asof_month": SMALL_MAX_ASOF, "model_id": "FORD FOCUS", "model_rate_long": 0.25},
    ], SMALL_MAX_ASOF, SMALL_GLOBAL_RATE)

    _write_channel_table(out_dir, "make_age_fail_rate_eb", [
        {"asof_month": SMALL_MAX_ASOF, "make": "FORD", "age_band": "0-3", "make_age_rate_eb": 0.21},
        {"asof_month": SMALL_MAX_ASOF, "make": "FORD", "age_band": "3-5", "make_age_rate_eb": 0.23},
    ], SMALL_MAX_ASOF, SMALL_GLOBAL_RATE)

    _write_channel_table(out_dir, "model_age_fail_rate_eb", [
        {"asof_month": SMALL_MAX_ASOF, "model_id": "FORD FOCUS", "age_band": "0-3", "model_age_rate_eb": 0.30},
        {"asof_month": SMALL_HISTORICAL_MONTH, "model_id": "FORD FOCUS", "age_band": "0-3", "model_age_rate_eb": 0.28},
    ], SMALL_MAX_ASOF, SMALL_GLOBAL_RATE)


@pytest.fixture
def small_fixture_dir(tmp_path) -> Path:
    d = tmp_path / "small_fixture"
    d.mkdir()
    _build_small_fixture(d)
    return d


@pytest.fixture
def full_history_store(small_fixture_dir) -> AsOfPriorStore:
    """restrict_to_max_asof=False: keeps both 2024-01 and 2025-06 rows --
    TEST-ONLY mode, needed to exercise the month-clip arithmetic."""
    return AsOfPriorStore.load(small_fixture_dir, restrict_to_max_asof=False)


@pytest.fixture
def sliced_store(small_fixture_dir) -> AsOfPriorStore:
    """Production default (restrict_to_max_asof=True)."""
    return AsOfPriorStore.load(small_fixture_dir)


# --------------------------------------------------------------------------- #
# Canon normalisation
# --------------------------------------------------------------------------- #

class TestCanon:
    def test_norm_str_collapses_whitespace_and_uppercases(self):
        assert norm_str("  ford   focus ") == "FORD FOCUS"

    def test_norm_str_none_is_none(self):
        assert norm_str(None) is None

    def test_norm_str_empty_or_blank_is_none(self):
        assert norm_str("") is None
        assert norm_str("   ") is None

    def test_canon_make_is_norm_str(self):
        assert canon_make("ford") == "FORD"
        assert canon_make(None) is None

    def test_canon_model_id_prefixes_bare_model(self):
        assert canon_model_id("FORD", "Focus") == "FORD FOCUS"

    def test_canon_model_id_no_double_prefix_case_and_whitespace_insensitive(self):
        assert canon_model_id("FORD", "ford   focus") == "FORD FOCUS"
        assert canon_model_id("FORD", "FORD") == "FORD"

    def test_canon_model_id_make_only_when_model_missing_or_blank(self):
        assert canon_model_id("FORD", None) == "FORD"
        assert canon_model_id("FORD", "") == "FORD"

    def test_canon_model_id_none_make_is_none(self):
        assert canon_model_id(None, "FOCUS") is None

    def test_canon_model_id_multiword_make(self):
        assert canon_model_id("Land Rover", "Range Rover") == "LAND ROVER RANGE ROVER"
        assert canon_model_id("Land Rover", "land   rover range rover") == "LAND ROVER RANGE ROVER"

    def test_canon_model_id_idempotent(self):
        once = canon_model_id("FORD", "Focus")
        twice = canon_model_id("FORD", once)
        assert once == twice == "FORD FOCUS"


class TestMonthTrunc:
    def test_datetime_truncates_to_first_of_month(self):
        assert _month_trunc(datetime(2025, 7, 15, 13, 30)) == date(2025, 7, 1)

    def test_date_truncates_to_first_of_month(self):
        assert _month_trunc(date(2025, 7, 15)) == date(2025, 7, 1)

    def test_pandas_timestamp_truncates_to_first_of_month(self):
        assert _month_trunc(pd.Timestamp("2025-07-15")) == date(2025, 7, 1)

    def test_iso_string_truncates_to_first_of_month(self):
        assert _month_trunc("2025-07-15") == date(2025, 7, 1)

    def test_already_first_of_month_is_unchanged(self):
        assert _month_trunc(date(2025, 7, 1)) == date(2025, 7, 1)


# --------------------------------------------------------------------------- #
# Spec pin
# --------------------------------------------------------------------------- #

def test_channel_spec_matches_frozen_backoff_order():
    assert CHANNELS == (
        "make_fail_rate_smoothed", "segment_fail_rate_smoothed",
        "model_fail_rate_smoothed", "make_age_fail_rate_eb", "model_age_fail_rate_eb",
    )
    assert BACKOFF_PARENT == {
        "model_age_fail_rate_eb": "make_age_fail_rate_eb",
        "model_fail_rate_smoothed": "make_fail_rate_smoothed",
    }
    assert ALL_COLUMNS == CHANNELS + ("eb_unified_prior",)


# --------------------------------------------------------------------------- #
# Lookup semantics: own / parent / global
# --------------------------------------------------------------------------- #

class TestLookupSemantics:
    LIVE_DATE = date(2025, 7, 15)  # after max_asof -> eff_asof always = max_asof

    def test_own_cell_hit(self, full_history_store):
        r = full_history_store.lookup(
            "model_age_fail_rate_eb",
            {"make": "FORD", "model_id": "FORD FOCUS", "age_band": "0-3", "mileage_band": "0-30k"},
            self.LIVE_DATE,
        )
        assert r == PriorLookup(0.30, "own")

    def test_parent_backoff_when_own_cell_missing(self, full_history_store):
        r = full_history_store.lookup(
            "model_age_fail_rate_eb",
            {"make": "FORD", "model_id": "FORD KA", "age_band": "0-3", "mileage_band": "0-30k"},
            self.LIVE_DATE,
        )
        assert r == PriorLookup(0.21, "parent")

    def test_global_fallback_when_own_and_parent_missing(self, full_history_store):
        r = full_history_store.lookup(
            "model_age_fail_rate_eb",
            {"make": "TOYOTA", "model_id": "TOYOTA YARIS", "age_band": "6-10", "mileage_band": "0-30k"},
            self.LIVE_DATE,
        )
        assert r == PriorLookup(SMALL_GLOBAL_RATE, "global")

    def test_model_channel_parent_backoff(self, full_history_store):
        # "FORD KA" absent from the model table -> backs off to FORD's make rate
        r = full_history_store.lookup(
            "model_fail_rate_smoothed",
            {"make": "FORD", "model_id": "FORD KA"},
            self.LIVE_DATE,
        )
        assert r == PriorLookup(0.22, "parent")

    def test_make_channel_has_no_parent_goes_straight_to_global(self, full_history_store):
        r = full_history_store.lookup("make_fail_rate_smoothed", {"make": "VAUXHALL"}, self.LIVE_DATE)
        assert r == PriorLookup(SMALL_GLOBAL_RATE, "global")

    def test_segment_channel_has_no_parent(self, full_history_store):
        r = full_history_store.lookup(
            "segment_fail_rate_smoothed",
            {"make": "TOYOTA", "age_band": "0-3", "mileage_band": "0-30k"},
            self.LIVE_DATE,
        )
        assert r == PriorLookup(SMALL_GLOBAL_RATE, "global")

    def test_make_age_channel_has_no_parent(self, full_history_store):
        r = full_history_store.lookup(
            "make_age_fail_rate_eb", {"make": "TOYOTA", "age_band": "0-3"}, self.LIVE_DATE,
        )
        assert r == PriorLookup(SMALL_GLOBAL_RATE, "global")

    def test_eb_unified_prior_mirrors_model_age_own(self, full_history_store):
        keys = {"make": "FORD", "model_id": "FORD FOCUS", "age_band": "0-3", "mileage_band": "0-30k"}
        assert (full_history_store.lookup("eb_unified_prior", keys, self.LIVE_DATE)
                == full_history_store.lookup("model_age_fail_rate_eb", keys, self.LIVE_DATE)
                == PriorLookup(0.30, "own"))

    def test_eb_unified_prior_mirrors_model_age_parent(self, full_history_store):
        keys = {"make": "FORD", "model_id": "FORD KA", "age_band": "0-3", "mileage_band": "0-30k"}
        assert (full_history_store.lookup("eb_unified_prior", keys, self.LIVE_DATE)
                == full_history_store.lookup("model_age_fail_rate_eb", keys, self.LIVE_DATE)
                == PriorLookup(0.21, "parent"))

    def test_missing_key_component_is_a_miss_not_a_crash(self, full_history_store):
        r = full_history_store.lookup("make_fail_rate_smoothed", {"make": None}, self.LIVE_DATE)
        assert r == PriorLookup(SMALL_GLOBAL_RATE, "global")

    def test_unknown_channel_raises_keyerror(self, full_history_store):
        with pytest.raises(KeyError):
            full_history_store.lookup("not_a_real_channel", {}, self.LIVE_DATE)


# --------------------------------------------------------------------------- #
# Month clip: eff_asof = LEAST(serving_month, max_asof)
# --------------------------------------------------------------------------- #

class TestMonthClip:
    KEYS = {"make": "FORD", "model_id": "FORD FOCUS", "age_band": "0-3", "mileage_band": "0-30k"}

    def test_serving_month_after_max_asof_clips_to_max_asof(self, full_history_store):
        r = full_history_store.lookup("model_age_fail_rate_eb", self.KEYS, date(2030, 1, 1))
        assert r == PriorLookup(0.30, "own")  # the 2025-06 row, never a lookahead to 2030

    def test_serving_month_before_max_asof_uses_its_own_month(self, full_history_store):
        r = full_history_store.lookup("model_age_fail_rate_eb", self.KEYS, date(2024, 1, 15))
        assert r == PriorLookup(0.28, "own")  # the DISTINCT 2024-01 value, not the 2025-06 value (0.30)

    def test_mid_month_dates_truncate_to_month_start(self, full_history_store):
        r_mid = full_history_store.lookup("make_fail_rate_smoothed", {"make": "FORD"}, date(2024, 1, 28))
        r_start = full_history_store.lookup("make_fail_rate_smoothed", {"make": "FORD"}, date(2024, 1, 1))
        assert r_mid == r_start == PriorLookup(0.20, "own")

    def test_serving_month_exactly_at_max_asof(self, full_history_store):
        r = full_history_store.lookup("model_age_fail_rate_eb", self.KEYS, date(2025, 6, 1))
        assert r == PriorLookup(0.30, "own")


# --------------------------------------------------------------------------- #
# Production default actually restricts to max_asof (the memory-saving path)
# --------------------------------------------------------------------------- #

class TestProductionSlicing:
    def test_default_load_keeps_only_max_asof_rows(self, small_fixture_dir):
        store = AsOfPriorStore.load(small_fixture_dir)  # default restrict_to_max_asof=True
        row_counts = store.health()["row_counts"]
        assert row_counts["model_age_fail_rate_eb"] == 1  # the 2024-01 row was dropped at load time
        assert row_counts["make_fail_rate_smoothed"] == 2  # FORD + TOYOTA at 2025-06 only

    def test_sliced_store_resolves_live_lookups_correctly(self, sliced_store):
        r = sliced_store.lookup(
            "model_age_fail_rate_eb",
            {"make": "FORD", "model_id": "FORD FOCUS", "age_band": "0-3", "mileage_band": "0-30k"},
            date(2026, 1, 1),  # any date at/after max_asof -- the only case prod ever calls with
        )
        assert r == PriorLookup(0.30, "own")

    def test_sliced_store_cannot_see_a_historical_month_by_design(self, sliced_store):
        """Documented trade-off: the reduced mode never loads non-max_asof
        months, so a historical serving_month degrades to backoff/global
        instead of the (unloaded) true historical value. Fine in production
        (serving_month is always "now"); must be TRUE, not accidentally
        correct."""
        r = sliced_store.lookup(
            "model_age_fail_rate_eb",
            {"make": "FORD", "model_id": "FORD FOCUS", "age_band": "0-3", "mileage_band": "0-30k"},
            date(2024, 1, 15),
        )
        assert r == PriorLookup(SMALL_GLOBAL_RATE, "global")


# --------------------------------------------------------------------------- #
# Loading / error handling
# --------------------------------------------------------------------------- #

class TestLoadErrors:
    def test_missing_table_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            AsOfPriorStore.load(tmp_path)

    def test_tables_present_false_on_empty_dir(self, tmp_path):
        assert tables_present(tmp_path) is False

    def test_tables_present_true_after_fixture_build(self, small_fixture_dir):
        assert tables_present(small_fixture_dir) is True

    def test_tables_present_false_if_one_file_missing(self, small_fixture_dir):
        (small_fixture_dir / "make_meta.parquet").unlink()
        assert tables_present(small_fixture_dir) is False

    def test_constructor_raises_on_missing_channel_data(self):
        with pytest.raises(ValueError, match="missing channel data"):
            AsOfPriorStore(data={}, meta={})

    def test_max_asof_disagreement_across_channels_raises(self, small_fixture_dir, tmp_path):
        bad_dir = tmp_path / "bad"
        shutil.copytree(small_fixture_dir, bad_dir)
        bad_meta = pd.DataFrame([{
            "max_asof": pd.to_datetime("2025-05-01").date(),
            "global_rate_at_max_asof": SMALL_GLOBAL_RATE,
        }])
        bad_meta.to_parquet(bad_dir / "make_meta.parquet", engine="pyarrow", index=False)
        # restrict_to_max_asof=False: the make table has no 2025-05 row at
        # all, so a filtered (default) load would raise its OWN "empty
        # slice" error first: use the full-history load to reach the
        # cross-channel max_asof invariant in __init__ specifically.
        with pytest.raises(ValueError, match="disagree on max_asof"):
            AsOfPriorStore.load(bad_dir, restrict_to_max_asof=False)

    def test_empty_max_asof_slice_raises(self, small_fixture_dir):
        meta = pd.DataFrame([{
            "max_asof": pd.to_datetime("2099-01-01").date(),  # no row anywhere has this month
            "global_rate_at_max_asof": SMALL_GLOBAL_RATE,
        }])
        meta.to_parquet(small_fixture_dir / "make_meta.parquet", engine="pyarrow", index=False)
        with pytest.raises(ValueError, match="empty"):
            AsOfPriorStore.load(small_fixture_dir)


def test_health_reports_expected_shape(sliced_store):
    h = sliced_store.health()
    assert h["loaded"] is True
    assert set(h["channels"]) == set(CHANNELS)
    assert h["max_asof"] == SMALL_MAX_ASOF
    assert all(ch in h["row_counts"] for ch in CHANNELS)
    assert all(ch in h["global_rates"] for ch in CHANNELS)


# --------------------------------------------------------------------------- #
# model_v55.asof_priors_state() self-check branching
# --------------------------------------------------------------------------- #

class TestModelV55SelfCheck:
    def test_disabled_when_flag_off(self, monkeypatch):
        monkeypatch.setattr(model_v55, "ASOF_PRIORS_ENABLED", False)
        assert model_v55.asof_priors_state() == {"status": "disabled", "flag": "AUTOSAFE_ASOF_PRIORS"}

    def test_flag_on_but_store_not_loaded_reports_presence_check(self, monkeypatch, tmp_path):
        monkeypatch.setattr(model_v55, "ASOF_PRIORS_ENABLED", True)
        monkeypatch.setattr(model_v55, "_asof_store", None)
        monkeypatch.setattr(model_v55, "DEFAULT_TABLES_DIR", tmp_path)  # empty dir
        state = model_v55.asof_priors_state()
        assert state["status"] == "flag_on_but_not_loaded"
        assert state["tables_present"] is False

    def test_flag_on_with_store_reports_loaded(self, monkeypatch, sliced_store):
        monkeypatch.setattr(model_v55, "ASOF_PRIORS_ENABLED", True)
        monkeypatch.setattr(model_v55, "_asof_store", sliced_store)
        state = model_v55.asof_priors_state()
        assert state["status"] == "loaded"
        assert state["max_asof"] == SMALL_MAX_ASOF


# --------------------------------------------------------------------------- #
# engineer_features() integration: flag-off no-op proof + real wiring
# --------------------------------------------------------------------------- #

class TestEngineerFeaturesIntegration:
    """(a) proves the flag-off no-op guarantee holds even with a real,
    loaded store passed in; (b) proves that when both the flag and the
    store are present, engineer_features correctly canon-normalises
    make/model and computes age_band/mileage_band before calling
    AsOfPriorStore.lookup() -- a genuine end-to-end wiring check, not just
    a unit test of the store in isolation."""

    PREDICTION_DATE = datetime(2025, 7, 15)  # after SMALL_MAX_ASOF -> eff_asof clips to it

    def test_flag_off_is_a_noop_even_with_a_loaded_store(self, monkeypatch, sliced_store):
        monkeypatch.delenv("AUTOSAFE_ASOF_PRIORS", raising=False)
        history = _make_history()
        features = feature_engineering_v55.engineer_features(
            history, "RG1 1AA", prediction_date=self.PREDICTION_DATE, asof_store=sliced_store,
        )
        # Legacy path (no hierarchical dicts passed) -> base_rate defaults,
        # NOT any of the fixture's asof values (0.15-0.30 range).
        for col in ("make_fail_rate_smoothed", "segment_fail_rate_smoothed",
                    "model_fail_rate_smoothed", "make_age_fail_rate_eb",
                    "model_age_fail_rate_eb", "eb_unified_prior"):
            assert features[col] == 0.28, f"{col}: flag-off leaked the asof path (got {features[col]})"

    def test_flag_on_without_a_store_is_still_a_noop(self, monkeypatch):
        monkeypatch.setenv("AUTOSAFE_ASOF_PRIORS", "1")
        history = _make_history()
        features = feature_engineering_v55.engineer_features(
            history, "RG1 1AA", prediction_date=self.PREDICTION_DATE, asof_store=None,
        )
        assert features["make_fail_rate_smoothed"] == 0.28
        assert features["model_age_fail_rate_eb"] == 0.28

    def test_flag_on_with_store_uses_asof_values_for_every_channel(self, monkeypatch, sliced_store):
        monkeypatch.setenv("AUTOSAFE_ASOF_PRIORS", "1")
        # FORD FOCUS, manufacture 2023 -> ~2y old at PREDICTION_DATE -> age_band '0-3'.
        # No mot_tests -> test_mileage=0 -> mileage_band '0-30k'.
        history = _make_history(make="FORD", model="FOCUS", manufacture_year=2023)
        features = feature_engineering_v55.engineer_features(
            history, "RG1 1AA", prediction_date=self.PREDICTION_DATE, asof_store=sliced_store,
        )
        expected_keys = {"make": "FORD", "model_id": "FORD FOCUS", "age_band": "0-3", "mileage_band": "0-30k"}
        for col in ("make_fail_rate_smoothed", "segment_fail_rate_smoothed",
                    "model_fail_rate_smoothed", "make_age_fail_rate_eb",
                    "model_age_fail_rate_eb", "eb_unified_prior"):
            expected = sliced_store.lookup(col, expected_keys, self.PREDICTION_DATE)
            assert features[col] == expected.value, f"{col}: expected {expected}, got {features[col]}"
        # ...and it must not silently equal the legacy default (proves the new path fired).
        assert features["model_age_fail_rate_eb"] == pytest.approx(0.30)
        assert features["model_age_fail_rate_eb"] != 0.28

    def test_canon_normalisation_applied_to_lowercase_raw_dvsa_strings(self, monkeypatch, sliced_store):
        """Regression guard: the legacy code built model_id via a plain
        f-string (f"{make} {model}") with no case/whitespace normalisation.
        Reusing that here would silently mismatch the (canon-built) table
        keys for any raw input that isn't already upper-cased -- this proves
        canon_make/canon_model_id are actually applied on the new path."""
        monkeypatch.setenv("AUTOSAFE_ASOF_PRIORS", "1")
        history = _make_history(make="ford", model="  focus ", manufacture_year=2023)
        features = feature_engineering_v55.engineer_features(
            history, "RG1 1AA", prediction_date=self.PREDICTION_DATE, asof_store=sliced_store,
        )
        canonical_keys = {"make": "FORD", "model_id": "FORD FOCUS", "age_band": "0-3", "mileage_band": "0-30k"}
        expected = sliced_store.lookup("model_age_fail_rate_eb", canonical_keys, self.PREDICTION_DATE)
        assert features["model_age_fail_rate_eb"] == expected.value == pytest.approx(0.30)


# --------------------------------------------------------------------------- #
# GOLDEN CROSS-CHECK
# --------------------------------------------------------------------------- #

class TestGoldenCrossCheck:
    """200 sampled synthetic rows, checked against an INDEPENDENTLY
    re-derived reference computed directly from the same plain Python dicts
    used to write the fixture tables -- never via AsOfPriorStore's own
    _raw()/lookup() internals. Mirrors the independence-boundary design of
    work/goal_0750/prior_rebuild_v1/scripts/golden_row_parity.py's
    compute_reference(): own -> parent's RAW own-cell -> this channel's own
    global, re-derived from the spec rather than imported (that repo is a
    separate git repo, unimportable from the product deploy).
    """

    MAKES = ["FORD", "TOYOTA", "VAUXHALL", "BMW", "AUDI"]
    AGE_BANDS = ["0-3", "3-5", "6-10", "11-15", "15+"]
    MILEAGE_BANDS = ["0-30k", "30k-60k", "60k-100k", "100k+"]
    N_MODELS_PER_MAKE = 4
    MAX_ASOF = "2025-06-01"
    # Fixed, safely after MAX_ASOF -> eff_asof always resolves to MAX_ASOF for
    # every sample. Month-clip arithmetic itself is covered by TestMonthClip;
    # mixing it in here would conflate two independent things to verify.
    SERVING_MONTH = date(2025, 7, 15)
    N_SAMPLES = 200

    @pytest.fixture(scope="class")
    def universe(self):
        """Independent reference dicts (own-grain rates only, keyed exactly
        like the real tables) + a deterministic sparsity pattern so all
        three backoff paths (own/parent/global) get exercised."""
        rng = random.Random(20260704)
        models = {m: [f"{m} MODEL{i}" for i in range(self.N_MODELS_PER_MAKE)] for m in self.MAKES}

        make_ref = {m: round(rng.uniform(0.05, 0.35), 6) for m in self.MAKES}  # 100% coverage

        make_age_ref = {}
        for m in self.MAKES:
            for ab in self.AGE_BANDS:
                if rng.random() < 0.8:  # ~80% coverage -> some make_age global fallback
                    make_age_ref[(m, ab)] = round(rng.uniform(0.05, 0.35), 6)

        segment_ref = {}
        for m in self.MAKES:
            for ab in self.AGE_BANDS:
                for mb in self.MILEAGE_BANDS:
                    if rng.random() < 0.5:  # ~50% coverage -> segment has no parent, tests global hard
                        segment_ref[(m, ab, mb)] = round(rng.uniform(0.05, 0.35), 6)

        model_ref = {}
        for m in self.MAKES:
            for model_id in models[m]:
                if rng.random() < 0.6:  # ~60% coverage -> model->make parent backoff exercised
                    model_ref[model_id] = round(rng.uniform(0.05, 0.35), 6)

        model_age_ref = {}
        for m in self.MAKES:
            for model_id in models[m]:
                for ab in self.AGE_BANDS:
                    if rng.random() < 0.5:  # ~50% coverage -> model_age->make_age parent + global both exercised
                        model_age_ref[(model_id, ab)] = round(rng.uniform(0.05, 0.35), 6)

        globals_ = {ch: 0.19 for ch in CHANNELS}
        return {
            "models": models, "make": make_ref, "make_age": make_age_ref,
            "segment": segment_ref, "model": model_ref, "model_age": model_age_ref,
            "globals": globals_,
        }

    @pytest.fixture(scope="class")
    def store(self, tmp_path_factory, universe):
        out_dir = tmp_path_factory.mktemp("golden_fixture")

        make_rows = [{"asof_month": self.MAX_ASOF, "make": m, "make_rate_long": r}
                     for m, r in universe["make"].items()]
        make_age_rows = [{"asof_month": self.MAX_ASOF, "make": m, "age_band": ab, "make_age_rate_eb": r}
                          for (m, ab), r in universe["make_age"].items()]
        segment_rows = [{"asof_month": self.MAX_ASOF, "make": m, "age_band": ab, "mileage_band": mb,
                          "segment_rate_long": r}
                         for (m, ab, mb), r in universe["segment"].items()]
        model_rows = [{"asof_month": self.MAX_ASOF, "model_id": mid, "model_rate_long": r}
                      for mid, r in universe["model"].items()]
        model_age_rows = [{"asof_month": self.MAX_ASOF, "model_id": mid, "age_band": ab,
                            "model_age_rate_eb": r}
                           for (mid, ab), r in universe["model_age"].items()]

        _write_channel_table(out_dir, "make_fail_rate_smoothed", make_rows,
                              self.MAX_ASOF, universe["globals"]["make_fail_rate_smoothed"])
        _write_channel_table(out_dir, "make_age_fail_rate_eb", make_age_rows,
                              self.MAX_ASOF, universe["globals"]["make_age_fail_rate_eb"])
        _write_channel_table(out_dir, "segment_fail_rate_smoothed", segment_rows,
                              self.MAX_ASOF, universe["globals"]["segment_fail_rate_smoothed"])
        _write_channel_table(out_dir, "model_fail_rate_smoothed", model_rows,
                              self.MAX_ASOF, universe["globals"]["model_fail_rate_smoothed"])
        _write_channel_table(out_dir, "model_age_fail_rate_eb", model_age_rows,
                              self.MAX_ASOF, universe["globals"]["model_age_fail_rate_eb"])

        return AsOfPriorStore.load(out_dir)  # production default

    @staticmethod
    def _reference(universe, make, model_id, age_band, mileage_band):
        """Independent re-derivation, written fresh: own -> parent's RAW
        own-cell -> this channel's own global. Deliberately duplicates
        BACKOFF_PARENT's *values* rather than importing the constant, so a
        bug in asof_priors.BACKOFF_PARENT itself would not silently escape
        detection by being reused on both sides of the comparison."""
        raw = {
            "make_fail_rate_smoothed": universe["make"].get(make),
            "segment_fail_rate_smoothed": universe["segment"].get((make, age_band, mileage_band)),
            "model_fail_rate_smoothed": universe["model"].get(model_id),
            "make_age_fail_rate_eb": universe["make_age"].get((make, age_band)),
            "model_age_fail_rate_eb": universe["model_age"].get((model_id, age_band)),
        }
        parent_map = {
            "model_age_fail_rate_eb": "make_age_fail_rate_eb",
            "model_fail_rate_smoothed": "make_fail_rate_smoothed",
        }
        out = {}
        for ch in CHANNELS:
            if raw[ch] is not None:
                out[ch] = (raw[ch], "own")
            else:
                parent = parent_map.get(ch)
                if parent is not None and raw[parent] is not None:
                    out[ch] = (raw[parent], "parent")
                else:
                    out[ch] = (universe["globals"][ch], "global")
        out["eb_unified_prior"] = out["model_age_fail_rate_eb"]
        return out

    def test_200_sampled_rows_match_independent_reference(self, store, universe):
        rng = random.Random(999)  # independent seed from fixture-building
        samples = []
        for _ in range(self.N_SAMPLES):
            make = rng.choice(self.MAKES)
            model_id = rng.choice(universe["models"][make])
            age_band = rng.choice(self.AGE_BANDS)
            mileage_band = rng.choice(self.MILEAGE_BANDS)
            samples.append((make, model_id, age_band, mileage_band))

        mismatches = []
        source_counts = {ch: {"own": 0, "parent": 0, "global": 0} for ch in ALL_COLUMNS}
        for make, model_id, age_band, mileage_band in samples:
            keys = {"make": make, "model_id": model_id, "age_band": age_band, "mileage_band": mileage_band}
            ref = self._reference(universe, make, model_id, age_band, mileage_band)
            for ch in ALL_COLUMNS:
                got = store.lookup(ch, keys, self.SERVING_MONTH)
                ref_val, ref_src = ref[ch]
                source_counts[ch][got.source] += 1
                if got.value != ref_val or got.source != ref_src:
                    mismatches.append({
                        "channel": ch, "keys": keys,
                        "got": (got.value, got.source), "expected": (ref_val, ref_src),
                    })

        assert not mismatches, (
            f"{len(mismatches)}/{self.N_SAMPLES * len(ALL_COLUMNS)} golden-cross-check "
            f"mismatches (first 5 shown): {mismatches[:5]}"
        )

        # Coverage sanity: the sparsity pattern must actually exercise every
        # backoff path at n=200, or this would be a vacuous pass (e.g. if it
        # accidentally degenerated to all-global). Deterministic seeds make
        # this non-flaky once it passes.
        assert source_counts["model_age_fail_rate_eb"]["own"] > 0
        assert source_counts["model_age_fail_rate_eb"]["parent"] > 0
        assert source_counts["model_age_fail_rate_eb"]["global"] > 0
        assert source_counts["model_fail_rate_smoothed"]["own"] > 0
        assert source_counts["model_fail_rate_smoothed"]["parent"] > 0
        assert source_counts["segment_fail_rate_smoothed"]["own"] > 0
        assert source_counts["segment_fail_rate_smoothed"]["global"] > 0
        assert source_counts["make_age_fail_rate_eb"]["own"] > 0
        assert source_counts["make_age_fail_rate_eb"]["global"] > 0
        # make itself has 100% coverage in this universe by design -> always "own".
        assert source_counts["make_fail_rate_smoothed"]["own"] == self.N_SAMPLES


# --------------------------------------------------------------------------- #
# Band-vocabulary train/serve guard (RT-SERVING caveat 1 / S3 / S5)
#
# The single highest-risk GF-17 surface: the age_band / mileage_band strings
# the serving code emits must be EXACTLY the band vocabulary the shipped v2
# prior tables were built with. A silent drift -- a get_age_band/get_mileage_band
# edit, a renamed bucket, or a table rebuilt under a new band scheme -- would
# mis-key every affected lookup into a dict-miss and a wrong global fallback:
# exactly the CatBoost-era vocab-shim failure. asof_priors.py's lookup()
# docstring claims this agreement is "pinned by
# tests/test_asof_priors.py::test_band_vocabulary_matches_reference"; before
# this file that test did not exist. This is that pin.
# --------------------------------------------------------------------------- #

def _shipped_band_vocab(band_col: str) -> set:
    """Distinct values of `band_col` across every SHIPPED prior parquet whose
    own-grain key includes it -- read straight from the real
    models/asof_priors/*.parquet artifacts production loads (via
    asof_priors.DEFAULT_TABLES_DIR), independent of AsOfPriorStore itself. Uses
    this file's own `_STEM` (deliberately re-typed, not imported from
    asof_priors._TABLE_SPEC) to discover which tables key on the band."""
    vocab: set = set()
    for stem, key_cols, _rate_col in _STEM.values():
        if band_col in key_cols:
            path = DEFAULT_TABLES_DIR / f"{stem}_fail_rate_asof.parquet"
            col = pd.read_parquet(path, columns=[band_col])[band_col]
            vocab.update(col.dropna().unique().tolist())
    return vocab


def test_band_vocabulary_matches_reference():
    """Every age_band / mileage_band the serving code can emit -- driven through
    the REAL feature_engineering_v55.get_age_band()/get_mileage_band() at their
    bucket boundaries -- must be EXACTLY a member of the shipped tables' band
    vocabulary, and together cover it with no drift on either side."""
    shipped_age = _shipped_band_vocab("age_band")
    shipped_mileage = _shipped_band_vocab("mileage_band")
    # Never let a failure to read the vocab masquerade as a vacuous pass.
    assert shipped_age, "no age_band vocabulary found in the shipped prior tables"
    assert shipped_mileage, "no mileage_band vocabulary found in the shipped prior tables"

    # Age boundaries: 0/3 -> '0-3', 4/5 -> '3-5', 10 -> '6-10', 15 -> '11-15',
    # 16 -> '15+'. 5 is also the NULL-first-use-date default engineer_features
    # falls back to (vehicle_age_for_cohort = 5 when manufacture_date is None).
    age_inputs = [0, 3, 4, 5, 10, 15, 16]
    emitted_age = {feature_engineering_v55.get_age_band(a) for a in age_inputs}
    for a in age_inputs:
        band = feature_engineering_v55.get_age_band(a)
        assert band in shipped_age, (
            f"serving age_band {band!r} (age={a}) absent from shipped vocab {sorted(shipped_age)}"
        )

    # Mileage boundaries: 0/29999 -> '0-30k', 30000/59999 -> '30k-60k',
    # 60000/99999 -> '60k-100k', 100000 -> '100k+'. 0 is also the
    # missing-mileage default (features.get('test_mileage', 0) with no tests).
    mileage_inputs = [0, 29999, 30000, 59999, 60000, 99999, 100000]
    emitted_mileage = {feature_engineering_v55.get_mileage_band(m) for m in mileage_inputs}
    for m in mileage_inputs:
        band = feature_engineering_v55.get_mileage_band(m)
        assert band in shipped_mileage, (
            f"serving mileage_band {band!r} (mileage={m}) absent from shipped vocab {sorted(shipped_mileage)}"
        )

    # Exact agreement in BOTH directions: the boundary inputs above emit every
    # band the serving code can produce, so set-equality also catches a table
    # rebuilt with a band the serving code can no longer key (e.g. a split of
    # '15+' into '15-20'/'20+'), not merely a serving-side drift.
    assert emitted_age == shipped_age, (
        f"age_band vocab drift: serving emits {sorted(emitted_age)}, "
        f"shipped tables have {sorted(shipped_age)}"
    )
    assert emitted_mileage == shipped_mileage, (
        f"mileage_band vocab drift: serving emits {sorted(emitted_mileage)}, "
        f"shipped tables have {sorted(shipped_mileage)}"
    )


class _BandCapturingStore:
    """Duck-typed AsOfPriorStore stand-in that records the age_band /
    mileage_band engineer_features() actually passes into lookup() on the
    flag-ON serving path -- so the assertion is against the REAL emitted band
    strings (get_age_band()/get_mileage_band() via the real engineer_features
    wiring, incl. the NULL-fud and missing-mileage defaulting branches), not a
    re-implementation. Value/source are irrelevant here; only the keys matter."""

    def __init__(self):
        self.age_bands: set = set()
        self.mileage_bands: set = set()

    def lookup(self, channel, keys, serving_month):
        if keys.get("age_band") is not None:
            self.age_bands.add(keys["age_band"])
        if keys.get("mileage_band") is not None:
            self.mileage_bands.add(keys["mileage_band"])
        return PriorLookup(0.19, "global")


def _null_fud_history() -> VehicleHistory:
    """History with NO manufacture_date -> engineer_features defaults
    vehicle_age_for_cohort = 5 -> age_band '3-5'."""
    return VehicleHistory(
        registration="NOFUD1", make="FORD", model="FOCUS", fuel_type="PE",
        colour="BLUE", registration_date=datetime(2020, 6, 1),
        manufacture_date=None, engine_size=1600, mot_tests=[],
    )


def test_serving_path_emits_only_shipped_bands(monkeypatch):
    """End-to-end companion to test_band_vocabulary_matches_reference: drive the
    REAL flag-ON engineer_features() wiring -- including the NULL-first-use-date
    and missing-mileage defaulting branches the boundary-function test can't
    reach -- and assert every band it feeds into AsOfPriorStore.lookup() is in
    the shipped vocabulary."""
    monkeypatch.setenv("AUTOSAFE_ASOF_PRIORS", "1")
    shipped_age = _shipped_band_vocab("age_band")
    shipped_mileage = _shipped_band_vocab("mileage_band")
    spy = _BandCapturingStore()

    prediction_date = datetime(2025, 7, 15)
    histories = [
        _make_history(make="FORD", model="FOCUS", manufacture_year=2023),   # ~2y -> '0-3'; no tests -> '0-30k'
        _make_history(make="BMW", model="3 SERIES", manufacture_year=2008),  # ~17y -> '15+'
        _null_fud_history(),                                                 # NULL fud -> age default 5 -> '3-5'
    ]
    for h in histories:
        feature_engineering_v55.engineer_features(
            h, "RG1 1AA", prediction_date=prediction_date, asof_store=spy,
        )

    assert spy.age_bands, "spy captured no age_band -- the flag-ON serving path did not fire"
    assert spy.mileage_bands, "spy captured no mileage_band -- the flag-ON serving path did not fire"
    assert spy.age_bands <= shipped_age, (
        f"serving emitted age_band(s) {sorted(spy.age_bands - shipped_age)} absent from shipped vocab"
    )
    assert spy.mileage_bands <= shipped_mileage, (
        f"serving emitted mileage_band(s) {sorted(spy.mileage_bands - shipped_mileage)} absent from shipped vocab"
    )
    # The two defaulting branches specifically resolve into the shipped vocab.
    assert "3-5" in spy.age_bands       # NULL-fud default (age 5)
    assert "0-30k" in spy.mileage_bands  # missing-mileage default (0)


# --------------------------------------------------------------------------- #
# Active staleness signal (RT-SERVING Q1 / caveat 4): stale_months on /health
# + a loud load-time WARNING once tables age past two publication cycles.
# --------------------------------------------------------------------------- #

def _build_uniform_fixture(out_dir: Path, max_asof: str) -> None:
    """Minimal one-row-per-channel fixture at an arbitrary max_asof, so the
    staleness arithmetic can be exercised at a chosen age relative to today."""
    _write_channel_table(out_dir, "make_fail_rate_smoothed",
                         [{"asof_month": max_asof, "make": "FORD", "make_rate_long": 0.20}], max_asof, 0.19)
    _write_channel_table(out_dir, "segment_fail_rate_smoothed",
                         [{"asof_month": max_asof, "make": "FORD", "age_band": "0-3",
                           "mileage_band": "0-30k", "segment_rate_long": 0.15}], max_asof, 0.19)
    _write_channel_table(out_dir, "model_fail_rate_smoothed",
                         [{"asof_month": max_asof, "model_id": "FORD FOCUS", "model_rate_long": 0.25}], max_asof, 0.19)
    _write_channel_table(out_dir, "make_age_fail_rate_eb",
                         [{"asof_month": max_asof, "make": "FORD", "age_band": "0-3", "make_age_rate_eb": 0.21}],
                         max_asof, 0.19)
    _write_channel_table(out_dir, "model_age_fail_rate_eb",
                         [{"asof_month": max_asof, "model_id": "FORD FOCUS", "age_band": "0-3",
                           "model_age_rate_eb": 0.30}], max_asof, 0.19)


class TestStaleness:
    def test_months_between_whole_calendar_months(self):
        assert _months_between(date(2025, 6, 1), date(2026, 7, 4)) == 13
        assert _months_between(date(2025, 6, 1), date(2025, 6, 30)) == 0  # day ignored
        assert _months_between(date(2025, 6, 1), date(2025, 5, 1)) == -1  # future max_asof

    def test_health_exposes_live_stale_months(self, sliced_store):
        h = sliced_store.health()
        assert "stale_months" in h
        # Live against today, self-consistent with the same helper (max_asof 2025-06-01).
        assert h["stale_months"] == _months_between(date(2025, 6, 1), date.today())

    def test_stale_tables_warn_loudly_at_load(self, tmp_path, caplog):
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        _build_uniform_fixture(old_dir, "2020-01-01")  # ~decade stale from any plausible 'today'
        with caplog.at_level(logging.WARNING):
            store = AsOfPriorStore.load(old_dir)
        assert store.health()["stale_months"] > STALE_MONTHS_WARN_THRESHOLD
        assert any("STALE" in r.getMessage() and r.levelname == "WARNING" for r in caplog.records), (
            "expected a loud staleness WARNING at load for decade-old tables"
        )

    def test_fresh_tables_do_not_warn_at_load(self, tmp_path, caplog):
        fresh_dir = tmp_path / "fresh"
        fresh_dir.mkdir()
        this_month = date.today().replace(day=1).isoformat()
        _build_uniform_fixture(fresh_dir, this_month)
        with caplog.at_level(logging.WARNING):
            store = AsOfPriorStore.load(fresh_dir)
        assert store.health()["stale_months"] == 0
        assert not any("STALE" in r.getMessage() for r in caplog.records), (
            "a current-month store must not raise the staleness warning"
        )
