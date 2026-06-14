"""Unit tests for run_guard.py — the promotion-grade clean-worktree gate.

Policy (review pt 8): a promotion run refuses to start unless EVERY tracked/untracked
file is clean EXCEPT an explicit output allowlist (runs/, the dev ledger). Don't
enumerate "source" — that lets a changed .sql/.yaml/contract slip through. The parser
is pure (porcelain text in, blocking paths out), so the policy is exhaustively tested.
"""
import pytest

from run_guard import dirty_blocking_paths, assert_source_clean, DirtyWorktreeError

ALLOW = ["experiment_loop/runs/", "experiment_loop/ledger.tsv"]


def test_clean_porcelain_has_no_blocking_paths():
    assert dirty_blocking_paths("", ALLOW) == []


def test_modified_source_file_blocks():
    assert dirty_blocking_paths(" M experiment_loop/evaluate.py\n", ALLOW) == \
        ["experiment_loop/evaluate.py"]


def test_allowlisted_run_artifacts_do_not_block():
    porc = " M experiment_loop/ledger.tsv\n?? experiment_loop/runs/abc/manifest.json\n"
    assert dirty_blocking_paths(porc, ALLOW) == []


def test_untracked_new_source_file_blocks():
    # an added file can change behaviour — it must block (don't only check known files)
    assert dirty_blocking_paths("?? experiment_loop/new_feature.py\n", ALLOW) == \
        ["experiment_loop/new_feature.py"]


def test_non_python_tracked_file_blocks():
    # a SQL/yaml/contract change must block too (the whole point of the allowlist policy)
    assert dirty_blocking_paths(" M work/contracts/feature_contract.yaml\n", ALLOW) == \
        ["work/contracts/feature_contract.yaml"]


def test_staged_and_unstaged_both_block():
    porc = "M  experiment_loop/decision.py\n M experiment_loop/sampling.py\n"
    assert set(dirty_blocking_paths(porc, ALLOW)) == \
        {"experiment_loop/decision.py", "experiment_loop/sampling.py"}


def test_rename_uses_new_path():
    assert dirty_blocking_paths("R  old.py -> experiment_loop/renamed.py\n", ALLOW) == \
        ["experiment_loop/renamed.py"]


def test_mixed_blocks_only_nonallowlisted():
    porc = (" M experiment_loop/runs/x.log\n"
            " M experiment_loop/evaluate.py\n"
            "?? experiment_loop/ledger.tsv\n")
    assert dirty_blocking_paths(porc, ALLOW) == ["experiment_loop/evaluate.py"]


def test_assert_source_clean_raises_on_dirty(monkeypatch):
    import run_guard
    monkeypatch.setattr(run_guard, "worktree_porcelain",
                        lambda repo_root: " M experiment_loop/evaluate.py\n")
    with pytest.raises(DirtyWorktreeError):
        assert_source_clean("/repo", ALLOW)


def test_assert_source_clean_passes_when_only_allowlisted_dirty(monkeypatch):
    import run_guard
    monkeypatch.setattr(run_guard, "worktree_porcelain",
                        lambda repo_root: "?? experiment_loop/runs/r1/manifest.json\n")
    assert_source_clean("/repo", ALLOW)  # no raise
