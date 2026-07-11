# AutoSafe RC1 Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish PR #32 as a reviewed, staging-verified, decision-ready release candidate while preserving unrelated work and stopping before merge or production deployment.

**Architecture:** Keep the already-approved RC1 product architecture unchanged. Repair only the CI orchestration boundary by separating long-running stack startup from the one-shot acceptance process, then package the existing source, test, browser, database, privacy, and rollback evidence into an auditable release packet.

**Tech Stack:** GitHub Actions, Docker Compose v2, FastAPI/Python 3.11, PostgreSQL 16, React 19/TypeScript/Vitest/Playwright, Markdown release documentation.

## Global Constraints

- Do not merge PR #32 or deploy production without Henri's explicit approval.
- Preserve the original `/Users/henrirapson/autosafe` working tree and all unrelated research work.
- Work only in `/Users/henrirapson/autosafe-rc` on `release/product-truth-rc1`.
- Stage explicit paths only; never run `git add -A`.
- Treat live GitHub checks and locally reopened artifacts as authoritative; do not rely on agent summaries.
- A green local suite is insufficient: all seven PR checks, including `staging-evidence`, must pass on the exact pushed SHA.
- Do not claim a Railway deployment identity or production behavior before a real Railway deployment.
- Stop at a decision-ready release packet and explicit GO/NO-GO recommendation.

---

### Task 1: Guard the Staging CI Lifecycle

**Files:**
- Create: `tests/test_ci_workflow.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `docker-compose.staging.yml` services `migrate`, `app`, and `acceptance`.
- Produces: a CI job that waits for `app` health before running `acceptance` as an independent one-shot container.

- [ ] **Step 1: Write the failing regression test**

```python
from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"


def _staging_job() -> str:
    text = WORKFLOW.read_text()
    return text.split("  staging-evidence:\n", 1)[1]


def test_staging_ci_does_not_abort_when_migration_exits_successfully():
    job = _staging_job()
    assert "--abort-on-container-exit" not in job
    assert "--exit-code-from acceptance" not in job
    assert "docker compose -f docker-compose.staging.yml up --build --wait app" in job
    assert "docker compose -f docker-compose.staging.yml run --rm --build --no-deps acceptance" in job
```

- [ ] **Step 2: Run the regression test and confirm RED**

Run: `/Users/henrirapson/autosafe/.venv/bin/python -m pytest tests/test_ci_workflow.py -q`

Expected: FAIL because the current job uses `--abort-on-container-exit --exit-code-from acceptance` and has no two-phase startup.

- [ ] **Step 3: Implement the minimal workflow correction**

Replace the single Compose command with:

```yaml
      - name: Build and start staging app
        env:
          GIT_SHA: ${{ github.sha }}
        run: docker compose -f docker-compose.staging.yml up --build --wait app

      - name: Run staging acceptance
        env:
          GIT_SHA: ${{ github.sha }}
        run: docker compose -f docker-compose.staging.yml run --rm --build --no-deps acceptance
```

Keep the existing failure logs and unconditional teardown steps.

- [ ] **Step 4: Verify GREEN and validate Compose/YAML syntax**

Run:

```bash
/Users/henrirapson/autosafe/.venv/bin/python -m pytest tests/test_ci_workflow.py -q
docker compose -f docker-compose.staging.yml config --quiet
ruby -e 'require "yaml"; YAML.load_file(".github/workflows/ci.yml"); puts "workflow yaml ok"'
```

Expected: regression test PASS; Compose and workflow YAML exit 0.

- [ ] **Step 5: Commit only the lifecycle fix**

```bash
git add tests/test_ci_workflow.py .github/workflows/ci.yml
git commit -m "fix: run staging acceptance after one-shot migration"
```

### Task 2: Close Local Staging and Preserve Evidence

**Files:**
- Inspect: `/private/tmp/claude-501/-Users-henrirapson/5e20271a-8b41-40dd-a611-4d5d44f72a11/scratchpad/staging-evidence/`
- Modify: none in the repository.

**Interfaces:**
- Consumes: the retained `autosafe-rc-staging-pg` container, Uvicorn PID, and numbered evidence files.
- Produces: verified cleanup and an evidence inventory used by the release packet.

- [ ] **Step 1: Confirm the resources belong to this RC staging run**

Run:

```bash
docker inspect autosafe-rc-staging-pg --format '{{.Name}} {{.Config.Image}} {{.State.Status}}'
ps -p 25246 -o pid=,command=
```

Expected: the named PostgreSQL staging container and Uvicorn on port 8100.

- [ ] **Step 2: Finish the interrupted teardown**

Run the exact scoped cleanup recorded by the staging agent: terminate only the confirmed Uvicorn PID, stop/remove only `autosafe-rc-staging-pg`, remove `/tmp/autosafe.db` and `/tmp/autosafe_db_build.lock`, and leave all other containers untouched.

- [ ] **Step 3: Verify cleanup**

Run:

```bash
docker ps -a --format '{{.Names}}' | grep -x autosafe-rc-staging-pg && exit 1 || true
pgrep -f 'uvicorn main:app.*--port 8100' && exit 1 || true
lsof -nP -iTCP:55432 -sTCP:LISTEN && exit 1 || true
lsof -nP -iTCP:8100 -sTCP:LISTEN && exit 1 || true
```

Expected: no matching container, process, or listeners.

- [ ] **Step 4: Reconcile the evidence index with the post-fix run**

Confirm `33_staging_acceptance_POSTFIX.txt` records `16 passed, 0 failed, 16 total`, record that the teardown was interrupted locally, and do not copy bulk raw evidence into Git.

### Task 3: Produce the RC1 Release Packet

**Files:**
- Create: `docs/release_rc1/README.md`
- Create: `docs/release_rc1/DECISION_RECORD.md`
- Create: `docs/release_rc1/RCA_CLOSURE_MATRIX.md`
- Create: `docs/release_rc1/RAILWAY_STRIP_RISK_MEMO.md`
- Create: `docs/release_rc1/RUNBOOK_DEPLOY_ROLLBACK.md`
- Create: `docs/release_rc1/PRIVACY_RECONCILIATION.md`
- Create: `docs/release_rc1/FINAL_RELEASE_REPORT.md`
- Modify: `docs/MONITORING.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: the original engineering challenge, committed RC1 implementation, local staging evidence, CI artifacts, and live PR metadata.
- Produces: the decision record, closure matrix, risk acceptance boundary, deploy/rollback procedure, privacy reconciliation, monitoring definitions, and ten-section release verdict.

- [ ] **Step 1: Build a citation map from the exact release SHA**

For every load-bearing packet claim, record current file/line evidence with `rg -n` or `nl -ba`; use `git show 657930d5:<path>` for original-cause citations. Never copy stale line numbers from prior summaries.

- [ ] **Step 2: Write the architecture decision record and closure matrix**

Cover the five original defects plus error taxonomy, privacy metadata, retention, reproducible build, and release identity. Each closure row must include original cause, implemented change, exact regression test, runtime evidence, and `CLOSED`, `DEFERRED`, or `BLOCKED` status.

- [ ] **Step 3: Write Railway risk and deploy/rollback documents**

State explicitly that local/CI Compose cannot prove Railway context filtering, Railway environment propagation, or Railway build behavior. Require all seven CI checks green, additive migration first, environment verification, post-deploy `/api/version` identity, a canary report round-trip, monitoring, and previous-image rollback.

- [ ] **Step 4: Write privacy reconciliation and monitoring additions**

Document captured identifiers, purpose, storage, 24-month pseudonymisation, 90-day report-token lifetime, URL/log/analytics exposure, rights lookup, and structured alert events. Mark Sentry as unimplemented.

- [ ] **Step 5: Update repository guidance**

Update `CLAUDE.md` to describe the RC1 v2 report path, legacy compatibility routes, current commands, untracked build outputs, `.npmrc`, `.railwayignore`, contract drift, migration, staging, and retention rules.

- [ ] **Step 6: Write the ten-section final release report**

Use the mandated format: executive verdict; source/deployment identity; product decisions; root-cause closure matrix; files changed; verification; staging evidence; privacy/security review; residual risks; release request. Do not issue GO while CI, review, or Railway evidence remains incomplete.

- [ ] **Step 7: Verify packet consistency**

Run:

```bash
rg -n 'TBD|T[O]DO|PLACEHOLDER|all checks green|production verified|Railway staging verified' docs/release_rc1 CLAUDE.md docs/MONITORING.md
/Users/henrirapson/autosafe/.venv/bin/python scripts/claim_sweep.py
```

Expected: no placeholders or unsupported production claims; claim sweep clean.

### Task 4: Review the Entire Release Diff

**Files:**
- Inspect: every path in `git diff --name-only 657930d5...HEAD`.
- Modify: only files required to resolve verified Critical or Important findings.

**Interfaces:**
- Consumes: the full 151+ file RC diff and original hard release gates.
- Produces: a blocker-first review ledger and resolved critical findings.

- [ ] **Step 1: Review by failure boundary**

Audit contract/provenance, database persistence/idempotency, sharing/expiry, form lifecycle, privacy/retention, error taxonomy, build identity, migration compatibility, and CI/package behavior against the original acceptance criteria.

- [ ] **Step 2: Classify every finding**

Record `Critical`, `Important`, or `Minor`, with exact path/line and impact. Fix Critical and Important findings before proceeding; document accepted Minor findings in the packet.

- [ ] **Step 3: Reconcile the pre-existing dirty files**

Verify whether the `App.test.tsx` additions in `eslint.config.js` and `tsconfig.json` are required release coverage. If required, test and commit them explicitly; otherwise preserve them unstaged and disclose them as unrelated workspace state.

- [ ] **Step 4: Commit packet and reviewed corrections explicitly**

Stage only named paths and use focused commits. Never stage `test-results/`.

### Task 5: Run Fresh Release Verification

**Files:**
- Inspect: exact committed tree and generated evidence.

**Interfaces:**
- Consumes: reviewed branch state.
- Produces: fresh evidence for the final status and pushed SHA.

- [ ] **Step 1: Run all local gates**

```bash
/Users/henrirapson/autosafe/.venv/bin/python -m pytest tests/ -q
npm run typecheck
npm run lint
npm run test
npm run build
npx playwright test
/Users/henrirapson/autosafe/.venv/bin/python scripts/check_openapi_drift.py
/Users/henrirapson/autosafe/.venv/bin/python scripts/claim_sweep.py
docker compose -f docker-compose.staging.yml config --quiet
docker build --build-arg GIT_SHA=$(git rev-parse HEAD) -t autosafe-rc-verification .
```

Expected: every command exits 0 with exact pass counts captured.

- [ ] **Step 2: Verify commit and worktree provenance**

Confirm every intended change is committed, any unrelated files remain explicitly unstaged, the branch is based on `657930d5`, and `git diff --check` is clean.

- [ ] **Step 3: Push the reviewed SHA**

Push `release/product-truth-rc1` without force. Record the exact SHA.

- [ ] **Step 4: Wait for all seven PR checks**

Use `gh pr checks 32 --watch` and verify `test`, `contract-drift`, `security-check`, `build-check`, `frontend-build`, `e2e`, and `staging-evidence` are all successful on the exact pushed SHA.

- [ ] **Step 5: Finalize the handoff**

Update only evidence status in `FINAL_RELEASE_REPORT.md` if the live checks differ from the pre-push state, commit/push that factual update, re-verify checks, and return the executive verdict. Keep PR #32 draft unless Henri separately authorizes ready-for-review or merge actions.
