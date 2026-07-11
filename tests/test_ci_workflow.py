"""Regression checks for release-gating GitHub Actions lifecycle semantics."""

from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"
DOCKERFILE = Path(__file__).parents[1] / "Dockerfile"
STAGING_COMPOSE = Path(__file__).parents[1] / "docker-compose.staging.yml"


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


def test_release_image_redacts_access_paths_and_carries_frontend_identity():
    dockerfile = DOCKERFILE.read_text()

    assert "FROM python:3.11-slim" in dockerfile
    assert "--no-access-log" in dockerfile
    assert ".frontend_sha" in dockerfile
    assert ".build_timestamp" in dockerfile


def test_frontend_dependency_audit_is_a_hard_ci_gate():
    workflow = WORKFLOW.read_text()
    frontend_job = workflow.split("  frontend-build:\n", 1)[1].split("\n  e2e:\n", 1)[0]

    assert "npm audit --audit-level=high" in frontend_job
    audit_step = frontend_job.split("npm audit --audit-level=high", 1)[1]
    assert "continue-on-error: true" not in audit_step.split("\n      - name:", 1)[0]


def test_backend_ci_matches_the_release_python_runtime():
    workflow = WORKFLOW.read_text()

    assert "python-version: '3.11'" in workflow
    assert "python-version: '3.9'" not in workflow


def test_python_dependency_audit_is_a_hard_ci_gate():
    workflow = WORKFLOW.read_text()
    security_job = workflow.split("  security-check:\n", 1)[1].split("\n  build-check:\n", 1)[0]

    assert "pip-audit -r requirements.txt" in security_job
    audit_step = security_job.split("pip-audit -r requirements.txt", 1)[1]
    assert "continue-on-error: true" not in audit_step.split("\n      - name:", 1)[0]


def test_production_secret_scan_is_a_hard_ci_gate():
    workflow = WORKFLOW.read_text()
    security_job = workflow.split("  security-check:\n", 1)[1].split("\n  build-check:\n", 1)[0]
    secret_step = security_job.split("      - name: Check for hardcoded secrets\n", 1)[1].split(
        "\n      - name:", 1
    )[0]

    assert "if grep -rE" in secret_step
    assert "exit 1" in secret_step
    assert "--exclude-dir=tests" in secret_step
    assert "continue-on-error: true" not in secret_step


def test_internal_link_sweep_is_a_hard_ci_gate():
    workflow = WORKFLOW.read_text()
    test_job = workflow.split("  test:\n", 1)[1].split("\n  contract-drift:\n", 1)[0]
    assert "python check_internal_links.py" in test_job
    link_step = test_job.split("python check_internal_links.py", 1)[1]
    assert "continue-on-error: true" not in link_step.split("\n      - name:", 1)[0]


def test_staging_stack_bootstraps_assignment_tables_and_gates_retention():
    compose = STAGING_COMPOSE.read_text()

    assert "python3 create_garages_table.py" in compose
    assert "python3 scripts/seed_staging_data.py" in compose
    assert "python3 scripts/retention_sweep.py --months 1" in compose
    assert "python3 scripts/retention_sweep.py --months 1 --execute --batch 50" in compose
    assert "python3 scripts/lead_retention_sweep.py --months 1" in compose
    assert "python3 scripts/lead_retention_sweep.py --months 1 --execute --batch 50" in compose
    assert "VRM_HMAC_KEY:" in compose.split("  acceptance:\n", 1)[1]
    assert (
        "python3 scripts/staging_acceptance.py --base-url http://app:8000 "
        "--expected-share-base-url http://127.0.0.1:8100 "
        "--expected-backend-sha ${GIT_SHA:-unknown}"
    ) in compose
