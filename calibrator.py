"""Pickle-free Platt calibrator (GF audit P0 remediation).

The original calibrator was a pickled sklearn LogisticRegression fitted on raw
model probabilities. That pickle was created under scikit-learn 1.8.0; the
production image (python:3.9-slim) can only install scikit-learn <= 1.6.1,
where unpickling appears to succeed but ``predict_proba`` raises
``AttributeError: 'LogisticRegression' object has no attribute 'multi_class'``.
model_v55's silent exception fallback then served RAW probabilities — the
"calibrator silently inert" P0 (audit finding gf2b).

A fitted Platt calibrator is just two floats:

    calibrated = sigmoid(A * raw_prob + B)

so we store A and B in ``calibrator.json`` next to the model artifacts and
evaluate the sigmoid directly. No sklearn, no pickle, no version skew.
Equivalence to the pickle was verified exactly:
max |predict_proba - sigmoid(A*x + B)| == 0.0 over the probability domain.

The class keeps the sklearn ``predict_proba`` interface so existing callers
(model_v55.predict_risk) and the calibration regression tests run unchanged.
"""

import json
import math
from pathlib import Path
from typing import List, Sequence, Union

Number = Union[int, float]


class PlattCalibrator:
    """sigmoid(A * x + B) with an sklearn-compatible predict_proba."""

    def __init__(self, A: float, B: float):
        if not (math.isfinite(A) and math.isfinite(B)):
            raise ValueError(f"Non-finite calibrator constants: A={A!r} B={B!r}")
        self.A = float(A)
        self.B = float(B)

    @classmethod
    def from_json(cls, path: Path) -> "PlattCalibrator":
        with open(path) as f:
            spec = json.load(f)
        if spec.get("type") != "platt_sigmoid":
            raise ValueError(f"Unsupported calibrator type: {spec.get('type')!r}")
        return cls(spec["A"], spec["B"])

    def calibrate(self, raw_prob: float) -> float:
        return 1.0 / (1.0 + math.exp(-(self.A * raw_prob + self.B)))

    def predict_proba(self, X: Sequence[Sequence[Number]]) -> List[List[float]]:
        """Mirror sklearn's [[p0, p1], ...] contract for single-feature rows."""
        out = []
        for row in X:
            p1 = self.calibrate(float(row[0]))
            out.append([1.0 - p1, p1])
        return out

    def self_check(self) -> None:
        """Startup sanity: output in (0,1), strictly monotone in the input.

        Raises on failure so a broken artifact is loud at load time instead of
        silently degrading to raw probabilities at predict time.
        """
        probes = [0.01, 0.25, 0.5, 0.75, 0.99]
        values = [self.calibrate(p) for p in probes]
        if not all(0.0 < v < 1.0 for v in values):
            raise ValueError(f"Calibrator self-check failed: outputs {values}")
        if not all(b > a for a, b in zip(values, values[1:])):
            raise ValueError(f"Calibrator self-check failed: non-monotone {values}")
