"""V55 categorical vocabulary shim (GF-17 remediation, Phase A2).

The deployed CatBoost model was trained on categorical vocabularies that the
serving feature code does not emit (GF-17: five broken vocabularies; CatBoost
matches category strings exactly, so out-of-vocabulary values silently dead
the feature). This shim maps serving emissions onto the training vocabulary
at the model boundary only — `engineer_features()` output is unchanged for
all other consumers (confidence, component risks, logging), and the serving
emissions remain the canonical v57 definitions.

Mapping provenance:
- prev_cycle_outcome_band / advisory_trend: exactly the RCA-validated map
  (gf_rca_integrity_battery VMAP; measured effect on the fresh panel:
  ECE 0.0976 -> 0.0082, E/O 1.292 -> 0.989, AUC ~unchanged), plus
  'unknown' -> 'HISTORY_MISSING_LINK' for the rare non-PASS/FAIL result
  (zero rows in the 59,968-row fresh panel; battery-reachable only).
- dominant_mechanism: NO mapping — TESTED AND REJECTED on evidence
  (gf17_phaseA_verification run 1, 2026-06-11): mapping serving 'CLEAN' ->
  training 'NO_HISTORY' (the only in-vocab candidate; the training matrix
  contains no 'CLEAN' level) degraded fresh-panel AUC 0.6876 -> 0.6755
  (-1.2pp) and ECE 0.0082 -> 0.0121. Training's NO_HISTORY bucket (85% of
  rows, b9-dead advisory data) is not a semantic match for serving's
  keyword-less vehicles; an out-of-vocabulary (dead) feature beats an
  actively wrong one. Remains OOV until the v57 retrain on serving vocab.
- gap_band / usage_band_hybrid: NO mapping. The label systems measure
  different concepts / use different edges ('high' means >=12k in training
  but [10k,15k) in serving); any map would be silently wrong. These two
  features remain dead at serve until the v57 rebuild.

This module is an interim v55-compatibility layer: DELETE it when the v57
model (trained through serving FE) ships. Acceptance: GF-17 gate
--expect-fixed with the v57 matrix/model.
"""
from typing import Any, Dict

# Serving value -> training-vocabulary value, per categorical feature.
VOCAB_MAP: Dict[str, Dict[str, str]] = {
    'prev_cycle_outcome_band': {
        'pass': 'PASS',
        'fail': 'FAIL',
        'first_test': 'FIRST_TEST',
        'unknown': 'HISTORY_MISSING_LINK',
    },
    'advisory_trend': {
        'stable': 'STABLE',
        'increasing': 'WORSENING',
        'decreasing': 'IMPROVING',
        'unknown': 'UNKNOWN',
    },
    # dominant_mechanism deliberately ABSENT — see module docstring
    # (mapping tested and rejected: -1.2pp AUC on the fresh panel).
}

# Empirical training vocabulary of the deployed model.cbm for the shimmed
# features (from the frozen v55 training matrix; GF-17 leg 2). Used by tests
# to assert the subset property; not consulted at runtime.
TRAINING_VOCAB: Dict[str, set] = {
    'prev_cycle_outcome_band': {'PASS', 'FAIL', 'FIRST_TEST',
                                'HISTORY_MISSING_LINK'},
    'advisory_trend': {'UNKNOWN', 'FIRST_WITH_HISTORY', 'STABLE',
                       'WORSENING', 'IMPROVING'},
    'dominant_mechanism': {'NO_HISTORY', 'WEAR', 'DAMAGE', 'MIXED',
                           'CORROSION', 'LEAK'},
}


def apply_vocab_shim(features: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of `features` with shimmed categorical values.

    Idempotent (training-vocabulary values are never map keys). The input
    dict is not mutated. Unknown/unmapped values pass through unchanged.
    """
    out = dict(features)
    for feat, mapping in VOCAB_MAP.items():
        v = out.get(feat)
        if isinstance(v, str) and v in mapping:
            out[feat] = mapping[v]
    return out
