"""Regression checks for release-gating GitHub Actions lifecycle semantics."""

from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"


def _staging_job() -> str:
    text = WORKFLOW.read_text()
    return text.split("  staging-evidence:\n", 1)[1]


def test_staging_ci_does_not_abort_when_migration_exits_successfully():
    """A successful one-shot migration must not abort the long-lived stack."""
    job = _staging_job()

    assert "--abort-on-container-exit" not in job
    assert "--exit-code-from acceptance" not in job
    assert "docker compose -f docker-compose.staging.yml up --build --wait app" in job
    assert (
        "docker compose -f docker-compose.staging.yml run "
        "--rm --build --no-deps acceptance"
    ) in job
