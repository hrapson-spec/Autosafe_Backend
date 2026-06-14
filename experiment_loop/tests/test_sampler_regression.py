"""Regression guard for F1 (nondeterministic sampling) and the F3 rewire.

Source-level (no heavy deps): the committed `evaluate.py` must seed its dev sample and
must not carry the old bare unseeded clause or the old `seed_stable` boolean. A real
end-to-end determinism check on data lives in the promotion sampling tests / local run.
"""
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "evaluate.py").read_text()


def test_dev_sampler_is_seeded_reservoir():
    assert "USING SAMPLE {int(sample)} ROWS (reservoir, 42)" in SRC


def test_no_bare_unseeded_sample_clause():
    # the F1 bug was a bare 'USING SAMPLE {int(sample)} ROWS' with no method/seed
    assert 'USING SAMPLE {int(sample)} ROWS"' not in SRC


def test_old_f3_seed_stable_boolean_is_gone():
    assert "all(d > 0 for d in deltas)" not in SRC
    assert "seed_stable = all" not in SRC


def test_evaluate_imports_and_uses_shared_modules():
    import evaluate
    assert hasattr(evaluate, "load_frames") and hasattr(evaluate, "main")
    assert "score_core" in SRC and "decision.classify_seed_direction" in SRC
