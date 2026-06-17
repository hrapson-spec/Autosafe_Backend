"""Emit models/v57/feature_contract.json from the shared v57 feature definition.

The contract is generated mechanically from ``v57_features`` (which derives the
v57 feature set from the v55 source + the model_bundle decision table) — never
hand-maintained. The real training run will RE-EMIT this file with the exact
dtypes/defaults observed in the matrix and a full training_manifest; this
scaffold version (``v57.0-scaffold``) makes the contract loadable now so the
parity harness (``gf17_train_serve_parity.py --contract``) and the serving
adapter can be wired and tested ahead of the retrain.

Run:  python models/v57/build_contract.py
"""

import sys
from pathlib import Path

# Repo root on path so we can import the shared modules.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from model_bundle import emit_contract, load_contract  # noqa: E402
from v57_features import V57_CATEGORICAL, V57_FEATURE_NAMES, v57_feature_rows  # noqa: E402

OUT = Path(__file__).resolve().parent / "feature_contract.json"

# Serving reuses the v55 artifacts (the v57 path runs v55 engineer_features on a
# window-capped history, then applies the v57 transform), so the bundle depends
# on the same fitted lookups until the v57 retrain re-fits them.
ARTIFACT_DEPENDENCIES = [
    "cohort_stats.pkl",
    "model_hierarchical_features.pkl",
    "segment_hierarchical_features.pkl",
    "model_age_hierarchical.pkl",
    "calibrator.json",
]


def main() -> int:
    emit_contract(
        version="v57.0-scaffold",
        features=v57_feature_rows(),
        artifact_dependencies=ARTIFACT_DEPENDENCIES,
        out_path=OUT,
    )
    # Validate by loading it back through the same loader serving will use.
    c = load_contract(OUT)
    assert c.feature_names == V57_FEATURE_NAMES, "emitted order != shared order"
    assert len(c.feature_names) == 105, len(c.feature_names)
    assert c.categorical_features == V57_CATEGORICAL, c.categorical_features
    assert len(c.categorical_indices) == len(V57_CATEGORICAL)
    print(
        f"wrote {OUT.relative_to(Path.cwd()) if OUT.is_relative_to(Path.cwd()) else OUT}\n"
        f"  version={c.version}  features={len(c.feature_names)}  "
        f"categorical={len(c.categorical_features)}  window_start={c.history_window_start}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
