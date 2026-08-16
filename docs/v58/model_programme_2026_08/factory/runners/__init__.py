"""Fit runners for the 2026-08 model programme.

Three entry points, one output contract:

    fit_runner.py       ONE (frame, config, seed) fit -- CatBoost | LightGBM | RealMLP
    stack_runner.py     the OWNER-AMEND-6 residual-stack screen (four fences)
    b0_module_runner.py packets view -> feature_engineering_v55 -> the B0-104 frame

All three emit the harness contract in
`scripts/analysis/ablation_tables.py:21-28` (fit JSON + keyed preds parquet), so
`ablation_tables.py --results-dir` consumes them without adaptation.
"""
from . import fit_contract, metrics

__all__ = ["fit_contract", "metrics"]
