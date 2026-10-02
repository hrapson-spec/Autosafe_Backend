"""Disposable failing candidate: proves required CI rejects a merge.

This branch is a release-control experiment and is never a release candidate.
"""


def test_release_gate_rejects_failed_candidate():
    assert False, "Intentional CI gate probe; close this PR after recording the rejection"
