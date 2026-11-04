"""Versioned, hash-pinned PSI reference artefact and the frozen-bin assignment rules.

Rules implemented (AMENDMENT_DRAFT.md:49; ROOT = work/garage_mvp):
  * continuous: development deciles, identical edges collapsed; missing is its own cell
  * binary: two cells (0, 1); missing its own cell
  * categorical: a frozen category list (development top-N plus every value with frequency >= 1%),
    all other values -> OTHER, null/empty/'UNKNOWN'/'MISSING' -> UNKNOWN_MISSING
  * 0.5 is added to every cell before normalising both samples (done in psi.py)

"Bins and reference must be fixed before outcome access" is represented, not proven: the artefact is
accepted only through `ReferenceArtefact.from_bytes(raw, expected_sha256)`, where `expected_sha256` is a
required argument that the caller takes from the freeze record (never from the artefact itself), and the
artefact states `bins_frozen_utc`, which G6 compares with the caller-supplied outcome-access start.
That makes silent edits detectable; it cannot prove the stated timestamp is true.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ._stats import MonitoringInputError, quantile

SCHEMA = "garage.monitoring.psi_reference/1"
OTHER = "OTHER"
UNKNOWN_MISSING = "UNKNOWN_MISSING"
MISSING = "MISSING"
SCORE_KEY = "score"
REFERENCE_SOURCES = ("development_<=2023", "authorised_2024_assessment", "fixture")
NORMALISATION = "strip_upper"          # fixed; the artefact must declare it so it is part of the hash
_NULL_STRINGS = frozenset({"UNKNOWN", "MISSING"})
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ReferenceArtefactError(MonitoringInputError):
    """Artefact missing, mis-hashed or malformed."""


def parse_utc(s: Any, what: str) -> datetime:
    if not isinstance(s, str):
        raise MonitoringInputError(f"{what} must be an ISO-8601 string")
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError as e:
        raise MonitoringInputError(f"{what} is not ISO-8601: {s!r}") from e
    if d.tzinfo is None:
        raise MonitoringInputError(f"{what} must carry a timezone offset")
    return d.astimezone(timezone.utc)


def normalise_category(v: Any) -> Optional[str]:
    """None for null/empty/'UNKNOWN'/'MISSING'; otherwise strip().upper() of str(v)."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    s = str(v).strip().upper()
    return None if (s == "" or s in _NULL_STRINGS) else s


def _is_nan(v: Any) -> bool:
    return isinstance(v, float) and math.isnan(v)


class Binner:
    """Assigns raw values to the frozen cells of one input. Cell order: see `labels`."""

    def __init__(self, name: str, spec: Mapping[str, Any]):
        self.name = name
        self.kind = spec["kind"]
        if self.kind == "continuous":
            self.edges = [float(e) for e in spec["edges"]]
            self.labels = [f"bin{i}" for i in range(len(self.edges) + 1)] + [MISSING]
        elif self.kind == "binary":
            self.labels = ["0", "1", MISSING]
        elif self.kind == "categorical":
            self.categories = list(spec["categories"])
            self._cat_index = {c: i for i, c in enumerate(self.categories)}
            known = spec.get("known_values")
            self.known = frozenset(known) if known is not None else None
            self.labels = self.categories + [OTHER, UNKNOWN_MISSING]
        else:  # pragma: no cover - validated earlier
            raise ReferenceArtefactError(f"{name}: unknown kind {self.kind!r}")
        self.n_cells = len(self.labels)

    def classify(self, v: Any) -> Tuple[int, Optional[str]]:
        """(cell index, flag) with flag in {None, 'unparseable', 'other', 'unseen'}.
        'unseen' = categorical value absent from the artefact's `known_values` (only when supplied);
        such a value is also an 'other' value. 'unparseable' values are placed in the MISSING cell."""
        if self.kind == "continuous":
            miss = self.n_cells - 1
            if v is None or _is_nan(v):
                return miss, None
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return miss, "unparseable"
            return bisect_right(self.edges, v), None
        if self.kind == "binary":
            if v is None or _is_nan(v):
                return 2, None
            if isinstance(v, (bool, int, float)) and v in (0, 1):
                return int(v), None
            return 2, "unparseable"
        c = normalise_category(v)
        if c is None:
            return self.n_cells - 1, None
        i = self._cat_index.get(c)
        if i is not None:
            return i, None
        return self.n_cells - 2, ("unseen" if self.known is not None and c not in self.known else "other")


def _validate_input(name: str, spec: Mapping[str, Any], reference_n: int) -> None:
    if not isinstance(spec, Mapping) or spec.get("kind") not in ("continuous", "binary", "categorical"):
        raise ReferenceArtefactError(f"{name}: kind must be continuous, binary or categorical")
    kind = spec["kind"]
    if kind == "continuous":
        e = spec.get("edges")
        if not isinstance(e, list) or not all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                              and math.isfinite(x) for x in e):
            raise ReferenceArtefactError(f"{name}: edges must be a list of finite numbers")
        if any(b <= a for a, b in zip(e, e[1:])):
            raise ReferenceArtefactError(f"{name}: edges must be strictly increasing (identical edges are collapsed)")
        n_cells = len(e) + 2
    elif kind == "binary":
        n_cells = 3
    else:
        cats = spec.get("categories")
        if not isinstance(cats, list) or not all(isinstance(c, str) and c for c in cats):
            raise ReferenceArtefactError(f"{name}: categories must be a list of non-empty strings")
        if len(set(cats)) != len(cats):
            raise ReferenceArtefactError(f"{name}: duplicate categories")
        if any(c != normalise_category(c) for c in cats):
            raise ReferenceArtefactError(f"{name}: categories must already be {NORMALISATION}-normalised and not null-like")
        if OTHER in cats or UNKNOWN_MISSING in cats:
            raise ReferenceArtefactError(f"{name}: {OTHER}/{UNKNOWN_MISSING} are reserved cell names")
        kv = spec.get("known_values")
        if kv is not None and (not isinstance(kv, list) or not set(cats) <= set(kv)):
            raise ReferenceArtefactError(f"{name}: known_values must be a list containing every category")
        n_cells = len(cats) + 2
    rc = spec.get("ref_counts")
    if not isinstance(rc, list) or len(rc) != n_cells or not all(
            isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in rc):
        raise ReferenceArtefactError(f"{name}: ref_counts must be {n_cells} non-negative integers")
    if sum(rc) != reference_n:
        raise ReferenceArtefactError(f"{name}: ref_counts sum to {sum(rc)}, expected reference_n={reference_n}")


def canonical_sha256(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                          .encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReferenceArtefact:
    artefact_id: str
    sha256: str                       # of the exact file bytes, verified against the pinned value
    reference_source: str
    reference_n: int
    bins_frozen_utc: datetime
    inputs: Dict[str, Dict[str, Any]]  # model inputs only (score excluded)
    score: Dict[str, Any]
    raw: Dict[str, Any]

    @classmethod
    def from_bytes(cls, raw_bytes: bytes, expected_sha256: str) -> "ReferenceArtefact":
        """`expected_sha256` is REQUIRED and must come from the freeze record, not from the artefact."""
        if not isinstance(expected_sha256, str) or not _HEX64.match(expected_sha256):
            raise ReferenceArtefactError("expected_sha256 must be 64 lowercase hex characters (pin from the freeze record)")
        got = hashlib.sha256(raw_bytes).hexdigest()
        if got != expected_sha256:
            raise ReferenceArtefactError(f"reference artefact hash mismatch: got {got}, pinned {expected_sha256}")
        try:
            d = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            raise ReferenceArtefactError("reference artefact is not valid UTF-8 JSON") from e
        if not isinstance(d, dict) or d.get("schema") != SCHEMA:
            raise ReferenceArtefactError(f"schema must be {SCHEMA!r}")
        if d.get("normalisation") != NORMALISATION:
            raise ReferenceArtefactError(f"normalisation must be {NORMALISATION!r}")
        if d.get("smoothing") != 0.5:
            raise ReferenceArtefactError("smoothing must be 0.5 (AMENDMENT_DRAFT.md:49)")
        if d.get("reference_source") not in REFERENCE_SOURCES:
            raise ReferenceArtefactError(f"reference_source must be one of {REFERENCE_SOURCES}")
        aid = d.get("artefact_id")
        if not isinstance(aid, str) or not aid:
            raise ReferenceArtefactError("artefact_id is required")
        n = d.get("reference_n")
        if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
            raise ReferenceArtefactError("reference_n must be a positive integer")
        frozen = parse_utc(d.get("bins_frozen_utc"), "bins_frozen_utc")
        inputs, score = d.get("inputs"), d.get("score")
        if not isinstance(inputs, dict) or not inputs or SCORE_KEY in inputs:
            raise ReferenceArtefactError(f"inputs must be a non-empty object and must not contain {SCORE_KEY!r}")
        for name, spec in inputs.items():
            _validate_input(name, spec, n)
        if not isinstance(score, dict) or score.get("kind") != "continuous":
            raise ReferenceArtefactError("score must be a continuous input spec")
        _validate_input(SCORE_KEY, score, n)
        return cls(aid, got, d["reference_source"], n, frozen, inputs, score, d)

    @classmethod
    def from_file(cls, path: str, expected_sha256: str) -> "ReferenceArtefact":
        with open(path, "rb") as f:
            return cls.from_bytes(f.read(), expected_sha256)

    def binners(self) -> Dict[str, Binner]:
        out = {name: Binner(name, spec) for name, spec in self.inputs.items()}
        out[SCORE_KEY] = Binner(SCORE_KEY, self.score)
        return out

    def ref_counts(self, name: str) -> List[int]:
        return list((self.score if name == SCORE_KEY else self.inputs[name])["ref_counts"])


# --------------------------------------------------------------------------- reference builder
def build_reference_dict(rows: Sequence[Mapping[str, Any]], input_kinds: Mapping[str, Mapping[str, Any]], *,
                         artefact_id: str, reference_source: str, bins_frozen_utc: str,
                         score_key: str = SCORE_KEY, min_freq: float = 0.01,
                         include_known_values: bool = True) -> Dict[str, Any]:
    """Reference implementation of the AMENDMENT_DRAFT.md:49 binning rules, for fixtures and tests.

    rows: dicts {input: value, ..., 'score': value}. input_kinds: {name: {'kind': ..., 'top_n': int|None}}
    (top_n applies to categoricals: 20 for make, 30 for family). The production artefact must come from
    the training pipeline; this function exists so the evaluator's rules are executable and testable.

    ASSUMPTIONS (not in the amendment text): decile edges use linear interpolation (type 7); the >=1%
    frequency is over all rows including missing.
    """
    n = len(rows)
    if n == 0:
        raise MonitoringInputError("no reference rows")

    def edges_for(vals: List[float]) -> List[float]:
        s = sorted(vals)
        if not s:
            return []
        e = sorted({quantile(s, k / 10.0) for k in range(1, 10)})
        return e

    inputs: Dict[str, Any] = {}
    for name, kd in list(input_kinds.items()) + [(score_key, {"kind": "continuous"})]:
        kind = kd["kind"]
        raw = [r.get(name) for r in rows]
        if kind == "continuous":
            vals = [float(v) for v in raw if isinstance(v, (int, float)) and not isinstance(v, bool)
                    and not _is_nan(v)]
            spec: Dict[str, Any] = {"kind": kind, "edges": edges_for(vals)}
        elif kind == "binary":
            spec = {"kind": kind}
        else:
            cnt = Counter(c for c in (normalise_category(v) for v in raw) if c is not None)
            top_n = kd.get("top_n")
            keep = {c for c, _ in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]} if top_n else set()
            keep |= {c for c, k in cnt.items() if k / n >= min_freq}
            spec = {"kind": kind, "categories": sorted(keep)}
            if include_known_values:
                spec["known_values"] = sorted(cnt)
        b = Binner(name, spec)
        counts = [0] * b.n_cells
        for v in raw:
            counts[b.classify(v)[0]] += 1
        spec["ref_counts"] = counts
        inputs[name] = spec
    score = inputs.pop(score_key)
    return {"schema": SCHEMA, "artefact_id": artefact_id, "reference_source": reference_source,
            "bins_frozen_utc": bins_frozen_utc, "reference_n": n, "normalisation": NORMALISATION,
            "smoothing": 0.5, "inputs": inputs, "score": score}


def dumps_canonical(d: Mapping[str, Any]) -> bytes:
    """Deterministic serialisation for writing an artefact; its sha256 is what the freeze record pins."""
    return (json.dumps(d, sort_keys=True, indent=1, ensure_ascii=True) + "\n").encode("utf-8")
