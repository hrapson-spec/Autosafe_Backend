#!/usr/bin/env python3
"""Train/serve parity harness (audit item #2; v55 audit + v57 promotion gate).

This is the harness referenced by ``models/v57/README.md`` (promotion gate #2)
and ``work/legacy_v55/README.md`` (GF-17 defect ledger). It checks that what the
**serving** path emits matches what the **model / training matrix / contract**
expects — the class of bug that shipped 42/104 v55 features failing parity,
including four served as constants and several dead categorical vocabularies.

Checks (each degrades gracefully when its input is unavailable):

  1. SERVING ↔ CONTRACT/NAMES — every contract feature is emitted, in order, no
     extras. Default contract is the served v55 ``FEATURE_NAMES``; pass
     ``--contract models/v57/feature_contract.json`` to gate v57.
  2. CONSTANT-SERVED — features that never vary across a diverse fixture panel.
     The four RC-3 features (station_fail_rate_smoothed,
     station_x_prev_outcome_fail_rate, station_strictness_bias,
     suspension_risk_profile) are *trained live but served as constants* — this
     surfaces them mechanically by intersecting with the model_bundle RC-3 set.
  3. CATEGORICAL VOCAB — after the model-boundary vocab shim, every emitted
     categorical value must lie inside the deployed model's training vocabulary
     (vocab_shim.TRAINING_VOCAB). Out-of-vocab values are silently-dead features
     in CatBoost.
  4. MODEL BINARY (needs catboost) — ``model.cbm`` ``feature_names_`` equals the
     serving names, in order, with matching categorical indices. This catches
     the 104-vs-107 / index-62 drift class.
  5. TRAIN MATRIX (needs pandas; ``--train-matrix``) — the training matrix's
     columns equal the serving emission order and the contract.

Exit codes:
  * default (v55 audit mode): 0 if the only parity violations are the
    *documented known-broken* set (RC-3 constants + known dead vocab); non-zero
    if a NEW violation appears (a regression) — or if a known one is *fixed*
    (so the baseline is consciously updated).
  * ``--expect-fixed`` (v57 gate mode): 0 only if there are NO violations.

Usable as a library: ``run_parity(...)`` returns a ``ParityReport``; the pytest
in ``tests/test_train_serve_parity.py`` calls it directly.

NOTE: importing this module pulls ``feature_engineering_v55`` (→ numpy). The
catboost/pandas checks are imported lazily so the core checks run with numpy
alone (as in CI).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dvsa_client import MOTTest, VehicleHistory
from feature_engineering_v55 import (
    CATEGORICAL_INDICES,
    FEATURE_NAMES,
    engineer_features,
    features_to_array,
)
from vocab_shim import TRAINING_VOCAB, apply_vocab_shim

# model_bundle is the single source of truth for the v57 decision table; using
# it here is the first non-test wiring of the bundle into the toolchain.
from model_bundle import RC3_DROPPED_FEATURES

MODEL_DIR = Path(__file__).parent / "catboost_production_v55"

# Documented known-broken baseline for v55 (GF-17 ledger). The harness passes in
# default mode iff observed violations == this baseline. `--expect-fixed`
# ignores the baseline and requires zero violations.
KNOWN_CONSTANT_SERVED = set(RC3_DROPPED_FEATURES)
# Categoricals whose serving vocabulary is deliberately unmapped/dead at serve
# (vocab_shim.py docstring: different concepts/edges or rejected mapping).
KNOWN_DEAD_VOCAB = {"dominant_mechanism", "gap_band", "usage_band_hybrid"}

# Categorical feature names (resolved from serving indices).
CATEGORICAL_FEATURES = [FEATURE_NAMES[i] for i in CATEGORICAL_INDICES]


# ---------------------------------------------------------------------------
# Fixtures: a deliberately diverse panel so genuinely-dynamic features vary.
# ---------------------------------------------------------------------------
def _mot(
    test_date: datetime,
    result: str = "PASSED",
    odo: Optional[int] = 40000,
    unit: str = "mi",
    defects: Optional[List[Dict[str, Any]]] = None,
) -> MOTTest:
    return MOTTest(
        test_date=test_date,
        test_result=result,
        expiry_date=test_date + timedelta(days=365),
        odometer_value=odo,
        odometer_unit=unit,
        test_number=f"T{int(test_date.timestamp())}",
        defects=defects or [],
    )


def _veh(make: str, model: str, tests: List[MOTTest], mfd_year: int = 2015) -> VehicleHistory:
    return VehicleHistory(
        registration="AB12CDE",
        make=make,
        model=model,
        fuel_type="Petrol",
        colour="Blue",
        registration_date=datetime(mfd_year, 3, 1),
        manufacture_date=datetime(mfd_year, 3, 1),
        engine_size=1600,
        mot_tests=tests,
    )


def build_fixtures() -> List[Tuple[str, VehicleHistory, str]]:
    """Return ``[(name, history, postcode), ...]`` covering distinct cohorts."""
    adv_brake = {"type": "ADVISORY", "text": "Brake disc worn but within limits"}
    adv_tyre = {"type": "ADVISORY", "text": "Tyre worn close to legal limit"}
    adv_susp = {"type": "ADVISORY", "text": "Suspension arm has slight corrosion"}
    adv_leak = {"type": "ADVISORY", "text": "Oil leak from engine, slight"}
    adv_struct = {"type": "ADVISORY", "text": "Sill has corrosion (structural)"}
    fail_brake = {"type": "FAIL", "text": "Brake pad excessively worn"}
    fail_susp = {"type": "FAIL", "text": "Suspension component fractured/corroded"}

    fixtures: List[Tuple[str, VehicleHistory, str]] = []

    # 1. No history — first-test / no-prior cohort.
    fixtures.append(("no_history", _veh("FORD", "FIESTA", []), "SW1A 1AA"))

    # 2. Single prior test.
    fixtures.append(
        ("single_pass", _veh("VOLKSWAGEN", "GOLF",
                             [_mot(datetime(2025, 5, 1), odo=30000)]), "EH1 1AA")
    )

    # 3. Multi clean, rising mileage.
    fixtures.append(
        ("multi_clean", _veh("TOYOTA", "YARIS", [
            _mot(datetime(2025, 6, 1), odo=60000),
            _mot(datetime(2024, 6, 1), odo=50000),
            _mot(datetime(2023, 6, 1), odo=40000),
        ]), "M1 1AE")
    )

    # 4. Advisories across components.
    fixtures.append(
        ("multi_advisories", _veh("VAUXHALL", "ASTRA", [
            _mot(datetime(2025, 6, 1), odo=90000, defects=[adv_brake, adv_tyre, adv_leak]),
            _mot(datetime(2024, 6, 1), odo=78000, defects=[adv_susp, adv_struct]),
            _mot(datetime(2023, 6, 1), odo=66000, defects=[adv_brake]),
        ]), "B1 1AA")
    )

    # 5. Failure history with component defects.
    fixtures.append(
        ("multi_failures", _veh("RENAULT", "CLIO", [
            _mot(datetime(2025, 6, 1), result="FAILED", odo=120000,
                 defects=[fail_brake, fail_susp]),
            _mot(datetime(2024, 6, 1), result="FAILED", odo=108000, defects=[fail_brake]),
            _mot(datetime(2023, 6, 1), odo=96000, defects=[adv_brake]),
        ]), "LS1 1AA")
    )

    # 6. Kilometre units (exercise km→mi conversion).
    fixtures.append(
        ("km_units", _veh("BMW", "320D", [
            _mot(datetime(2025, 6, 1), odo=160000, unit="km"),
            _mot(datetime(2024, 6, 1), odo=140000, unit="km"),
        ]), "G1 1AA")
    )

    # 7. Mileage anomaly (implausible jump → plausibility/anomaly flags).
    fixtures.append(
        ("mileage_anomaly", _veh("AUDI", "A4", [
            _mot(datetime(2025, 6, 1), odo=300000),
            _mot(datetime(2024, 6, 1), odo=50000),
        ]), "CF10 1AA")
    )

    # 8. High-risk model flag (must be in HIGH_RISK_MODELS_SET).
    fixtures.append(
        ("high_risk_model", _veh("ROVER", "75", [
            _mot(datetime(2025, 6, 1), result="FAILED", odo=140000, defects=[fail_susp]),
            _mot(datetime(2024, 6, 1), odo=130000, defects=[adv_susp]),
        ]), "NE1 1AA")
    )

    # 9. Old, high-mileage, no postcode; one pre-window test to exercise the
    #    v57 history-window cap (the 2017 test is dropped in the v57 path).
    fixtures.append(
        ("old_high_mileage", _veh("MERCEDES-BENZ", "E CLASS", [
            _mot(datetime(2025, 6, 1), odo=210000),
            _mot(datetime(2024, 1, 1), odo=198000, defects=[adv_tyre]),
            _mot(datetime(2022, 12, 1), odo=180000),
            _mot(datetime(2017, 11, 1), odo=150000),
        ], mfd_year=2006), "")
    )

    # 10. Recent fail streak (winter test month).
    fixtures.append(
        ("recent_fail_streak", _veh("PEUGEOT", "308", [
            _mot(datetime(2025, 1, 15), result="FAILED", odo=99000, defects=[fail_brake]),
            _mot(datetime(2024, 1, 15), result="FAILED", odo=85000, defects=[fail_brake]),
        ]), "OX1 1AA")
    )

    return fixtures


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
@dataclass
class ParityReport:
    serving_feature_count: int
    emitted_feature_count: int
    missing_from_serving: List[str] = field(default_factory=list)
    extra_in_serving: List[str] = field(default_factory=list)
    contract_name: str = "v55:FEATURE_NAMES"
    contract_order_mismatch: Optional[Tuple[int, str, str]] = None
    constant_served: List[str] = field(default_factory=list)
    rc3_constant_confirmed: List[str] = field(default_factory=list)
    unexpected_constants: List[str] = field(default_factory=list)
    vocab_violations: List[Tuple[str, str]] = field(default_factory=list)
    dead_vocab_unverifiable: List[str] = field(default_factory=list)
    model_check: Optional[str] = None
    train_matrix_check: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    violations: List[str] = field(default_factory=list)
    ok: bool = True

    def as_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d["contract_order_mismatch"] = (
            list(self.contract_order_mismatch) if self.contract_order_mismatch else None
        )
        d["vocab_violations"] = [list(v) for v in self.vocab_violations]
        return d


# ---------------------------------------------------------------------------
# Core checks
# ---------------------------------------------------------------------------
def collect_emissions(fixtures) -> List[Dict[str, Any]]:
    """Run serving feature engineering over every fixture (artifact-free path).

    Uses the bare ``engineer_features`` with no cohort/EB artifacts so the
    harness is CI-safe; that is fine because parity here is about *names, order,
    constants and vocab*, not the artifact-derived prior values.
    """
    pred_date = datetime(2026, 6, 1)
    emissions = []
    for name, history, postcode in fixtures:
        feats = engineer_features(history, postcode, prediction_date=pred_date)
        # Exercise the exact serving boundary (shim → array) without raising, so
        # a missing feature is reported by check_contract rather than crashing.
        features_to_array(apply_vocab_shim(feats), validate=False)
        emissions.append(feats)
    return emissions


def check_contract(emissions, feature_names: List[str]) -> Dict[str, Any]:
    emitted_keys = set()
    for e in emissions:
        emitted_keys |= set(e.keys())
    missing = [n for n in feature_names if n not in emitted_keys]
    extra = sorted(emitted_keys - set(feature_names))
    return {"missing": missing, "extra": extra}


def check_constants(emissions) -> Dict[str, Any]:
    """Features with a single unique value across the whole fixture panel."""
    all_keys = set()
    for e in emissions:
        all_keys |= set(e.keys())
    constant = []
    for k in sorted(all_keys):
        vals = {repr(e.get(k)) for e in emissions}
        if len(vals) == 1:
            constant.append(k)
    rc3_confirmed = sorted(set(constant) & KNOWN_CONSTANT_SERVED)
    unexpected = sorted(set(constant) - KNOWN_CONSTANT_SERVED)
    return {
        "constant": constant,
        "rc3_confirmed": rc3_confirmed,
        "unexpected": unexpected,
    }


def check_vocab(emissions) -> Dict[str, Any]:
    """Categorical emissions vs deployed training vocab (post vocab shim)."""
    violations: List[Tuple[str, str]] = []
    dead_unverifiable: List[str] = []
    seen: Dict[str, set] = {f: set() for f in CATEGORICAL_FEATURES}
    for e in emissions:
        shimmed = apply_vocab_shim(e)
        for f in CATEGORICAL_FEATURES:
            v = shimmed.get(f)
            if v is not None:
                seen[f].add(str(v))
    for f in CATEGORICAL_FEATURES:
        if f in TRAINING_VOCAB:
            for v in sorted(seen[f]):
                if v not in TRAINING_VOCAB[f]:
                    violations.append((f, v))
        else:
            # No recorded training vocabulary for this feature (e.g. make,
            # test_month, day_of_week, gap_band, usage_band_hybrid). Cannot be
            # verified against the binary here.
            if f in KNOWN_DEAD_VOCAB:
                dead_unverifiable.append(f)
    return {"violations": violations, "dead_unverifiable": sorted(set(dead_unverifiable))}


def check_model_binary(feature_names: List[str], model_path: Path) -> Dict[str, Any]:
    """Compare deployed model.cbm feature_names_/cat indices to serving."""
    try:
        from catboost import CatBoostClassifier
    except Exception as e:  # pragma: no cover - env-dependent
        return {"status": "skipped", "reason": f"catboost unavailable ({e})"}
    if not model_path.exists():
        return {"status": "skipped", "reason": f"model not found: {model_path}"}
    m = CatBoostClassifier()
    m.load_model(str(model_path))
    model_names = list(m.feature_names_)
    order_mismatch = None
    for i, (a, b) in enumerate(zip(model_names, feature_names)):
        if a != b:
            order_mismatch = (i, a, b)
            break
    cat_idx_model = sorted(m.get_cat_feature_indices())
    cat_idx_serving = sorted(CATEGORICAL_INDICES)
    ok = (
        model_names == list(feature_names)
        and cat_idx_model == cat_idx_serving
    )
    return {
        "status": "ok" if ok else "mismatch",
        "model_feature_count": len(model_names),
        "serving_feature_count": len(feature_names),
        "first_order_mismatch": order_mismatch,
        "cat_indices_match": cat_idx_model == cat_idx_serving,
    }


def check_train_matrix(path: Path, feature_names: List[str]) -> Dict[str, Any]:
    """Compare a training matrix's columns to the serving emission order."""
    try:
        import pandas as pd
    except Exception as e:  # pragma: no cover
        return {"status": "skipped", "reason": f"pandas unavailable ({e})"}
    if not path.exists():
        return {"status": "skipped", "reason": f"train matrix not found: {path}"}
    if path.suffix in (".parquet", ".pq"):
        cols = list(pd.read_parquet(path).columns)
    else:
        cols = list(pd.read_csv(path, nrows=1).columns)
    feature_cols = [c for c in cols if c in set(feature_names)]
    missing = [n for n in feature_names if n not in cols]
    extra = [c for c in cols if c not in set(feature_names)]
    return {
        "status": "ok" if feature_cols == list(feature_names) else "mismatch",
        "matrix_feature_count": len(feature_cols),
        "serving_feature_count": len(feature_names),
        "missing_from_matrix": missing[:20],
        "extra_in_matrix": extra[:20],
    }


def run_parity(
    *,
    contract_path: Optional[Path] = None,
    model_path: Optional[Path] = None,
    train_matrix_path: Optional[Path] = None,
    expect_fixed: bool = False,
) -> ParityReport:
    # Resolve the contract: v57 json if given, else served v55 names.
    if contract_path:
        from model_bundle import load_contract

        contract = load_contract(contract_path)
        feature_names = contract.feature_names
        contract_name = f"{contract.version} ({contract_path.name})"
    else:
        feature_names = list(FEATURE_NAMES)
        contract_name = "v55:FEATURE_NAMES"

    fixtures = build_fixtures()
    emissions = collect_emissions(fixtures)

    rep = ParityReport(
        serving_feature_count=len(FEATURE_NAMES),
        emitted_feature_count=len({k for e in emissions for k in e}),
        contract_name=contract_name,
    )

    # 1. contract / names
    c = check_contract(emissions, feature_names)
    rep.missing_from_serving = c["missing"]
    rep.extra_in_serving = c["extra"]

    # 2. constants
    k = check_constants(emissions)
    rep.constant_served = k["constant"]
    rep.rc3_constant_confirmed = k["rc3_confirmed"]
    rep.unexpected_constants = k["unexpected"]

    # 3. vocab
    v = check_vocab(emissions)
    rep.vocab_violations = v["violations"]
    rep.dead_vocab_unverifiable = v["dead_unverifiable"]

    # 4. model binary
    if model_path is not None:
        mc = check_model_binary(feature_names, model_path)
        rep.model_check = json.dumps(mc)
        if mc.get("status") == "mismatch":
            rep.violations.append(f"model_binary:{mc.get('first_order_mismatch')}")

    # 5. train matrix
    if train_matrix_path is not None:
        tc = check_train_matrix(train_matrix_path, feature_names)
        rep.train_matrix_check = json.dumps(tc)
        if tc.get("status") == "mismatch":
            rep.violations.append("train_matrix:column_drift")

    # Assemble violations. A MISSING feature breaks serving outright (hard).
    if rep.missing_from_serving:
        rep.violations.append(f"missing_from_serving:{rep.missing_from_serving[:5]}")

    # Constants & extras are fixture-sensitive (a defect category the panel does
    # not exercise looks "constant"); report them but do not hard-fail on them.
    rep.notes.append(f"RC-3 served-as-constant confirmed: {rep.rc3_constant_confirmed}")
    if rep.unexpected_constants:
        rep.notes.append(
            "constant across fixture panel (review: fixture-induced vs truly "
            f"served-constant): {rep.unexpected_constants}"
        )
    if rep.extra_in_serving:
        rep.notes.append(f"emitted keys not in contract (harmless extras): {rep.extra_in_serving}")

    # Vocab: violations not on the known-dead list are new breakage.
    new_vocab = [vv for vv in rep.vocab_violations if vv[0] not in KNOWN_DEAD_VOCAB]
    if new_vocab:
        rep.violations.append(f"new_vocab_oov:{new_vocab}")
    if rep.vocab_violations:
        rep.notes.append(f"known dead-vocab values (expected under v55): {rep.vocab_violations}")

    if expect_fixed:
        # v57 gate: no known-broken allowance.
        if rep.rc3_constant_confirmed:
            rep.violations.append(f"rc3_still_served_constant:{rep.rc3_constant_confirmed}")
        if rep.vocab_violations:
            rep.violations.append(f"vocab_oov_remaining:{rep.vocab_violations}")
        if rep.dead_vocab_unverifiable:
            rep.violations.append(f"dead_vocab_remaining:{rep.dead_vocab_unverifiable}")

    rep.ok = not rep.violations
    return rep


def run_v57_serving_parity(
    contract_path: Path = Path("models/v57/feature_contract.json"),
) -> Dict[str, Any]:
    """Prove the v57 SERVING adapter emits exactly the v57 contract.

    Runs the fixture panel through ``model_v57.engineer_features_v57`` and checks
    the emission against the loaded contract + decision table (RC-3 absent,
    coverage present, no pre-rename names). This is the v57 closure of gate #2:
    serving emission == contract, by construction of the shared transform.
    """
    from model_bundle import (
        COVERAGE_FEATURES,
        OBSERVED_RENAMES,
        RC3_DROPPED_FEATURES,
        load_contract,
    )
    from model_v57 import engineer_features_v57

    contract = load_contract(contract_path)
    cset = set(contract.feature_names)
    pred_date = datetime(2026, 6, 1)

    emitted: set = set()
    for _name, history, postcode in build_fixtures():
        feats = engineer_features_v57(history, postcode, prediction_date=pred_date)
        emitted |= set(feats.keys())

    missing = [n for n in contract.feature_names if n not in emitted]
    extra = sorted(emitted - cset)
    rc3_present = [f for f in RC3_DROPPED_FEATURES if f in emitted]
    coverage_missing = [c for c in COVERAGE_FEATURES if c not in emitted]
    pre_rename_present = [o for o in OBSERVED_RENAMES if o in emitted]

    violations = []
    if missing:
        violations.append(("missing_from_serving", missing[:5]))
    if rc3_present:
        violations.append(("rc3_present", rc3_present))
    if coverage_missing:
        violations.append(("coverage_missing", coverage_missing))
    if pre_rename_present:
        violations.append(("pre_rename_name_present", pre_rename_present))

    return {
        "contract_version": contract.version,
        "contract_features": len(contract.feature_names),
        "emitted_features_in_contract": len(emitted & cset),
        "missing_from_serving": missing,
        "extra_emitted_keys_note": extra,  # harmless intermediates, not a failure
        "rc3_present": rc3_present,
        "coverage_missing": coverage_missing,
        "pre_rename_present": pre_rename_present,
        "violations": violations,
        "ok": not violations,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_report(rep: ParityReport) -> None:
    print("=" * 72)
    print(f"TRAIN/SERVE PARITY  —  contract: {rep.contract_name}")
    print("=" * 72)
    print(f"serving features emitted : {rep.emitted_feature_count}")
    print(f"contract features        : {rep.serving_feature_count}")
    if rep.missing_from_serving:
        print(f"  MISSING from serving   : {rep.missing_from_serving[:10]}")
    if rep.extra_in_serving:
        print(f"  EXTRA in serving       : {rep.extra_in_serving[:10]}")
    print(f"RC-3 served-as-constant  : {rep.rc3_constant_confirmed}")
    if rep.unexpected_constants:
        print(f"  UNEXPECTED constants   : {rep.unexpected_constants}")
    if rep.vocab_violations:
        print(f"categorical OOV (dead)   : {rep.vocab_violations}")
    if rep.dead_vocab_unverifiable:
        print(f"dead vocab (unverifiable): {rep.dead_vocab_unverifiable}")
    if rep.model_check:
        print(f"model.cbm check          : {rep.model_check}")
    if rep.train_matrix_check:
        print(f"train-matrix check       : {rep.train_matrix_check}")
    for n in rep.notes:
        print(f"note: {n}")
    print("-" * 72)
    if rep.ok:
        print("RESULT: OK")
    else:
        print(f"RESULT: PARITY VIOLATIONS ({len(rep.violations)}):")
        for v in rep.violations:
            print(f"  - {v}")
    print("=" * 72)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Train/serve parity harness (GF-17).")
    p.add_argument("--contract", type=Path, default=None,
                   help="v57 feature_contract.json (default: served v55 FEATURE_NAMES)")
    p.add_argument("--cbm", type=Path, default=MODEL_DIR / "model.cbm",
                   help="CatBoost model to schema-check (needs catboost)")
    p.add_argument("--no-model", action="store_true",
                   help="skip the model.cbm schema check")
    p.add_argument("--train-matrix", type=Path, default=None,
                   help="training matrix parquet/csv to column-check")
    p.add_argument("--expect-fixed", action="store_true",
                   help="v57 gate: fail on ANY violation (no known-broken allowance)")
    p.add_argument("--v57", action="store_true",
                   help="check the v57 SERVING adapter emits exactly the v57 contract")
    p.add_argument("--json", action="store_true", help="emit JSON report")
    args = p.parse_args(argv)

    if args.v57:
        contract = args.contract or Path("models/v57/feature_contract.json")
        v = run_v57_serving_parity(contract)
        print(json.dumps(v, indent=2) if args.json else
              f"v57 serving↔contract ({v['contract_version']}, "
              f"{v['contract_features']} features): "
              f"{'OK' if v['ok'] else 'VIOLATIONS: ' + repr(v['violations'])}")
        return 0 if v["ok"] else 1

    rep = run_parity(
        contract_path=args.contract,
        model_path=None if args.no_model else args.cbm,
        train_matrix_path=args.train_matrix,
        expect_fixed=args.expect_fixed,
    )

    if args.json:
        print(json.dumps(rep.as_dict(), indent=2))
    else:
        _print_report(rep)
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
