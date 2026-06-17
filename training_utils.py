"""Training-time utilities shared by the v55/v57 trainers.

Currently this provides the **time-based validation split** used for CatBoost
early stopping.

Why this exists (leakage fix — audit item #1)
---------------------------------------------
The v55 trainer fit every seed with ``eval_set = <OOT pool>`` and
``early_stopping_rounds=150`` (train_catboost_production_v55.py). That lets the
**out-of-time** labels choose each model's iteration count, so the OOT AUC that
is then reported is optimistically biased — OOT is no longer "untouched".

The fix is to carve the early-stopping validation set out of the **training
period (DEV)** by date, and reserve OOT strictly for the final, single
evaluation. ``time_based_eval_split`` does the carving: the most-recent slice of
DEV (by test date) becomes the validation fold, everything earlier is used to
fit the trees. Using the most-recent DEV slice (rather than a random slice)
keeps the early-stopping signal temporally honest — it rewards iteration counts
that generalise *forward in time*, which is what OOT then measures on truly
unseen later data.

Dependency-light on purpose: stdlib only, so it is importable and unit-testable
in any environment (no numpy/pandas required).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Sequence, Tuple

__all__ = [
    "time_based_eval_split",
    "time_based_eval_split_by_cutoff",
]


def _to_sortable(value):
    """Coerce a date-like value to something orderable.

    Accepts ``datetime``/``date`` objects, ISO-8601 strings, and anything that
    is already comparable (e.g. numpy datetime64, pandas Timestamp). Returns the
    value unchanged when it is already a ``date``/``datetime``; parses strings.
    """
    if isinstance(value, (datetime, date)):
        return value
    if isinstance(value, str):
        # Accept 'YYYY-MM-DD' and full ISO timestamps; fall back to date prefix.
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.fromisoformat(value[:10])
    # numpy datetime64 / pandas Timestamp / etc. are already orderable.
    return value


def time_based_eval_split_by_cutoff(
    dates: Sequence,
    cutoff,
) -> Tuple[List[int], List[int]]:
    """Split positional indices by a date ``cutoff``.

    Rows with ``date < cutoff`` go to *train*; rows with ``date >= cutoff`` go to
    *validation*. Keeping the boundary inclusive on the validation side means all
    rows sharing the cutoff date land together (no same-date straddling between
    the fit set and the early-stopping set).

    Args:
        dates: per-row date-like values, positionally aligned to the feature
            matrix (``X.iloc[idx]``).
        cutoff: date-like threshold.

    Returns:
        ``(train_idx, val_idx)`` as lists of positional integer indices.
    """
    cut = _to_sortable(cutoff)
    train_idx: List[int] = []
    val_idx: List[int] = []
    for i, d in enumerate(dates):
        if _to_sortable(d) >= cut:
            val_idx.append(i)
        else:
            train_idx.append(i)
    return train_idx, val_idx


def time_based_eval_split(
    dates: Sequence,
    val_fraction: float = 0.1,
    *,
    min_val: int = 1,
) -> Tuple[List[int], List[int]]:
    """Carve the most-recent ``val_fraction`` of rows (by date) as validation.

    The cutoff is the date at the ``(1 - val_fraction)`` quantile of the sorted
    unique dates, so the split is taken on a **date boundary** and same-date rows
    are never separated between fit and early-stopping sets.

    This is the function trainers should call to obtain an early-stopping
    ``eval_set`` that lives entirely inside the training period — never the OOT
    set (see module docstring, audit item #1).

    Args:
        dates: per-row date-like values, positionally aligned to the feature
            matrix. Length must be >= 2.
        val_fraction: target share of rows for validation (0 < f < 1).
        min_val: floor on validation rows (guards tiny inputs).

    Returns:
        ``(train_idx, val_idx)`` as lists of positional integer indices. Both are
        guaranteed non-empty for valid inputs.

    Raises:
        ValueError: on empty input or ``val_fraction`` outside (0, 1).
    """
    n = len(dates)
    if n == 0:
        raise ValueError("dates is empty")
    if not (0.0 < val_fraction < 1.0):
        raise ValueError(f"val_fraction must be in (0, 1), got {val_fraction}")

    sortable = [_to_sortable(d) for d in dates]
    order = sorted(range(n), key=lambda i: sortable[i])

    target_val = max(min_val, round(n * val_fraction))
    target_val = min(target_val, n - 1)  # always leave at least one train row

    # Find a date cutoff so that the validation side has >= target_val rows but
    # never breaks a same-date group. Walk from the most-recent row backwards
    # until we have enough validation rows, then extend through the whole final
    # date group.
    boundary_pos = n - target_val
    cutoff_date = sortable[order[boundary_pos]]
    # Pull the boundary earlier to include every row sharing the cutoff date.
    while boundary_pos > 0 and sortable[order[boundary_pos - 1]] == cutoff_date:
        boundary_pos -= 1

    val_positions = set(order[boundary_pos:])
    train_idx = [i for i in range(n) if i not in val_positions]
    val_idx = [i for i in range(n) if i in val_positions]

    # Degenerate guard: a single dominant date could swallow everything.
    if not train_idx or not val_idx:
        # Fall back to a strict positional split on sorted order.
        cut = max(1, min(n - 1, n - target_val))
        train_positions = set(order[:cut])
        train_idx = [i for i in range(n) if i in train_positions]
        val_idx = [i for i in range(n) if i not in train_positions]

    return train_idx, val_idx
