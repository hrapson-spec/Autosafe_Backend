"""The fit-result contract: frame loading, train_mixture, JSON + keyed preds.

Normative source: `scripts/analysis/ablation_tables.py:21-28`.

    required     cell, arm, seed, auroc, auprc, logloss, keyed_preds_path
    recommended  arch, featureset, rung_rows, surface, max_train_target_date,
                 train_mixture{era_pre2018_share, prs_share_of_positives,
                 covid_hole_rows, quarter_weight_table}, config_sha, auroc_fprs
    keyed preds  parquet per (cell, seed): test_id, vehicle_id, y, p (float32)

Plus two fields the amendments made mandatory in practice:
    grade              'screen' | 'full'   [OWNER-AMEND-5]
    convergence_state  per-cell falsifier payload (never absent on screen-grade)

Hard gates enforced HERE, before a JSON is written, because the harness's own
gates are exit-3/exit-4 refusals of the WHOLE analysis:
    - max_train_target_date must be < 2024-01-01 (plan L98-100)
    - covid_hole_rows must be 0 (2020-03-30..2021-03-31, prereg S2 section 4)
    - the reported auroc must reproduce from the float32 stored preds
"""
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from . import metrics

FENCE_MAX_TRAIN_TARGET = date(2024, 1, 1)
COVID_HOLE = (date(2020, 3, 30), date(2021, 3, 31))
TAXONOMY_SWITCH = date(2018, 5, 20)
AUROC_RECOMPUTE_TOL = 1e-6

#: y_b3 / y_m1 added 2026-08-15 for the severity Stage-2 experiment
#: (PREREG_SEVERITY_STAGE2_2026_08_15.md). Widening this whitelist is inert on its own;
#: the label must also exist as a column in the base frame being loaded.
LABELS = ("y_final", "y_initial", "y_b3", "y_m1")
KEY_COLUMNS = ("tgt_id", "vehicle_id", "tgt_date", "tgt_outcome",
               "y_final", "y_initial", "inclusion_weight")

PREDS_SCHEMA = pa.schema([("test_id", pa.int64()), ("vehicle_id", pa.int64()),
                          ("y", pa.int8()), ("p", pa.float32())])


class FenceViolation(RuntimeError):
    """A fit that the harness would refuse (exit 3/4). Raised before writing."""


class LibraryUnavailable(RuntimeError):
    """The requested arch's library is not installed in this venv."""


class CategoricalRepresentationError(TypeError):
    """A cat_features column is not a stable string representation."""


class FrameNotSorted(RuntimeError):
    """has_time=True was requested on a frame that is not in time order."""


#: The single literal a missing categorical level takes. A stable literal is
#: load-bearing: CatBoost hashes categorical VALUES, so NaN in one frame and
#: 'nan' in another are two different levels for the same absence.
MISSING_CATEGORY = "__NA__"


def assert_categorical_representation(frame: "Frame", where: str = "frame") -> None:
    """Every cat_features column must be str, with MISSING_CATEGORY for absence.

    CatBoost raises "cat_features must be integer or string" only for the values
    it happens to meet first; a float that survives (1.0, or a NaN that pandas
    renders as the string 'nan') becomes a silent extra level instead. Checked
    explicitly, before Pool construction.
    """
    for name in frame.categorical:
        values = frame.features[name]
        if getattr(values, "dtype", None) is not None and values.dtype.kind == "f":
            raise CategoricalRepresentationError(
                f"{where}: categorical column {name!r} is float dtype "
                f"({values.dtype}); categoricals must be strings with "
                f"{MISSING_CATEGORY!r} for missing.")
        for value in values:
            if isinstance(value, str):
                continue
            raise CategoricalRepresentationError(
                f"{where}: categorical column {name!r} contains a non-string "
                f"value {value!r} ({type(value).__name__}). Missing must be the "
                f"literal {MISSING_CATEGORY!r} -- never NaN, None or a float, "
                f"which CatBoost would hash into a separate level.")


def assert_time_sorted(frame: "Frame", where: str = "frame") -> None:
    """Non-decreasing by (tgt_date, tgt_id). ASSERTS -- never sorts silently.

    `has_time=True` tells CatBoost the row order IS the time order and disables
    its random permutation. Sorting here instead of failing would hide a caller
    that believed its frame was ordered when it was not -- and the resulting
    model would be fitted on a time order nobody chose. The id is the
    representation tiebreak only (D13): it orders nothing across days.
    """
    if frame.n_rows < 2:
        return
    days = np.array([(_as_date(d) or date(1900, 1, 1)).toordinal()
                     for d in frame.tgt_date], dtype=np.int64)
    ids = np.asarray(frame.test_id, dtype=np.int64)
    d_day = np.diff(days)
    d_id = np.diff(ids)
    ok = (d_day > 0) | ((d_day == 0) & (d_id >= 0))
    if not bool(np.all(ok)):
        first = int(np.argmin(ok))
        raise FrameNotSorted(
            f"{where}: has_time=True requires the frame in (test_date, id) order; "
            f"row {first + 1} goes backwards "
            f"(day {days[first]} -> {days[first + 1]}). Materialise a sorted copy "
            f"first -- this runner will not reorder your frame behind your back.")


def config_sha(config: dict) -> str:
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, default=str).encode()).hexdigest()


def _glob_relation(path: str) -> str:
    escaped = str(path).replace("'", "''")
    return f"read_parquet('{escaped}', union_by_name=true)"


@dataclass
class Frame:
    """A loaded modelling frame: keys, label, weights, feature matrix."""

    test_id: np.ndarray
    vehicle_id: np.ndarray
    tgt_date: np.ndarray
    tgt_outcome: np.ndarray
    y: np.ndarray
    weight: np.ndarray
    features: Dict[str, np.ndarray]
    categorical: List[str] = field(default_factory=list)

    @property
    def n_rows(self) -> int:
        return len(self.test_id)

    @property
    def feature_names(self) -> List[str]:
        return list(self.features)

    def matrix(self):
        """Design matrix as a typed DataFrame.

        CatBoost/LightGBM both want categoricals as strings and everything else
        numeric; an object-dtype ndarray makes both libraries guess.
        """
        import pandas as pd

        from .. import blocks

        data = {}
        ordered = blocks_ordered_vocabularies()
        for name, values in self.features.items():
            # ORDERED pinned vocabulary first: it is no longer in
            # `self.categorical` (cat_features indices derive from that list),
            # so it must be handled before the numeric fallthrough.
            #
            # Rendered as its contract-defined ordinal. Preserves the ordering a
            # categorical discards, and makes the Pool layout independent of
            # which levels a given split observed -- a rare level ('none': 2
            # rows in 239) can fall entirely into one side of a seed-dependent
            # train/valid split, after which quantize() derives a different
            # layout and CatBoost refuses the fit outright.
            if name not in self.categorical:
                data[name] = values.astype(np.float64)
                continue
            text = values.astype(str)
            vocab = blocks.PINNED_VOCABULARIES.get(name)
            if vocab is None:
                data[name] = text
                continue
            # A pinned NOMINAL categorical carries its FULL vocabulary on every frame,
            # so the Pool's category set is a property of the contract rather
            # than of whichever levels this split happened to observe. Without
            # this, a rare level ('none': 2 rows in 239) can fall entirely into
            # one side of a train/valid split, quantize() derives different
            # layouts, and CatBoost refuses the fit with "Feature #N is used in
            # training data, but not available in test dataset".
            #
            # Membership is validated; COVERAGE deliberately is not. Requiring
            # every frame to observe every level would make a legitimately
            # homogeneous cohort un-scoreable.
            # MISSING_CATEGORY is always carried: NULL is a legitimate value for
            # a status column and must be representable in every Pool.
            levels = tuple(vocab) + ((MISSING_CATEGORY,)
                                     if MISSING_CATEGORY not in vocab else ())
            stray = set(text) - set(levels)
            if stray:
                raise ValueError(
                    f"{name}: values outside the pinned vocabulary: "
                    f"{sorted(stray)[:5]}. The frame and factory/blocks.py "
                    f"disagree; one of them is stale.")
            data[name] = pd.Categorical(text, categories=levels)
        return pd.DataFrame(data, columns=list(self.features))


def resolve_featureset(featureset: Sequence[str],
                       extra_columns: Optional[Sequence[str]] = None) -> List[str]:
    """Block names (B1..B6) and/or explicit column names -> column list.

    Meta columns are never features: they are identity, labels and the sampling
    design. `tgt_miles` in particular is the target test's OWN odometer reading,
    which is unavailable at serving time.
    """
    from .. import blocks

    out: List[str] = []
    for token in featureset:
        key = str(token).strip()
        if key.upper() in blocks.BLOCK_COLUMNS and key.upper() != "META":
            out.extend(c.name for c in blocks.BLOCK_COLUMNS[key.upper()])
        elif key.lower() == "meta":
            raise ValueError("meta columns are identity/label/design, never features")
        else:
            out.append(key)
    out.extend(extra_columns or [])
    seen, unique = set(), []
    for name in out:
        if name not in seen:
            seen.add(name)
            unique.append(name)
    return unique


def featureset_hash(columns: Sequence[str]) -> str:
    """Content hash of the SELECTED logical column set.

    Recorded alongside the physical schema hash so two models built from the
    same parquet with different logical featuresets are distinguishable in the
    ledger. Order-insensitive: a reordered config is the same featureset.
    """
    import hashlib

    digest = hashlib.sha256()
    for name in sorted(set(columns)):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def serve_class_census(columns: Sequence[str]) -> Dict[str, Any]:
    """Classify a selected featureset by deployability.

    Recorded on EVERY saved model, so a research artifact is self-identifying
    and can never be mistaken downstream for something shippable. Columns
    outside the factory registry (B0-104, generated interactions) carry their
    own provenance and are counted as `unregistered` rather than assumed safe.
    """
    from .. import blocks

    spec = {c.name: c for c in blocks.ALL_COLUMNS}
    census: Dict[str, Any] = {"deployable": 0, "unregistered": 0,
                              "research_only": [], "screen_only": []}
    for name in columns:
        col = spec.get(name)
        if col is None:
            census["unregistered"] += 1
        elif col.screen_only:
            census["screen_only"].append(name)
        elif col.serve_class != blocks.SERVE_DEPLOYABLE:
            census["research_only"].append(name)
        else:
            census["deployable"] += 1
    census["research_only"].sort()
    census["screen_only"].sort()
    census["is_deployable"] = not (census["research_only"] or census["screen_only"])
    return census


def assert_deployable(columns: Sequence[str]) -> None:
    """Refuse an artifact declared shippable that carries a non-servable column.

    A serve_class tag only the dictionary enforces is documentation, not a
    contract: the runner is where it has to bite. But the bite belongs on the
    DECLARED-deployable path only -- research arms legitimately persist models
    for scoring and SHAP, and blocking those would break the existing lane
    while protecting nothing. Opt in with config `require_deployable: true`
    (set it on every final/fullpop arm); the census is recorded either way.
    """
    census = serve_class_census(columns)
    if not census["is_deployable"]:
        raise AssertionError(
            "require_deployable is set, but this featureset cannot be served:\n"
            f"  research_only ({len(census['research_only'])}): "
            f"{census['research_only'][:8]}\n"
            f"  screen_only   ({len(census['screen_only'])}): "
            f"{census['screen_only'][:8]}\n"
            "Lane R columns measure an information ceiling and never ship. "
            "Promotion needs an owner ruling AND a verified live serve path.")


def load_frame(con, frame_glob: str, columns: Sequence[str], label: str = "y_final",
               use_weights: bool = True, extra_glob: Optional[str] = None,
               row_filter: Optional[str] = None) -> Frame:
    """Column-projected load. Never a pandas full load of the frame.

    `extra_glob` is an optional second parquet (e.g. the B0-104 frame emitted by
    b0_module_runner) joined on test_id/tgt_id.
    """
    if label not in LABELS:
        raise ValueError(f"label {label!r} must be one of {LABELS}")
    rel = _glob_relation(frame_glob)
    # Additive, opt-in: a label outside KEY_COLUMNS is projected only when asked for.
    # Adding it to KEY_COLUMNS instead would make the column MANDATORY on every frame
    # read by every caller of load_frame, breaking all existing frames.
    select = list(KEY_COLUMNS) + ([label] if label not in KEY_COLUMNS else [])
    base_cols = {c[0] for c in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()}
    from_clause = f"{rel} f"
    extra_cols: List[str] = []
    if extra_glob:
        erel = _glob_relation(extra_glob)
        extra_available = {c[0] for c in
                           con.execute(f"DESCRIBE SELECT * FROM {erel}").fetchall()}
        extra_cols = [c for c in columns if c in extra_available and c not in base_cols]
        from_clause += f" LEFT JOIN {erel} x ON x.test_id = f.tgt_id"
    missing = [c for c in columns if c not in base_cols and c not in extra_cols]
    if missing:
        raise ValueError(f"frame is missing requested columns {missing[:8]} "
                         f"({len(missing)} total)")
    projected = ([f'f."{c}"' for c in select]
                 + [f'f."{c}"' for c in columns if c in base_cols]
                 + [f'x."{c}"' for c in extra_cols])
    where = f"WHERE {row_filter}" if row_filter else ""
    result = con.execute(f"SELECT {', '.join(projected)} FROM {from_clause} {where}")
    table = (result.to_arrow_table() if hasattr(result, "to_arrow_table")
             else result.fetch_arrow_table())

    def col(name):
        return table[name].to_numpy(zero_copy_only=False)

    weight = (col("inclusion_weight").astype(float) if use_weights
              else np.ones(table.num_rows))
    features, categorical = {}, []
    for name in columns:
        # Typing is decided by the ARROW SCHEMA, never by the numpy dtype that
        # comes back: a boolean column with NULLs materialises as object while
        # the same column without NULLs materialises as bool, so a dtype-based
        # rule would make a feature categorical in the train frame and numeric
        # in the eval frame -- which CatBoost rejects at Pool construction.
        values, is_cat = _typed_column(table, name)
        # An ORDERED pinned vocabulary becomes a numeric ordinal HERE, at load,
        # not at render: `features` is read directly by the neural imputation
        # and SHAP paths, so converting only in `matrix()` would leave those
        # looking at raw strings. Converting once keeps every consumer
        # consistent, and drops the column out of `categorical` -- cat_features
        # indices derive from that list.
        if is_cat and name in blocks_ordered_vocabularies():
            features[name] = _ordinal_column(name, values)
            continue
        features[name] = values
        if is_cat:
            categorical.append(name)
    return Frame(
        test_id=col("tgt_id").astype(np.int64),
        vehicle_id=col("vehicle_id").astype(np.int64),
        tgt_date=col("tgt_date"), tgt_outcome=col("tgt_outcome"),
        y=col(label).astype(np.int8), weight=weight,
        features=features, categorical=categorical)


def _ordinal_column(name: str, values) -> "np.ndarray":
    """Render an ORDERED pinned vocabulary to its contract-defined ordinal.

    Preserves the ordering a categorical encoding discards (none <
    left_censored < partial < full), and makes the feature's layout independent
    of which levels a given split observed. Membership is validated per value;
    coverage deliberately is not -- a frame need not observe every level.
    """
    from .. import blocks

    return np.array(
        [np.nan if v == MISSING_CATEGORY
         else float(blocks.vocabulary_ordinal(name, str(v)))
         for v in values.astype(str)], dtype=np.float64)


def blocks_ordered_vocabularies() -> frozenset:
    """Lazy accessor: fit_contract must not import blocks at module scope."""
    from .. import blocks

    return frozenset(blocks.ORDERED_VOCABULARIES)


def _typed_column(table, name):
    """(values, is_categorical) for one column, decided by its arrow type.

    strings          -> categorical (NULL becomes the explicit level '__NA__')
    dates/timestamps -> numeric ordinal (ordered, so a tree can split on it)
    booleans/numbers -> float64 with NaN for NULL
    """
    import pyarrow.types as pat

    column = table[name]
    arrow_type = table.schema.field(name).type
    if pat.is_string(arrow_type) or pat.is_large_string(arrow_type):
        return np.array(["__NA__" if v is None else str(v)
                         for v in column.to_pylist()], dtype=object), True
    if pat.is_date(arrow_type):
        column = column.cast(pa.int32())
    elif pat.is_timestamp(arrow_type):
        column = column.cast(pa.int64())
    return column.cast(pa.float64()).to_numpy(zero_copy_only=False), False


def _as_date(value) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def train_mixture(frame: Frame) -> dict:
    """The recommended `train_mixture` block, computed from the TRAIN rows."""
    dates = [_as_date(d) for d in frame.tgt_date]
    n = len(dates)
    pre = sum(1 for d in dates if d is not None and d < TAXONOMY_SWITCH)
    positives = frame.y == 1
    n_pos = int(positives.sum())
    prs = int(sum(1 for o, is_pos in zip(frame.tgt_outcome, positives)
                  if is_pos and str(o) == "PRS"))
    hole = sum(1 for d in dates
               if d is not None and COVID_HOLE[0] <= d <= COVID_HOLE[1])
    quarters: Dict[str, float] = {}
    for d, w in zip(dates, frame.weight):
        if d is None:
            continue
        key = f"{d.year}Q{(d.month - 1) // 3 + 1}"
        quarters[key] = quarters.get(key, 0.0) + float(w)
    return {
        "era_pre2018_share": (pre / n) if n else None,
        "prs_share_of_positives": (prs / n_pos) if n_pos else 0.0,
        "covid_hole_rows": int(hole),
        "quarter_weight_table": {k: round(v, 4) for k, v in sorted(quarters.items())},
        "n_train_rows": n,
        "n_train_vehicles": int(len(np.unique(frame.vehicle_id))),
        "positive_rate": (n_pos / n) if n else None,
        "weighted": bool(not np.allclose(frame.weight, 1.0)),
    }


def max_train_target_date(frame: Frame) -> Optional[str]:
    dates = [d for d in (_as_date(x) for x in frame.tgt_date) if d is not None]
    return max(dates).isoformat() if dates else None


def assert_fences(mixture: dict, max_target: Optional[str]) -> None:
    if max_target is not None and max_target >= FENCE_MAX_TRAIN_TARGET.isoformat():
        raise FenceViolation(
            f"max_train_target_date={max_target} >= {FENCE_MAX_TRAIN_TARGET} "
            f"(plan L98-100). The harness refuses the whole analysis with exit 3; "
            f"refusing to write the fit instead.")
    if mixture.get("covid_hole_rows"):
        raise FenceViolation(
            f"covid_hole_rows={mixture['covid_hole_rows']} > 0: training rows "
            f"inside {COVID_HOLE[0]}..{COVID_HOLE[1]} (prereg S2 section 4). "
            f"Filter the frame with --row-filter before fitting.")


def write_keyed_preds(path: str, test_id, vehicle_id, y, p_stored) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    table = pa.Table.from_pydict(
        {"test_id": np.asarray(test_id, dtype=np.int64),
         "vehicle_id": np.asarray(vehicle_id, dtype=np.int64),
         "y": np.asarray(y, dtype=np.int8),
         "p": np.asarray(p_stored, dtype=np.float32)}, schema=PREDS_SCHEMA)
    pq.write_table(table, path, compression="zstd")
    return path


def build_fit_json(cell: str, arm: str, seed: int, eval_frame: Frame,
                   p_stored: np.ndarray, keyed_preds_path: str, *,
                   arch: str, featureset: str, grade: str, surface: str,
                   rung_rows: Optional[int], config: dict,
                   convergence_state: dict, train: Frame,
                   extra: Optional[dict] = None) -> dict:
    """Assemble + self-verify the fit JSON. Metrics come from the STORED preds."""
    mixture = train_mixture(train)
    max_target = max_train_target_date(train)
    assert_fences(mixture, max_target)

    y = eval_frame.y
    auroc = metrics.auroc(y, p_stored)
    payload = {
        "cell": cell, "arm": arm, "seed": int(seed),
        "auroc": auroc,
        "auprc": metrics.auprc(y, p_stored),
        "logloss": metrics.logloss(y, p_stored),
        "keyed_preds_path": str(keyed_preds_path),
        "arch": arch, "featureset": featureset,
        "rung_rows": rung_rows if rung_rows is not None else train.n_rows,
        "surface": surface,
        "max_train_target_date": max_target,
        "train_mixture": mixture,
        "config_sha": config_sha(config),
        "auroc_fprs": metrics.auroc_fprs(y, p_stored),
        "grade": grade,
        "convergence_state": convergence_state,
        "n_eval_rows": int(eval_frame.n_rows),
        "n_eval_vehicles": int(len(np.unique(eval_frame.vehicle_id))),
        "n_features": len(train.feature_names),
    }
    payload.update(extra or {})

    # the harness recomputes this from the parquet; prove it here.
    reproduced = metrics.auroc(y, np.asarray(p_stored, dtype=np.float32))
    if abs(reproduced - payload["auroc"]) > AUROC_RECOMPUTE_TOL:
        raise FenceViolation(
            f"reported auroc {payload['auroc']!r} does not reproduce from the "
            f"stored float32 preds ({reproduced!r}); the harness would exit 4.")
    return payload


def write_fit_json(out_dir: str, cell: str, seed: int, payload: dict) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{cell}.seed{seed}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, default=str)
    return path


def write_train_ids(path: str, test_id) -> str:
    """The training row ids, so the stack screen can PROVE disjointness."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    pq.write_table(pa.Table.from_pydict(
        {"test_id": np.asarray(test_id, dtype=np.int64)},
        schema=pa.schema([("test_id", pa.int64())])), path, compression="zstd")
    return path
