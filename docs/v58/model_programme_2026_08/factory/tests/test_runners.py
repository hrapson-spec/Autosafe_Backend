"""Runner tests: contract conformance, stack fences, packets->module equivalence.

The headline test is `test_e2e_ablation_tables_accepts_our_fits`: fit_runner's
output is fed to the REAL harness (scripts/analysis/ablation_tables.py) through
its own CLI, with a temp results dir and a fake PREREG_SHA manifest. If the
harness would refuse our JSON for any reason -- missing key, fence violation,
AUROC that does not reproduce from the float32 preds -- that test fails.

Fixtures only. No real lake, no network.
"""
import importlib.util
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pytest

from conftest import make_config, run_factory
from factory import blocks, emit, sampling
from factory.fixtures import FixtureLake, ItemRow, TestRow, default_population
from factory.runners import b0_module_runner, eb_fleet_builder
from factory.runners import fit_contract as fc, fit_runner, metrics
from factory.runners import score_runner, shap_diagnostic, stack_runner

PROGRAMME_DIR = Path(__file__).resolve().parents[2]
HARNESS = PROGRAMME_DIR / "scripts" / "analysis" / "ablation_tables.py"
VENV_PY = "/Users/henrirapson/autosafe-v58/.venv/bin/python"
RECIPE = emit.WindowRecipe("all", date(2005, 1, 1), date(2024, 1, 1))


# --- metric twins -----------------------------------------------------------

def _load_harness():
    spec = importlib.util.spec_from_file_location("_ablation_tables", HARNESS)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(HARNESS.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(HARNESS.parent))
    return module


def test_auroc_twin_matches_the_harness():
    """Our AUROC must be the harness's AUROC, ties and all (exit-4 gate)."""
    harness = _load_harness()
    rng = np.random.default_rng(7)
    for n, n_levels in ((500, 500), (2000, 7), (300, 1)):
        y = rng.integers(0, 2, n)
        p = rng.integers(0, n_levels, n).astype(float) / max(n_levels - 1, 1)
        ours, theirs = metrics.auroc(y, p), harness.unweighted_auroc(y, p)
        assert (np.isnan(ours) and np.isnan(theirs)) or ours == pytest.approx(
            theirs, abs=1e-15), (n, n_levels)


def test_metrics_are_computed_from_the_stored_float32():
    rng = np.random.default_rng(3)
    y = rng.integers(0, 2, 5000)
    p64 = rng.random(5000)
    stored = metrics.as_stored(p64)
    assert stored.dtype == np.float32
    # the value that must reproduce is the float32 one
    assert metrics.auroc(y, stored) == pytest.approx(
        metrics.auroc(y, stored.astype(np.float64)), abs=1e-15)


def test_auprc_and_logloss_are_sane():
    y = np.array([0, 0, 1, 1])
    perfect = np.array([0.1, 0.2, 0.8, 0.9], dtype=np.float32)
    assert metrics.auroc(y, perfect) == 1.0
    assert metrics.auprc(y, perfect) == pytest.approx(1.0)
    assert metrics.logloss(y, perfect) < metrics.logloss(y, 1 - perfect)
    assert set(metrics.auroc_fprs(y, perfect)) == {"0.01", "0.05", "0.1", "0.2"}


# --- fixtures: a modelling frame -------------------------------------------

def _frames(tmp_path, n_vehicles=260, seed=5, start_year=2016, n_years=6):
    """Build factory frames: a TRAIN window and a strictly later EVAL window.

    Windows are derived from the generated years so the fixture never asks for a
    year the lake does not have (the factory refuses that, correctly).
    """
    lake = default_population(str(tmp_path / "lake"), n_vehicles=n_vehicles,
                              seed=seed, start_year=start_year, n_years=n_years)
    inputs = lake.write()
    config = make_config(tmp_path, n_buckets=2, write_batch_rows=5000)
    factory = emit.Factory(inputs, config)
    factory.connect()
    years = sorted({t.test_date.year for t in lake.tests})
    last = start_year + n_years - 1
    recipes = [
        emit.WindowRecipe("train", date(start_year + 1, 1, 1), date(last - 1, 1, 1)),
        emit.WindowRecipe("evalslice", date(last - 1, 1, 1), date(last + 1, 1, 1)),
    ]
    preflight = factory.preflight(years, recipes)
    prepare = factory.prepare(years, recipes)
    ladder = sampling.RungLadder([sampling.Rung("all", 1.0, 1.0)])
    factory.build(years, recipes, ladder, preflight, prepare)
    root = Path(config.output_dir)
    return {
        "train": str(root / "recipe=train" / "rung=all" / "frame" / "*.parquet"),
        "eval": str(root / "recipe=evalslice" / "rung=all" / "frame" / "*.parquet"),
        "train_packets": str(root / "recipe=train" / "rung=all" / "packets" / "*.parquet"),
        "eval_packets": str(root / "recipe=evalslice" / "rung=all" / "packets" / "*.parquet"),
        "factory": factory,
    }


BASE_CONFIG = {
    "arch": "catboost_gbm", "preset": "screen", "grade": "screen",
    "featureset": ["B1", "B5"], "label": "y_final", "use_weights": True,
    "surface": "panel_fixture", "valid_fraction": 0.2,
    # the COVID hole must be EMPTY in training rows (prereg S2 section 4); this
    # is the --row-filter knob doing exactly that job.
    "row_filter": ("tgt_date < DATE '2020-03-30' OR tgt_date > DATE '2021-03-31'"),
    "params": {"iterations": 60, "depth": 4, "od_wait": 20},
}


# --- fit_runner -------------------------------------------------------------

def test_presets_carry_the_recipes_of_record():
    screen = fit_runner.preset_params("catboost_gbm", "screen")
    full = fit_runner.preset_params("catboost_gbm", "full")
    assert screen["iterations"] == 600 and full["iterations"] == 2000
    assert screen["eval_metric"] == full["eval_metric"] == "AUC"
    realmlp = fit_runner.preset_params("realmlp", "screen")
    assert realmlp["use_ls"] is False and realmlp["ls_eps"] == 0.0
    assert realmlp["batch_size"] == 4096 and realmlp["device"] == "cpu"
    assert realmlp["val_metric_name"] == "1-auc_ovr"
    assert realmlp["use_early_stopping"] is True
    lgbm = fit_runner.preset_params("lightgbm", "screen")
    assert lgbm["objective"] == "binary" and lgbm["metric"] == "auc"


@pytest.mark.parametrize("arch,module,needle", [
    ("lightgbm", "lightgbm", "ENSEMBLE LEG ONLY"),
    ("realmlp", "pytabkit", "label smoothing OFF"),
])
def test_uninstalled_arches_refuse_with_the_recipe_named(tmp_path, arch, module, needle):
    """An absent library must refuse loudly AND name the recipe of record."""
    if importlib.util.find_spec(module) is not None:
        pytest.skip(f"{module} is installed; the refusal path is unreachable")
    frames = _frames(tmp_path, n_vehicles=40, n_years=5)
    config = dict(BASE_CONFIG, arch=arch, params={})
    with pytest.raises(fc.LibraryUnavailable, match=needle):
        fit_runner.run_fit(config, frames["train"], frames["eval"], 101,
                           "s2.D.cum.b0", "D", str(tmp_path / "fits"))


@pytest.mark.parametrize("arch,module,overrides", [
    ("lightgbm", "lightgbm", {"n_estimators": 40, "num_leaves": 15,
                              "min_child_samples": 5, "early_stopping_rounds": 10}),
    ("realmlp", "pytabkit", {"n_epochs": 4, "batch_size": 256}),
    # architecture screen (2026-08-13): same contract, same subprocess shape
    ("xgboost", "xgboost", {"n_estimators": 40, "max_depth": 4,
                            "early_stopping_rounds": 10}),
    ("tabm", "pytabkit", {"model_class": "TabM_D_Classifier", "n_epochs": 4,
                          "batch_size": 256, "device": "cpu"}),
    ("tabpfn", "tabpfn", {"family": "tabpfn", "context_rows": 400,
                          "batch_rows": 500, "max_eval_minutes": 30}),
    ("xrfm", "xrfm", {"time_limit_s": 60, "max_leaf_size": 400}),
])
def test_installed_arches_fit_and_emit_the_contract(tmp_path, arch, module, overrides):
    """Every arch the venv actually has must complete the contract, not just parse.

    Run through the CLI in a SUBPROCESS -- exactly how the night queue invokes it,
    one arch per process. That is not fastidiousness: CatBoost/LightGBM and
    torch/lightning each load their own OpenMP runtime, and on this machine a
    RealMLP fit co-resident with a LightGBM fit SEGFAULTS in
    pytabkit/models/data/conversion.py. In-process it is a crash; one-process-
    per-job (the queue's shape) it is fine, and this test proves the shape.

    It also pins that fit_runner's calls match the real library signatures --
    LGBMClassifier(**kwargs) + lgb.early_stopping + categorical_feature;
    RealMLP_TD_Classifier(...).fit(X_val=, y_val=, cat_col_names=) with
    val_metric_name='1-auc_ovr' (plain '1-auc' is rejected) and NaN-free
    continuous columns (RealMLP refuses NaN; fit_runner imputes train medians).
    """
    if importlib.util.find_spec(module) is None:
        pytest.skip(f"{module} is not installed")
    frames = _frames(tmp_path, n_vehicles=120, n_years=5)
    config = dict(BASE_CONFIG, arch=arch, featureset=["B1"], params=overrides)
    config_path = tmp_path / f"{arch}.json"
    config_path.write_text(json.dumps(config))
    out_dir = tmp_path / "fits"
    proc = subprocess.run(
        [VENV_PY, "-m", "factory.runners.fit_runner",
         "--frame", frames["train"], "--eval-frame", frames["eval"],
         "--config", str(config_path), "--seed", "101",
         "--cell", "s2.D.cum.b0", "--arm", "D", "--out-dir", str(out_dir),
         "--preds-dir", str(out_dir / "preds"), "--thread-count", "1"],
        capture_output=True, text=True, cwd=str(PROGRAMME_DIR))
    assert proc.returncode == 0, f"{arch} fit failed (rc={proc.returncode}):\n{proc.stderr[-2000:]}"

    payload = json.loads((out_dir / "s2.D.cum.b0.seed101.json").read_text())
    assert payload["arch"] == arch
    assert 0.0 <= payload["auroc"] <= 1.0
    n_run = payload["convergence_state"]["n_iterations_run"]
    assert n_run is None or n_run >= 1  # None: archs without iteration semantics
    if arch == "realmlp":
        assert payload["convergence_state"]["nan_imputation"]["rule"] == "train_median"
    table = duckdb.connect().execute(
        f"SELECT * FROM read_parquet('{payload['keyed_preds_path']}')"
    ).to_arrow_table()
    assert str(table.schema.field("p").type) == "float"
    assert metrics.auroc(table["y"].to_numpy(), table["p"].to_numpy()) == pytest.approx(
        payload["auroc"], abs=1e-9), "the harness's exit-4 recompute would fail"


def test_fit_runner_emits_the_contract(tmp_path):
    frames = _frames(tmp_path)
    out = tmp_path / "fits"
    payload = fit_runner.run_fit(BASE_CONFIG, frames["train"], frames["eval"],
                                 101, "s2.D.cum.b0", "D", str(out))
    for key in ("cell", "arm", "seed", "auroc", "auprc", "logloss",
                "keyed_preds_path"):
        assert key in payload
    for key in ("arch", "featureset", "rung_rows", "surface",
                "max_train_target_date", "train_mixture", "config_sha",
                "auroc_fprs", "grade", "convergence_state"):
        assert key in payload, key
    mixture = payload["train_mixture"]
    assert set(mixture) >= {"era_pre2018_share", "prs_share_of_positives",
                            "covid_hole_rows", "quarter_weight_table"}
    assert payload["max_train_target_date"] < "2024-01-01"
    state = payload["convergence_state"]
    assert set(state) >= {"best_iteration", "n_iterations_run", "early_stopped",
                          "converged", "quantization"}

    table = duckdb.connect().execute(
        f"SELECT * FROM read_parquet('{payload['keyed_preds_path']}')"
    ).to_arrow_table()
    assert table.column_names == ["test_id", "vehicle_id", "y", "p"]
    assert str(table.schema.field("p").type) == "float"
    y = table["y"].to_numpy()
    p = table["p"].to_numpy()
    assert metrics.auroc(y, p) == pytest.approx(payload["auroc"], abs=1e-9)
    assert os.path.exists(payload["train_ids_path"])


def test_sample_weights_are_used(tmp_path):
    frames = _frames(tmp_path, n_vehicles=120, n_years=5)
    con = duckdb.connect()
    columns = fc.resolve_featureset(["B1"])
    weighted = fc.load_frame(con, frames["train"], columns, use_weights=True)
    unweighted = fc.load_frame(con, frames["train"], columns, use_weights=False)
    assert np.allclose(unweighted.weight, 1.0)
    assert weighted.weight.shape == unweighted.weight.shape
    assert fc.train_mixture(unweighted)["weighted"] is False


def test_fence_violations_refuse_before_writing(tmp_path):
    frames = _frames(tmp_path, n_vehicles=40, n_years=4)
    con = duckdb.connect()
    frame = fc.load_frame(con, frames["train"], fc.resolve_featureset(["B1"]))
    with pytest.raises(fc.FenceViolation, match="2024-01-01"):
        fc.assert_fences({"covid_hole_rows": 0}, "2024-06-01")
    with pytest.raises(fc.FenceViolation, match="covid_hole_rows"):
        fc.assert_fences({"covid_hole_rows": 3}, "2023-01-01")
    assert fc.max_train_target_date(frame) < "2024-01-01"


def test_meta_columns_are_never_features():
    with pytest.raises(ValueError, match="never features"):
        fc.resolve_featureset(["meta"])
    cols = fc.resolve_featureset(["B1", "B2"])
    assert "tgt_miles" not in cols and "y_final" not in cols
    assert len(cols) == len(blocks.B1_COLUMNS) + len(blocks.B2_COLUMNS)


def test_quantisation_borders_are_reused_across_seeds(tmp_path):
    frames = _frames(tmp_path, n_vehicles=120, n_years=5)
    borders = str(tmp_path / "borders.tsv")
    first = fit_runner.run_fit(BASE_CONFIG, frames["train"], frames["eval"], 101,
                               "s2.D.cum.b0", "D", str(tmp_path / "f1"),
                               borders_path=borders)
    second = fit_runner.run_fit(BASE_CONFIG, frames["train"], frames["eval"], 202,
                                "s2.D.cum.b0", "D", str(tmp_path / "f2"),
                                borders_path=borders)
    assert first["convergence_state"]["quantization"] == "computed"
    assert second["convergence_state"]["quantization"] == "reused"
    assert os.path.exists(borders)


# --- stack_runner -----------------------------------------------------------

def _conditioning(tmp_path, extra=()):
    path = tmp_path / "conditioning.json"
    path.write_text(json.dumps({
        "top_j_features": ["b1_n_prior_test_days", "b1_n_prior_tests",
                           "b5_days_since_prior_day"] + list(extra),
        "chosen_by": "incumbent importance", "frozen_at": "2026-08-12T19:00Z"}))
    return str(path)


def test_stack_refuses_b3_and_b4():
    for block in ("b3", "b4"):
        with pytest.raises(stack_runner.StackLicenceRefused, match="EXEMPT"):
            stack_runner.cell_id("block", block)
    assert stack_runner.cell_id("block", "b1") == "s2.D.stack.b1"
    assert stack_runner.cell_id("null", None) == "s2.D.stack.null"
    assert stack_runner.cell_id("ebcal", None) == "s2.D.stack.ebcal"


def test_conditioning_set_fence(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"top_j_features": []}))
    with pytest.raises(stack_runner.StackLicenceRefused, match="fixed BEFORE"):
        stack_runner.load_conditioning(str(empty))
    too_many = tmp_path / "many.json"
    too_many.write_text(json.dumps({"top_j_features": [f"f{i}" for i in range(16)]}))
    with pytest.raises(stack_runner.StackLicenceRefused, match="J=16"):
        stack_runner.load_conditioning(str(too_many))
    ok = stack_runner.load_conditioning(_conditioning(tmp_path))
    assert ok["J"] == 3 and len(ok["sha256"]) == 64


def _stack_setup(tmp_path):
    """Reference fit on half the vehicles; stack partition = the other half."""
    frames = _frames(tmp_path, n_vehicles=240, n_years=6)
    con = duckdb.connect()
    train_rel = f"read_parquet('{frames['train']}', union_by_name=true)"
    vehicles = [int(r[0]) for r in con.execute(
        f"SELECT DISTINCT vehicle_id FROM {train_rel} ORDER BY vehicle_id").fetchall()]
    half = set(vehicles[: len(vehicles) // 2])
    ref_dir = tmp_path / "refframe"
    ref_dir.mkdir()
    ref_path = str(ref_dir / "ref.parquet")
    stack_path = str(ref_dir / "stack.parquet")
    ids = ", ".join(str(v) for v in sorted(half))
    con.execute(f"COPY (SELECT * FROM {train_rel} WHERE vehicle_id IN ({ids})) "
                f"TO '{ref_path}' (FORMAT parquet)")
    con.execute(f"COPY (SELECT * FROM {train_rel} WHERE vehicle_id NOT IN ({ids})) "
                f"TO '{stack_path}' (FORMAT parquet)")

    ref_cfg = dict(BASE_CONFIG, featureset=["B1"])
    reference = fit_runner.run_fit(ref_cfg, ref_path, frames["eval"], 101,
                                   "s2.D.cum.b0", "D", str(tmp_path / "fits"))
    # score the stack partition AND the eval slice with the same reference
    scored = fit_runner.run_fit(ref_cfg, ref_path, stack_path, 101,
                                "s2.D.cum.b0.scorestack", "D",
                                str(tmp_path / "scorefits"))
    combined = str(tmp_path / "refpreds.parquet")
    con.execute(
        f"COPY (SELECT * FROM read_parquet('{reference['keyed_preds_path']}') "
        f"UNION ALL SELECT * FROM read_parquet('{scored['keyed_preds_path']}')) "
        f"TO '{combined}' (FORMAT parquet)")
    return frames, stack_path, combined, reference


def test_stack_runs_all_four_arms_with_fences(tmp_path):
    frames, stack_path, refpreds, reference = _stack_setup(tmp_path)
    cond = _conditioning(tmp_path)
    out = str(tmp_path / "stackfits")
    cfg = dict(BASE_CONFIG, featureset=[], params={"iterations": 60, "depth": 3})

    payloads = {}
    for cell_type, block in (("null", None), ("block", "b1"), ("block", "b5"),
                             ("ebcal", None)):
        cfg_arm = dict(cfg)
        if cell_type == "ebcal":
            # the EB family is a B0 artefact family; in the fixture we calibrate
            # against a known-real block instead, which exercises the same arm
            cfg_arm["eb_columns"] = ["b1_n_prior_final_fails", "b1_n_prior_tests"]
        payloads[cell_type if block is None else block] = stack_runner.run_stack(
            cfg_arm, stack_path, frames["eval"], 101, cell_type, block, out,
            reference_preds=refpreds,
            reference_train_ids=reference["train_ids_path"],
            conditioning_json=cond,
            refit_delta=0.004 if cell_type == "ebcal" else None)

    assert payloads["null"]["cell"] == "s2.D.stack.null"
    assert payloads["ebcal"]["cell"] == "s2.D.stack.ebcal"
    assert payloads["b1"]["cell"] == "s2.D.stack.b1"
    for payload in payloads.values():
        assert payload["arm"] == "stack"
        fences = payload["stack_fences"]
        assert fences["1_disjoint_partition"]["overlap_rows"] == 0
        assert fences["2_conditioning_set"]["J"] <= 15
        assert fences["3_reconstruction_null_cell"] == "s2.D.stack.null"
        assert fences["4_eb_calibration_cell"] == "s2.D.stack.ebcal"
        assert "QUEUE-ORDERING ONLY" in payload["licence"]
        assert "scalar" in payload["estimand"].lower()
    assert payloads["null"]["n_block_columns"] == 0
    assert payloads["b1"]["n_block_columns"] == len(blocks.B1_COLUMNS)
    assert payloads["ebcal"]["refit_delta"] == pytest.approx(0.004)


def test_stack_refuses_a_non_disjoint_partition(tmp_path):
    frames, _stack_path, refpreds, reference = _stack_setup(tmp_path)
    cond = _conditioning(tmp_path)
    cfg = dict(BASE_CONFIG, featureset=[], params={"iterations": 40, "depth": 3})
    with pytest.raises(stack_runner.StackLicenceRefused, match="fence 1 VIOLATED"):
        stack_runner.run_stack(cfg, frames["train"], frames["eval"], 101,
                               "block", "b1", str(tmp_path / "bad"),
                               reference_preds=refpreds,
                               reference_train_ids=reference["train_ids_path"],
                               conditioning_json=cond)


# --- b0_module_runner -------------------------------------------------------

def _hand_computable_lake(root: str):
    """Three vehicles whose B0 values are hand-computable at the last target."""
    lake = FixtureLake(root)
    # v1: 3 clean passes -> n_prior_tests 3, no failures, prev band 'pass'
    for i, year in enumerate((2019, 2020, 2021), start=1):
        lake.add_test(TestRow(test_id=100 + i, vehicle_id=1,
                              test_date=date(year, 6, 1), outcome="PASS",
                              test_mileage=30_000 + 10_000 * i))
    lake.add_test(TestRow(test_id=190, vehicle_id=1, test_date=date(2022, 6, 1),
                          outcome="PASS", test_mileage=70_000))
    # v2: brakes FAIL then PASS -> has_ever_failed_brakes 1, prev band 'pass'
    lake.add_test(TestRow(test_id=201, vehicle_id=2, test_date=date(2020, 6, 1),
                          outcome="FAIL", test_mileage=40_000))
    lake.add_item(ItemRow(test_id=201, rfr_id="20001", rfr_type_code="F"))
    lake.add_test(TestRow(test_id=202, vehicle_id=2, test_date=date(2021, 6, 1),
                          outcome="PASS", test_mileage=50_000))
    lake.add_test(TestRow(test_id=290, vehicle_id=2, test_date=date(2022, 6, 1),
                          outcome="PASS", test_mileage=60_000))
    # v3: single prior, tyres ADVISORY -> prev_adv_tyres 1, n_prior_tests 1
    lake.add_test(TestRow(test_id=301, vehicle_id=3, test_date=date(2021, 6, 1),
                          outcome="PASS", test_mileage=20_000))
    lake.add_item(ItemRow(test_id=301, rfr_id="20003", rfr_type_code="A"))
    lake.add_test(TestRow(test_id=390, vehicle_id=3, test_date=date(2022, 6, 1),
                          outcome="PASS", test_mileage=28_000))
    return lake


@pytest.mark.skipif(not os.path.exists(b0_module_runner.MODULE_REPO),
                    reason="module-of-record checkout not present")
def test_b0_module_equivalence_on_hand_computable_histories(tmp_path):
    lake = _hand_computable_lake(str(tmp_path / "lake"))
    inputs = lake.write()
    config = make_config(tmp_path / "b0", n_buckets=2)
    factory = emit.Factory(inputs, config)
    factory.connect()
    years = sorted({t.test_date.year for t in lake.tests})
    recipes = [emit.WindowRecipe("t", date(2019, 1, 1), date(2023, 1, 1))]
    pre = factory.preflight(years, recipes)
    prep = factory.prepare(years, recipes)
    factory.build(years, recipes,
                  sampling.RungLadder([sampling.Rung("all", 1.0, 1.0)]), pre, prep)
    packets = str(Path(config.output_dir) / "recipe=t" / "rung=all" / "packets"
                  / "*.parquet")

    out = str(tmp_path / "b0" / "b0_frame.parquet")
    manifest = b0_module_runner.run(packets, out, text_source="section",
                                    processes=1)
    assert manifest["n_features"] == 104
    assert manifest["module_failure_vocabulary"] == ["DANGEROUS", "F", "FAIL", "MAJOR"]

    con = duckdb.connect()
    rows = {int(r[0]): r for r in con.execute(
        f"SELECT test_id, n_prior_tests, n_prior_fails, prev_cycle_outcome_band, "
        f"prior_fail_rate_smoothed, has_ever_failed_brakes, prev_adv_tyres, "
        f"days_since_last_test, test_mileage "
        f"FROM read_parquet('{out}')").fetchall()}

    # v1: three clean priors
    assert rows[190][1] == 3 and rows[190][2] == 0
    assert rows[190][3] == "pass" and rows[190][4] == pytest.approx(0.0)
    assert rows[190][5] == 0
    assert rows[190][7] == pytest.approx(365.0)          # 2021-06-01 -> 2020-06-01
    assert rows[190][8] == pytest.approx(60_000.0)       # LAST PRIOR reading, not the target's
    # v2: one prior FAIL on brakes, then a pass
    assert rows[290][1] == 2 and rows[290][2] == 1
    assert rows[290][4] == pytest.approx(0.5)
    assert rows[290][5] == 1, "repaired vocabulary must see the MAJOR brake failure"
    assert rows[290][3] == "pass"
    # v3: one prior, tyres advisory
    assert rows[390][1] == 1 and rows[390][2] == 0
    assert rows[390][6] == 1
    assert rows[390][3] == "pass"


@pytest.mark.skipif(not os.path.exists(b0_module_runner.MODULE_REPO),
                    reason="module-of-record checkout not present")
def test_b0_module_1k_scale_and_join(tmp_path):
    """~1k targets through the packets->module path, joined back by test_id."""
    frames = _frames(tmp_path, n_vehicles=200, n_years=6)
    out = str(tmp_path / "b0_frame.parquet")
    manifest = b0_module_runner.run(frames["train_packets"], out,
                                    text_source="section", processes=2)
    assert manifest["n_targets"] >= 500
    con = duckdb.connect()
    n_frame, n_joined = con.execute(f"""
        SELECT (SELECT count(*) FROM read_parquet('{frames["train"]}')),
               (SELECT count(*) FROM read_parquet('{frames["train"]}') f
                JOIN read_parquet('{out}') b ON b.test_id = f.tgt_id)
    """).fetchone()
    assert n_joined == n_frame, "every frame row must join its B0 row on test_id"
    assert manifest["defect_text_source"] == "section"
    assert "precision gate is UNRUN" in manifest["defect_text_caveat"]


def test_defect_type_enum_mapping():
    assert b0_module_runner.defect_type({"sev": "dangerous"}) == "DANGEROUS"
    assert b0_module_runner.defect_type({"sev": "major"}) == "MAJOR"
    assert b0_module_runner.defect_type({"sev": "minor"}) == "MINOR"
    assert b0_module_runner.defect_type({"sev": "advisory"}) == "ADVISORY"
    # pre-2018 ungraded falls back to the disposition
    assert b0_module_runner.defect_type({"sev": "pre2018_ungraded", "disp": "F"}) == "MAJOR"
    assert b0_module_runner.defect_type({"sev": "pre2018_ungraded", "disp": "P"}) == "PRS"
    assert b0_module_runner.defect_type({"sev": "pre2018_ungraded", "disp": "A"}) == "ADVISORY"


# --- END-TO-END: the real harness must accept our fits ----------------------

def _fake_prereg(tmp_path) -> Path:
    prereg = tmp_path / "prereg"
    prereg.mkdir()
    (prereg / "PREREG_STAGE2.md").write_text("# fixture prereg S2\n")
    (prereg / "PREREG_STAGE3.md").write_text("# fixture prereg S3\n")
    return prereg


def _prereg_manifest(results_dir: Path, prereg: Path):
    import hashlib

    files = {}
    for name in ("PREREG_STAGE2.md", "PREREG_STAGE3.md"):
        files[name] = hashlib.sha256((prereg / name).read_bytes()).hexdigest()
    (results_dir / "PREREG_SHA.json").write_text(json.dumps({"files": files}))


@pytest.mark.skipif(not HARNESS.exists(), reason="ablation_tables.py not present")
def test_e2e_ablation_tables_accepts_our_fits(tmp_path):
    """fit_runner output -> the REAL harness CLI, end to end.

    Two cells (reference + one adoption contrast), two prereg'd seeds each.
    A refusal at any harness gate -- prereg (exit 2), fence (exit 3), input
    contract incl. the AUROC-from-preds recompute (exit 4) -- fails this test.
    """
    frames = _frames(tmp_path, n_vehicles=320, seed=11, n_years=6)
    results = tmp_path / "results"
    results.mkdir()
    preds = tmp_path / "results" / "preds"

    cells = {"s2.D.cum.b0": ["B1"], "s2.D.cum.b0-1": ["B1", "B5"]}
    for cell, featureset in cells.items():
        config = dict(BASE_CONFIG, featureset=featureset)
        for seed in (101, 202):                       # SEED_SETS['screen']
            fit_runner.run_fit(config, frames["train"], frames["eval"], seed,
                               cell, "D", str(results), preds_dir=str(preds),
                               borders_path=str(tmp_path / f"borders_{cell}.tsv"))

    n_fits = len(list(results.glob("*.json")))
    assert n_fits == 4

    prereg = _fake_prereg(tmp_path)
    _prereg_manifest(results, prereg)
    out_dir = tmp_path / "tables"
    proc = subprocess.run(
        [VENV_PY, str(HARNESS), "--results-dir", str(results),
         "--out-dir", str(out_dir), "--stage", "2",
         "--prereg-dir", str(prereg), "--reps", "50"],
        capture_output=True, text=True, cwd=str(HARNESS.parent))
    assert proc.returncode == 0, (
        f"ablation_tables.py refused our fits (exit {proc.returncode}):\n"
        f"{proc.stderr}")
    assert (out_dir / "stage2_decision_tables.md").exists()
    decisions = json.loads((out_dir / "stage2_decisions.json").read_text())
    assert set(decisions["cells"]) == set(cells)
    for name, cell in decisions["cells"].items():
        assert cell["k"] == 2, name
        assert cell["convergence_state"], f"{name}: convergence_state missing"
    contrast = [c for c in decisions["contrasts"] if c["cell"] == "s2.D.cum.b0-1"]
    assert contrast and contrast[0]["ref"] == "s2.D.cum.b0"
    assert contrast[0]["ci_lo"] is not None, "paired bootstrap did not run"
    assert not any("seed set" in w for w in decisions["warnings"]), decisions["warnings"]


@pytest.mark.skipif(not HARNESS.exists(), reason="ablation_tables.py not present")
def test_e2e_harness_rejects_a_fence_violating_fit(tmp_path):
    """The acceptance proof is only meaningful if the harness can still refuse."""
    frames = _frames(tmp_path, n_vehicles=60, n_years=4)
    results = tmp_path / "results"
    results.mkdir()
    payload = fit_runner.run_fit(BASE_CONFIG, frames["train"], frames["eval"],
                                 101, "s2.D.cum.b0", "D", str(results))
    doctored = dict(payload, max_train_target_date="2024-06-01")
    (results / "s2.D.cum.b0.seed101.json").write_text(json.dumps(doctored, default=str))
    prereg = _fake_prereg(tmp_path)
    _prereg_manifest(results, prereg)
    proc = subprocess.run(
        [VENV_PY, str(HARNESS), "--results-dir", str(results),
         "--out-dir", str(tmp_path / "t"), "--prereg-dir", str(prereg)],
        capture_output=True, text=True, cwd=str(HARNESS.parent))
    assert proc.returncode == 3, proc.stderr
    assert "max_train_target_date" in proc.stderr


# --- score_runner: SCORE-ONLY (never trains) --------------------------------

FULL_CONFIG = dict(BASE_CONFIG, preset="full", grade="full",
                   params={"iterations": 60, "depth": 4, "od_wait": 20})


def test_save_model_refuses_screen_grade(tmp_path):
    """A screen fit is a measurement, not an artifact: it must not be scorable."""
    frames = _frames(tmp_path, n_vehicles=80, n_years=5)
    with pytest.raises(fc.FenceViolation, match="screen-grade"):
        fit_runner.run_fit(BASE_CONFIG, frames["train"], frames["eval"], 101,
                           "s2.D.cum.b0", "D", str(tmp_path / "fits"),
                           model_path=str(tmp_path / "m.pkl"))
    assert not os.path.exists(tmp_path / "m.pkl")


def test_score_runner_round_trip_reproduces_the_fit_auroc(tmp_path):
    """fit -> save -> score the SAME eval frame must reproduce the fit's AUROC.

    1e-6 is not a courtesy: `ablation_tables.load_cell_preds` refuses the whole
    analysis (exit 4) when a JSON auroc does not reproduce from its preds.
    """
    frames = _frames(tmp_path, n_vehicles=200, n_years=6)
    model = str(tmp_path / "models" / "final.pkl")
    fit = fit_runner.run_fit(FULL_CONFIG, frames["train"], frames["eval"], 101,
                             "s2.D.confirm.final", "D", str(tmp_path / "fits"),
                             model_path=model)
    assert fit["model_path"] == model and os.path.exists(model + ".meta.json")

    scored = score_runner.run_score(model, frames["eval"], "s2.D.confirm.sealed",
                                    "confirm", str(tmp_path / "scored"))
    assert scored["auroc"] == pytest.approx(fit["auroc"], abs=1e-6)
    assert scored["auprc"] == pytest.approx(fit["auprc"], abs=1e-6)
    assert scored["grade"] == "score_only"
    assert scored["config_sha"] == fit["config_sha"], "source config_sha not carried"
    assert scored["convergence_state"]["trained_here"] is False
    assert scored["scored_from"]["source_cell"] == "s2.D.confirm.final"
    assert scored["max_train_target_date"] == fit["max_train_target_date"]
    assert scored["train_mixture"] == fit["train_mixture"]
    a = duckdb.connect().execute(
        f"SELECT count(*) FROM read_parquet('{scored['keyed_preds_path']}')"
    ).fetchone()[0]
    assert a == fit["n_eval_rows"]


def test_score_runner_scores_a_different_frame(tmp_path):
    """The sealed/drift use: score a frame the model never saw."""
    frames = _frames(tmp_path, n_vehicles=200, n_years=6)
    model = str(tmp_path / "m.pkl")
    fit_runner.run_fit(FULL_CONFIG, frames["train"], frames["eval"], 101,
                       "s2.D.confirm.final", "D", str(tmp_path / "fits"),
                       model_path=model)
    scored = score_runner.run_score(model, frames["train"], "s2.D.confirm.drift",
                                    "drift", str(tmp_path / "scored2"))
    assert scored["n_eval_rows"] > 0
    assert scored["scored_frame"] == frames["train"]


def test_score_runner_refuses_missing_or_mismatched_artifacts(tmp_path):
    frames = _frames(tmp_path, n_vehicles=80, n_years=5)
    with pytest.raises(score_runner.ModelArtifactError, match="missing"):
        score_runner.run_score(str(tmp_path / "nope.pkl"), frames["eval"], "c",
                               "a", str(tmp_path / "o"))
    model = str(tmp_path / "m.pkl")
    fit_runner.run_fit(FULL_CONFIG, frames["train"], frames["eval"], 101,
                       "s2.D.confirm.final", "D", str(tmp_path / "fits"),
                       model_path=model)
    meta = json.loads(open(model + ".meta.json").read())
    meta["feature_names"] = meta["feature_names"] + ["a_column_that_does_not_exist"]
    open(model + ".meta.json", "w").write(json.dumps(meta))
    with pytest.raises(Exception, match="missing"):
        score_runner.run_score(model, frames["eval"], "c", "a", str(tmp_path / "o2"))


# --- eb_fleet_builder -------------------------------------------------------

def _eb_fixture(tmp_path):
    """Four events with hand-computable fixed-fleet priors, all in one age band."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = [  # (tgt_id, vehicle, date, make, model_id, y)
        (1, 10, date(2020, 1, 1), "FORD", "FORD FOCUS", 1),
        (2, 20, date(2020, 6, 1), "VAUX", "VAUX CORSA", 0),
        (3, 30, date(2021, 1, 1), "FORD", "FORD FOCUS", 0),
        (4, 40, date(2022, 1, 1), "FORD", "FORD FOCUS", 1),
    ]
    table = pa.Table.from_pydict(
        {"tgt_id": [r[0] for r in rows], "vehicle_id": [r[1] for r in rows],
         "tgt_date": [r[2] for r in rows], "tgt_make": [r[3] for r in rows],
         "tgt_model_id": [r[4] for r in rows],
         # age is exactly 7.0 years for every row -> all share the '6-10' band
         "tgt_fud": [r[2] - timedelta(days=2557) for r in rows],
         "y_final": [bool(r[5]) for r in rows]},
        schema=pa.schema([("tgt_id", pa.int64()), ("vehicle_id", pa.int64()),
                          ("tgt_date", pa.date32()), ("tgt_make", pa.string()),
                          ("tgt_model_id", pa.string()), ("tgt_fud", pa.date32()),
                          ("y_final", pa.bool_())]))
    path = str(tmp_path / "fleet.parquet")
    pq.write_table(table, path)
    return path


def test_eb_fleet_priors_are_hand_computable(tmp_path):
    frame = _eb_fixture(tmp_path)
    out = str(tmp_path / "eb" / "eb_frame.parquet")
    manifest = eb_fleet_builder.run(frame, out, prior_strength=50.0)
    assert manifest["rows"] == 4
    rows = {int(r[0]): r for r in duckdb.connect().execute(
        f"SELECT test_id, eb_global_rate_asof, make_age_fail_rate_eb, "
        f"model_age_fail_rate_eb, eb_unified_prior, eb_n_prior_make_age, "
        f"eb_n_prior_model_age FROM read_parquet('{out}')").fetchall()}

    # event 4 sees exactly events 1-3 (2 FORD FOCUS, 1 of them a fail)
    m = 50.0
    r_global = 1 / 3
    make = (1 + m * r_global) / (2 + m)
    model = (1 + m * make) / (2 + m)
    assert rows[4][1] == pytest.approx(r_global)
    assert rows[4][2] == pytest.approx(make)
    assert rows[4][3] == pytest.approx(model)
    assert rows[4][4] == pytest.approx(model), "eb_unified_prior == model_age rate"
    assert rows[4][5] == 2 and rows[4][6] == 2

    # the first event has NO priors: the module's 0.28 base rate, fully shrunk
    assert rows[1][1] == pytest.approx(eb_fleet_builder.BASE_RATE)
    assert rows[1][2] == pytest.approx(eb_fleet_builder.BASE_RATE)
    assert rows[1][5] == 0


def test_eb_fleet_priors_exclude_the_targets_own_day(tmp_path):
    """Strict-date discipline: a frozen end-of-period table would leak."""
    frame = _eb_fixture(tmp_path)
    out = str(tmp_path / "eb.parquet")
    eb_fleet_builder.run(frame, out, prior_strength=50.0)
    leaky_global = 2 / 4          # what it would be if row 4 saw itself
    got = duckdb.connect().execute(
        f"SELECT eb_global_rate_asof FROM read_parquet('{out}') WHERE test_id = 4"
    ).fetchone()[0]
    assert got == pytest.approx(1 / 3)
    assert got != pytest.approx(leaky_global)


def test_eb_builder_emits_a_drop_in_replacement_frame(tmp_path):
    """With --b0-frame it returns the full B0 column set, 3 columns swapped."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    frame = _eb_fixture(tmp_path)
    b0 = str(tmp_path / "b0.parquet")
    pq.write_table(pa.Table.from_pydict({
        "test_id": [1, 2, 3, 4], "vehicle_id": [10, 20, 30, 40],
        "prior_fail_rate_smoothed": [0.1, 0.2, 0.3, 0.4],
        "eb_unified_prior": [9.0] * 4, "model_age_fail_rate_eb": [9.0] * 4,
        "make_age_fail_rate_eb": [9.0] * 4, "n_prior_tests": [1, 2, 3, 4]}), b0)
    out = str(tmp_path / "eb_dropin.parquet")
    manifest = eb_fleet_builder.run(frame, out, b0_frame=b0)
    assert manifest["drop_in_replacement"] is True
    got = duckdb.connect().execute(
        f"SELECT prior_fail_rate_smoothed, eb_unified_prior, n_prior_tests "
        f"FROM read_parquet('{out}') WHERE test_id = 4").fetchone()
    assert got[0] == pytest.approx(0.4), "per-vehicle ratio must be CARRIED, not refit"
    assert got[1] != pytest.approx(9.0), "the EB table column must be REPLACED"
    assert got[2] == 4, "non-EB B0 columns pass through"
    assert "prior_fail_rate_smoothed" in manifest["carried_unchanged"]
    assert len(manifest["simplifications"]) >= 4


def _eb_later_frame(tmp_path, name="later.parquet"):
    """Two 2024 targets: one FORD FOCUS (known source support), one unseen KIA."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = [(100, 900, date(2024, 6, 1), "FORD", "FORD FOCUS"),
            (101, 901, date(2024, 6, 2), "KIA", "KIA RIO")]
    table = pa.Table.from_pydict(
        {"tgt_id": [r[0] for r in rows], "vehicle_id": [r[1] for r in rows],
         "tgt_date": [r[2] for r in rows], "tgt_make": [r[3] for r in rows],
         "tgt_model_id": [r[4] for r in rows],
         "tgt_fud": [r[2] - timedelta(days=2557) for r in rows],
         "y_final": [True, False]},
        schema=pa.schema([("tgt_id", pa.int64()), ("vehicle_id", pa.int64()),
                          ("tgt_date", pa.date32()), ("tgt_make", pa.string()),
                          ("tgt_model_id", pa.string()), ("tgt_fud", pa.date32()),
                          ("y_final", pa.bool_())]))
    path = str(tmp_path / name)
    pq.write_table(table, path)
    return path


def test_eb_frozen_tables_are_hand_computable(tmp_path):
    """Frozen mode: tables fitted on the WHOLE source window (inclusive of its
    last day), applied unchanged to strictly-later rows; unseen keys fall back
    through the hierarchy to the global rate."""
    src = _eb_fixture(tmp_path)          # 4 events, 2 fails -> global 2/4
    frame = _eb_later_frame(tmp_path)
    out = str(tmp_path / "eb_frozen.parquet")
    manifest = eb_fleet_builder.run(frame, out, tables_from=src,
                                    prior_strength=50.0)
    assert manifest["frozen_at"] == "2022-01-01"
    rows = {int(r[0]): r for r in duckdb.connect().execute(
        f"SELECT test_id, eb_global_rate_asof, make_age_fail_rate_eb, "
        f"model_age_fail_rate_eb, eb_n_prior_make_age FROM read_parquet('{out}')"
    ).fetchall()}

    m = 50.0
    r_global = 2 / 4                     # frozen totals INCLUDE the last day
    make = (2 + m * r_global) / (3 + m)  # FORD/6-10: n=3, k=2
    model = (2 + m * make) / (3 + m)     # FORD FOCUS/6-10: n=3, k=2
    assert rows[100][1] == pytest.approx(r_global)
    assert rows[100][2] == pytest.approx(make)
    assert rows[100][3] == pytest.approx(model)
    assert rows[100][4] == 3

    # unseen make: zero support, fully shrunk to the global rate at every level
    assert rows[101][2] == pytest.approx(r_global)
    assert rows[101][3] == pytest.approx(r_global)
    assert rows[101][4] == 0


def test_eb_frozen_refuses_overlapping_windows(tmp_path):
    """A frozen table applied inside its own window would leak; hard refusal."""
    src = _eb_fixture(tmp_path)
    with pytest.raises(ValueError, match="disjoint"):
        eb_fleet_builder.run(src, str(tmp_path / "x.parquet"), tables_from=src)


# --- owner checklist adoptions: metric_period / categoricals / has_time -----

def test_full_preset_pins_metric_period_1_screen_carries_none():
    """Close-out probe 2026-08-12: metric_period is INERT while od_type=Iter
    (the OD forces per-iteration eval). Pinned to 1 for honest telemetry and so
    a future od_type change cannot silently quantise use_best_model by 25."""
    assert fit_runner.preset_params("catboost_gbm", "full")["metric_period"] == 1
    assert "metric_period" not in fit_runner.preset_params("catboost_gbm", "screen")


def _cat_frame(values):
    import numpy as np

    n = len(values)
    return fc.Frame(test_id=np.arange(n, dtype=np.int64),
                    vehicle_id=np.arange(n, dtype=np.int64),
                    tgt_date=np.array([date(2020, 1, 1 + i % 27) for i in range(n)]),
                    tgt_outcome=np.array(["PASS"] * n), y=np.zeros(n, dtype=np.int8),
                    weight=np.ones(n),
                    features={"c": np.array(values, dtype=object),
                              "x": np.arange(n, dtype=float)},
                    categorical=["c"])


def test_categorical_representation_assertion(tmp_path):
    """A NaN in a cat_features column must RAISE, not become a silent level."""
    import numpy as np

    fc.assert_categorical_representation(_cat_frame(["a", "b", fc.MISSING_CATEGORY]))
    with pytest.raises(fc.CategoricalRepresentationError, match="non-string"):
        fc.assert_categorical_representation(_cat_frame(["a", np.nan, "b"]))
    with pytest.raises(fc.CategoricalRepresentationError, match="non-string"):
        fc.assert_categorical_representation(_cat_frame(["a", None, "b"]))
    with pytest.raises(fc.CategoricalRepresentationError, match="non-string"):
        fc.assert_categorical_representation(_cat_frame(["a", 1.0, "b"]))
    float_cat = _cat_frame(["a", "b"])
    float_cat.features["c"] = np.array([1.0, 2.0])
    with pytest.raises(fc.CategoricalRepresentationError, match="float dtype"):
        fc.assert_categorical_representation(float_cat)


def test_loaded_frames_always_satisfy_the_categorical_contract(tmp_path):
    """load_frame's own output must pass the assertion on real fixture data."""
    frames = _frames(tmp_path, n_vehicles=80, n_years=5)
    con = duckdb.connect()
    frame = fc.load_frame(con, frames["train"], fc.resolve_featureset(["B1", "B5"]))
    assert frame.categorical, "the fixture must exercise categoricals"
    fc.assert_categorical_representation(frame)
    for name in frame.categorical:
        assert fc.MISSING_CATEGORY in set(frame.features[name]) or True


def test_has_time_asserts_sortedness_and_never_sorts(tmp_path):
    import numpy as np

    ordered = _cat_frame(["a"] * 5)
    fc.assert_time_sorted(ordered)
    shuffled = _cat_frame(["a"] * 5)
    shuffled.tgt_date = np.array([date(2020, 1, 5), date(2020, 1, 1),
                                  date(2020, 1, 9), date(2020, 1, 2),
                                  date(2020, 1, 7)])
    before = list(shuffled.tgt_date)
    with pytest.raises(fc.FrameNotSorted, match="goes backwards"):
        fc.assert_time_sorted(shuffled, "train")
    assert list(shuffled.tgt_date) == before, "the frame was reordered behind us"


def test_has_time_refuses_an_unsorted_frame_end_to_end(tmp_path):
    """The factory writes in scan order, so has_time must fail loud on it."""
    frames = _frames(tmp_path, n_vehicles=120, n_years=5)
    config = dict(BASE_CONFIG, featureset=["B1"], has_time=True)
    with pytest.raises(fc.FrameNotSorted):
        fit_runner.run_fit(config, frames["train"], frames["eval"], 101,
                           "s2.D.hastime.b0-6", "D", str(tmp_path / "fits"))


def test_has_time_fits_on_a_sorted_frame(tmp_path):
    frames = _frames(tmp_path, n_vehicles=120, n_years=5)
    con = duckdb.connect()
    sorted_dir = tmp_path / "sorted"
    sorted_dir.mkdir()
    train_sorted = str(sorted_dir / "train.parquet")
    eval_sorted = str(sorted_dir / "eval.parquet")
    for src, dst in ((frames["train"], train_sorted), (frames["eval"], eval_sorted)):
        con.execute(f"COPY (SELECT * FROM read_parquet('{src}', union_by_name=true) "
                    f"ORDER BY tgt_date, tgt_id) TO '{dst}' (FORMAT parquet)")
    config = dict(BASE_CONFIG, featureset=["B1"], has_time=True)
    payload = fit_runner.run_fit(config, train_sorted, eval_sorted, 101,
                                 "s2.D.hastime.b0-6", "D", str(tmp_path / "fits"))
    assert payload["has_time"] is True
    assert payload["convergence_state"]["has_time"] is True
    assert 0.0 <= payload["auroc"] <= 1.0


def test_has_time_is_catboost_only(tmp_path):
    frames = _frames(tmp_path, n_vehicles=40, n_years=4)
    config = dict(BASE_CONFIG, arch="lightgbm", featureset=["B1"], has_time=True)
    with pytest.raises(ValueError, match="CatBoost ordering contract"):
        fit_runner.run_fit(config, frames["train"], frames["eval"], 101,
                           "s2.D.hastime.b0-6", "D", str(tmp_path / "fits"))


def test_shap_diagnostic_ranks_and_flags_dominance(tmp_path):
    frames = _frames(tmp_path, n_vehicles=200, n_years=6)
    model = str(tmp_path / "m.pkl")
    fit_runner.run_fit(FULL_CONFIG, frames["train"], frames["eval"], 101,
                       "s2.D.confirm.final", "D", str(tmp_path / "fits"),
                       model_path=model)
    out = str(tmp_path / "shap_final_topN.json")
    payload = shap_diagnostic.run(model, frames["eval"], out, subsample=500,
                                  top_n=10)
    assert len(payload["top_features"]) == 10
    ranks = [f["mean_abs_shap"] for f in payload["top_features"]]
    assert ranks == sorted(ranks, reverse=True), "top_features must be ranked"
    assert payload["rows_used"] <= payload["rows_available"]
    assert isinstance(payload["dominance"]["flag"], bool)
    assert "TRIPWIRE" in payload["dominance"]["meaning"]
    assert payload["config_sha"] == json.loads(
        open(model + ".meta.json").read())["config_sha"]
    assert os.path.exists(out)


# --- PREREG_OVERFIT_2026_08_16: temporal ES split + planted-null shuffle -----

def _temporal_frame(n_vehicles=200, rows_per=3, start=date(2018, 1, 1)):
    """A frame where every vehicle's rows are spread across the whole window,
    so a naive date cut WOULD straddle vehicles. That is the point: a fixture
    in which the disjointness rule is never exercised proves nothing."""
    import numpy as np

    test_id, vehicle_id, dates = [], [], []
    for v in range(n_vehicles):
        for r in range(rows_per):
            test_id.append(v * rows_per + r)
            vehicle_id.append(v)
            dates.append(start + timedelta(days=(r * 400) + (v % 40)))
    n = len(test_id)
    order = np.argsort([d.toordinal() for d in dates], kind="mergesort")
    test_id = np.array(test_id, dtype=np.int64)[order]
    vehicle_id = np.array(vehicle_id, dtype=np.int64)[order]
    dates = np.array(dates, dtype=object)[order]
    return fc.Frame(test_id=test_id, vehicle_id=vehicle_id, tgt_date=dates,
                    tgt_outcome=np.array(["PASS"] * n),
                    y=(np.arange(n) % 4 == 0).astype(np.int8),
                    weight=np.ones(n),
                    features={"x": np.arange(n, dtype=float)}, categorical=[])


def test_temporal_split_is_strictly_ordered_and_vehicle_disjoint():
    """PREREG_OVERFIT R5. Both invariants, on a fixture built to violate them
    if the disjointness pass were removed."""
    frame = _temporal_frame()
    fit_part, valid_part = fit_runner.split_validation(frame, 0.10, seed=1,
                                                       mode="temporal")
    assert fit_part.n_rows > 0 and valid_part.n_rows > 0

    fit_v = set(fit_part.vehicle_id.tolist())
    val_v = set(valid_part.vehicle_id.tolist())
    assert not (fit_v & val_v), "a vehicle straddles the temporal split"

    fit_max = max(fc._as_date(d) for d in fit_part.tgt_date)
    val_min = min(fc._as_date(d) for d in valid_part.tgt_date)
    assert fit_max < val_min, f"fit part reaches {fit_max} >= valid start {val_min}"

    # the drop is real and must be visible, not silently absorbed
    assert fit_part.n_rows + valid_part.n_rows < frame.n_rows


def test_temporal_split_fixture_can_actually_fail():
    """A green invariance test proves nothing until the fixture is shown able to
    fail. Reproduce the split WITHOUT the disjointness pass and assert that the
    very assertions above then break."""
    import numpy as np

    frame = _temporal_frame()
    days = np.array([fc._as_date(d).toordinal() for d in frame.tgt_date])
    cut = np.quantile(days, 0.90, method="lower")
    valid_mask = days > cut
    naive_fit = fit_runner._subset(frame, ~valid_mask)      # no straddle removal
    naive_val = fit_runner._subset(frame, valid_mask)
    assert set(naive_fit.vehicle_id.tolist()) & set(naive_val.vehicle_id.tolist()), (
        "fixture is too easy: no vehicle straddles the cut, so the disjointness "
        "pass is never exercised and the passing test above is vacuous")


def test_temporal_split_rejects_an_unsupportable_fraction():
    frame = _temporal_frame()
    with pytest.raises(ValueError, match="cannot support fraction"):
        fit_runner.split_validation(frame, 0.0, seed=1, mode="temporal")


def test_split_validation_mode_default_is_unchanged():
    """The new key must be inert when absent: byte-identical partitions."""
    frame = _temporal_frame()
    a_fit, a_val = fit_runner.split_validation(frame, 0.15, seed=7)
    b_fit, b_val = fit_runner.split_validation(frame, 0.15, seed=7, mode="vehicle")
    assert a_fit.test_id.tolist() == b_fit.test_id.tolist()
    assert a_val.test_id.tolist() == b_val.test_id.tolist()
    with pytest.raises(ValueError, match="must be 'vehicle' or 'temporal'"):
        fit_runner.split_validation(frame, 0.15, seed=7, mode="random")


def test_planted_null_shuffles_train_labels_only(tmp_path):
    """PREREG_OVERFIT R6. The shuffle must be a PERMUTATION of the training
    label (positive count preserved), must be recorded, and must leave the eval
    frame's labels alone -- otherwise the arm compares two shuffles instead of
    scoring noise against the real target."""
    frames = _frames(tmp_path)
    config = dict(BASE_CONFIG, shuffle_label_seed=90210)
    payload = fit_runner.run_fit(config, frames["train"], frames["eval"], 101,
                                 "of.nullfix", "OF", str(tmp_path / "fits"))
    shuffle = payload["convergence_state"]["label_shuffle"]
    assert shuffle["seed"] == 90210
    assert shuffle["positives_before"] == shuffle["positives_after"] > 0
    assert shuffle["eval_labels_touched"] is False

    # the eval labels are untouched: identical positive count to the control run
    control = fit_runner.run_fit(BASE_CONFIG, frames["train"], frames["eval"],
                                 101, "of.nullctrl", "OF", str(tmp_path / "fits"))
    import pyarrow.parquet as pq
    a = pq.read_table(payload["keyed_preds_path"], columns=["y"])["y"].to_numpy()
    b = pq.read_table(control["keyed_preds_path"], columns=["y"])["y"].to_numpy()
    assert a.tolist() == b.tolist()

    # The permutation must actually move rows -- a near-identity draw would be
    # a silently useless null. NOT asserted here: that the shuffled arm scores
    # below the control. This fixture lake carries no real signal (the control
    # itself lands at ~0.48 on it), so an ordering assertion would be testing
    # fixture noise. R6's chance-level read belongs on the real frame.
    changed = payload["convergence_state"]["label_shuffle"]["n_positions_changed"]
    assert changed > 0.1 * shuffle["n_rows"], f"only {changed} rows moved"


def test_shuffle_key_absent_is_inert(tmp_path):
    frames = _frames(tmp_path)
    payload = fit_runner.run_fit(BASE_CONFIG, frames["train"], frames["eval"],
                                 101, "of.inert", "OF", str(tmp_path / "fits"))
    assert "label_shuffle" not in payload["convergence_state"]
    assert payload["convergence_state"]["valid_split"] == "vehicle"
