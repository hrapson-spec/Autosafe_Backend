#!/usr/bin/env python3
"""Tests for the Stage-2 label patch to fit_contract.

The patch must be ADDITIVE: it may admit a new label, but it must not change what any
existing caller loads. Includes fixtures PROVEN ABLE TO FAIL.
"""
import sys
from pathlib import Path

import duckdb
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from factory.runners import fit_contract as fc  # noqa: E402

CANON_EVAL = "out/frames_eval/recipe=eval2024/rung=all/frame/part_*.parquet"
SEV_EVAL = "out/frames_sev/eval/recipe=eval2024/rung=all/frame/part_*.parquet"
COLS = ["b1_n_prior_tests"]


@pytest.fixture()
def con():
    c = duckdb.connect()
    c.execute("SET memory_limit='1GB'")
    yield c
    c.close()


def _glob(p):
    return str(ROOT / p)


# --- the whitelist -----------------------------------------------------------
def test_labels_admits_stage2_targets():
    assert fc.LABELS == ("y_final", "y_initial", "y_b3", "y_m1")


def test_key_columns_unchanged():
    """The new labels must NOT be in KEY_COLUMNS -- that would make them mandatory
    on every frame read by every caller."""
    assert "y_b3" not in fc.KEY_COLUMNS
    assert "y_m1" not in fc.KEY_COLUMNS
    assert fc.KEY_COLUMNS == ("tgt_id", "vehicle_id", "tgt_date", "tgt_outcome",
                              "y_final", "y_initial", "inclusion_weight")


def test_unregistered_label_still_raises(con):
    with pytest.raises(ValueError, match="must be one of"):
        fc.load_frame(con, _glob(SEV_EVAL), COLS, "y_not_a_label")


# --- existing callers are unaffected ----------------------------------------
def test_canonical_frame_still_loads_y_final(con):
    """The patch must not break the frames that lack y_b3 entirely."""
    f = fc.load_frame(con, _glob(CANON_EVAL), COLS, "y_final")
    assert f.n_rows == 330665
    assert int(f.y.sum()) == 75647


def test_canonical_and_copy_agree_on_y_final(con):
    """The relabelled copy must be inert for the ORIGINAL label."""
    a = fc.load_frame(con, _glob(CANON_EVAL), COLS, "y_final")
    b = fc.load_frame(con, _glob(SEV_EVAL), COLS, "y_final")
    assert a.n_rows == b.n_rows
    assert np.array_equal(np.sort(a.test_id), np.sort(b.test_id))
    order_a = np.argsort(a.test_id)
    order_b = np.argsort(b.test_id)
    assert np.array_equal(a.y[order_a], b.y[order_b])


# --- the new label works ------------------------------------------------------
def test_new_label_loads_with_stage1_counts(con):
    f = fc.load_frame(con, _glob(SEV_EVAL), COLS, "y_b3")
    assert f.n_rows == 330665
    assert int(f.y.sum()) == 31463          # Stage 1 banked B3 positives


def test_m1_label_loads_with_stage1_counts(con):
    f = fc.load_frame(con, _glob(SEV_EVAL), COLS, "y_m1")
    assert int(f.y.sum()) == 39707          # Stage 1 banked M1 positives


def test_new_label_absent_from_canonical_frame_raises(con):
    """PROOF the projection is real: y_b3 does not exist in the canonical frame, so
    asking for it there must fail rather than silently yield zeros."""
    with pytest.raises(Exception):
        fc.load_frame(con, _glob(CANON_EVAL), COLS, "y_b3")


def test_labels_are_distinct_vectors(con):
    """Fixture proven able to fail: if the patch projected the wrong column, y_b3 would
    equal y_final and this would not detect a thing."""
    a = fc.load_frame(con, _glob(SEV_EVAL), COLS, "y_final")
    b = fc.load_frame(con, _glob(SEV_EVAL), COLS, "y_b3")
    assert not np.array_equal(a.y, b.y)
    assert int(a.y.sum()) == 75647 and int(b.y.sum()) == 31463


def test_target_never_enters_the_feature_matrix(con):
    """A target listed in extra_columns would become a feature and self-predict."""
    f = fc.load_frame(con, _glob(SEV_EVAL), COLS, "y_b3")
    assert "y_b3" not in f.feature_names
    assert "y_final" not in f.feature_names


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
