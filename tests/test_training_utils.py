"""Tests for the time-based early-stopping split (leakage fix, audit item #1).

Stdlib-only (no numpy/pandas) so they run in any environment.

The invariant under test: the early-stopping validation fold is carved from the
*training period* by date — never from OOT — and the carving falls on a date
boundary so same-date rows are not split between fit and validation.
"""

import os
import sys
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from training_utils import (  # noqa: E402
    time_based_eval_split,
    time_based_eval_split_by_cutoff,
)


def _dates(n, start=datetime(2019, 1, 1), step_days=1):
    return [start + timedelta(days=i * step_days) for i in range(n)]


def test_split_partitions_all_rows_without_overlap():
    dates = _dates(100)
    train_idx, val_idx = time_based_eval_split(dates, val_fraction=0.1)
    assert set(train_idx).isdisjoint(val_idx)
    assert sorted(train_idx + val_idx) == list(range(100))
    assert train_idx and val_idx


def test_validation_is_the_most_recent_slice():
    # Ascending dates: the validation indices must be the latest ones.
    dates = _dates(100)
    train_idx, val_idx = time_based_eval_split(dates, val_fraction=0.1)
    assert max(train_idx) < min(val_idx)
    # ~10% of rows in validation.
    assert 8 <= len(val_idx) <= 12


def test_validation_is_recent_even_when_input_unordered():
    # Shuffle so positional order != temporal order; the split must still pick
    # the temporally-latest rows for validation.
    base = _dates(50)
    order = list(range(50))
    order = order[::-1]  # reverse: newest first
    dates = [base[i] for i in order]
    train_idx, val_idx = time_based_eval_split(dates, val_fraction=0.2)
    val_dates = [dates[i] for i in val_idx]
    train_dates = [dates[i] for i in train_idx]
    assert min(val_dates) >= max(train_dates)


def test_same_date_group_not_split():
    # 90 unique early dates + 20 rows all sharing one late date. A 0.1 fraction
    # (~11 rows) must pull in the WHOLE 20-row final group rather than cut it.
    dates = _dates(90)
    tied = [datetime(2030, 1, 1)] * 20
    dates = dates + tied
    train_idx, val_idx = time_based_eval_split(dates, val_fraction=0.1)
    val_dates = {dates[i] for i in val_idx}
    # The tied date is entirely on one side.
    tied_in_val = sum(1 for i in val_idx if dates[i] == datetime(2030, 1, 1))
    assert tied_in_val in (0, 20)
    if datetime(2030, 1, 1) in val_dates:
        assert tied_in_val == 20


def test_by_cutoff_inclusive_on_validation_side():
    dates = _dates(10)  # 2019-01-01 .. 2019-01-10
    cutoff = datetime(2019, 1, 6)
    train_idx, val_idx = time_based_eval_split_by_cutoff(dates, cutoff)
    assert [dates[i] for i in train_idx] == _dates(5)  # strictly before cutoff
    assert len(val_idx) == 5  # 6th..10th inclusive


def test_accepts_iso_strings():
    dates = [d.date().isoformat() for d in _dates(20)]
    train_idx, val_idx = time_based_eval_split(dates, val_fraction=0.25)
    assert len(val_idx) == 5
    assert max(train_idx) < min(val_idx)


def test_min_val_floor_respected():
    dates = _dates(3)
    train_idx, val_idx = time_based_eval_split(dates, val_fraction=0.01, min_val=1)
    assert len(val_idx) >= 1
    assert len(train_idx) >= 1


def test_rejects_bad_fraction():
    dates = _dates(10)
    for bad in (0.0, 1.0, -0.1, 1.5):
        try:
            time_based_eval_split(dates, val_fraction=bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for val_fraction={bad}")
