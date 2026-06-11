"""Tripwire: the API must engineer features through the artifact-loaded path.

A local refactor once switched main.py to the bare
feature_engineering_v55.engineer_features(), which silently bypasses the
loaded cohort/EB artifacts (model_age_fail_rate_eb alone carries 13.4%
importance) and degrades every hierarchical prior to its global default —
with no error and no visible failure. Caught in the GF-17 Phase A review
(2026-06-11). These source-level assertions make that regression loud.
"""
import re
from pathlib import Path

MAIN = (Path(__file__).resolve().parent.parent / "main.py").read_text()


def test_main_imports_artifact_loaded_path():
    assert "from model_v55 import engineer_features_with_stats" in MAIN


def test_main_does_not_import_bare_engineer_features():
    assert not re.search(
        r"from\s+feature_engineering_v55\s+import\s+[^\n]*\bengineer_features\b",
        MAIN), ("main.py imports the bare engineer_features — this bypasses "
                "loaded cohort/EB artifacts; use model_v55."
                "engineer_features_with_stats")


def test_no_bare_engineer_features_calls():
    # Every scoring call site must use the _with_stats variant. Comments are
    # excluded so prose mentioning the call form cannot trip this.
    code_only = "\n".join(line.split("#", 1)[0] for line in MAIN.splitlines())
    bare_calls = re.findall(r"(?<![\w.])engineer_features\(", code_only)
    assert not bare_calls, (
        f"{len(bare_calls)} bare engineer_features() call(s) in main.py — "
        "use engineer_features_with_stats")
