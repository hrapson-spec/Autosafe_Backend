# V55 Release Follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the baked Railway frontend identity, document a privacy-safe production-canary policy, and complete the V55 48-hour watch without creating more production report rows.

**Architecture:** A small shell helper chooses Railway's provided Git commit at Docker build time and falls back to the existing local `GIT_SHA`; the Dockerfile remains the only image builder. Release documentation defines the canary-data boundary. Production verification and scheduled checkpoints reuse restricted saved-report tokens and treat unavailable monitoring evidence as unknown, never green.

**Tech Stack:** Docker multi-stage builds, POSIX shell, Python 3.11/pytest, FastAPI release metadata, GitHub Actions, Railway, Codex local automations.

## Global Constraints

- Do not change the V55 prediction contract or inference behaviour.
- Do not create a production `FRONTEND_BUILD_SHA` override merely to make labels agree.
- Do not commit a real registration, dealer-listing URL, report token, postcode, or other restricted release evidence.
- Production canaries may use only an owner-approved current public dealer listing that was not supplied by an AutoSafe customer or user.
- Routine monitoring checkpoints are read-only and must not create additional production report rows.
- Missing dashboards, provider IDs, logs, or metrics are evidence gaps, not successful checks.
- Use the V55 deployment timestamp `2026-07-17T15:37:07Z` for the 4-hour, 24-hour, and 48-hour checkpoints.

---

### Task 1: Bake the Railway Git identity into the frontend image

**Files:**
- Create: `scripts/write_frontend_identity.sh`
- Modify: `Dockerfile:1-12,35-40,43-75`
- Modify: `tests/test_ci_workflow.py:25-40`

**Interfaces:**
- Consumes: build-time `RAILWAY_GIT_COMMIT_SHA` and local/CI `GIT_SHA`.
- Produces: `scripts/write_frontend_identity.sh OUTPUT_PATH`, which writes exactly one SHA plus a newline; `.frontend_sha` in the runtime image.

- [ ] **Step 1: Add failing build-identity tests**

Append these imports and tests to `tests/test_ci_workflow.py`:

```python
import os
import subprocess


IDENTITY_WRITER = Path(__file__).parents[1] / "scripts" / "write_frontend_identity.sh"


def _write_frontend_identity(tmp_path, *, railway_sha=None, git_sha=None):
    output = tmp_path / ".frontend_sha"
    env = {"PATH": os.environ["PATH"]}
    if railway_sha is not None:
        env["RAILWAY_GIT_COMMIT_SHA"] = railway_sha
    if git_sha is not None:
        env["GIT_SHA"] = git_sha
    subprocess.run(["sh", str(IDENTITY_WRITER), str(output)], check=True, env=env)
    return output.read_text(encoding="utf-8")


def test_frontend_identity_prefers_railway_git_commit_sha(tmp_path):
    assert _write_frontend_identity(
        tmp_path, railway_sha="railway-sha", git_sha="local-sha"
    ) == "railway-sha\n"


def test_frontend_identity_falls_back_to_local_git_sha(tmp_path):
    assert _write_frontend_identity(tmp_path, git_sha="local-sha") == "local-sha\n"


def test_dockerfile_consumes_railway_git_commit_in_frontend_stage():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    frontend_stage = dockerfile.split("FROM node:20-slim AS frontend", 1)[1].split(
        "FROM python:3.11-slim", 1
    )[0]
    assert "ARG RAILWAY_GIT_COMMIT_SHA" in frontend_stage
    assert "write_frontend_identity.sh" in frontend_stage
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
python3 -m pytest tests/test_ci_workflow.py -q
```

Expected: the new tests fail because `scripts/write_frontend_identity.sh` and the Railway build argument do not exist.

- [ ] **Step 3: Add the deterministic identity writer**

Create `scripts/write_frontend_identity.sh`:

```sh
#!/bin/sh
set -eu

output_path=${1:?usage: write_frontend_identity.sh OUTPUT_PATH}
frontend_sha=${RAILWAY_GIT_COMMIT_SHA:-${GIT_SHA:-unknown}}
printf '%s\n' "$frontend_sha" > "$output_path"
```

- [ ] **Step 4: Wire the helper into the Docker build**

Make these exact structural changes to `Dockerfile`:

```dockerfile
ARG GIT_SHA=unknown
ARG RAILWAY_GIT_COMMIT_SHA

FROM node:20-slim AS frontend
ARG GIT_SHA
ARG RAILWAY_GIT_COMMIT_SHA
```

Copy and invoke the helper after the frontend build:

```dockerfile
COPY scripts/write_frontend_identity.sh ./scripts/write_frontend_identity.sh
RUN RAILWAY_GIT_COMMIT_SHA="$RAILWAY_GIT_COMMIT_SHA" GIT_SHA="$GIT_SHA" \
    sh ./scripts/write_frontend_identity.sh /build/.frontend_sha \
    && date -u +'%Y-%m-%dT%H:%M:%SZ' > /build/.build_timestamp
```

Delete the old `RUN printf ...` identity command. In the runtime stage, declare both arguments while preserving the local runtime fallback:

```dockerfile
ARG GIT_SHA
ARG RAILWAY_GIT_COMMIT_SHA
ENV GIT_SHA=${GIT_SHA}
```

- [ ] **Step 5: Run the focused and privacy tests**

Run:

```bash
python3 -m pytest tests/test_ci_workflow.py tests/test_privacy_surfaces.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit the independently testable build fix**

```bash
git add Dockerfile scripts/write_frontend_identity.sh tests/test_ci_workflow.py
git commit -m "fix: bake Railway commit into frontend identity"
```

---

### Task 2: Document and guard the production-canary privacy policy

**Files:**
- Modify: `docs/release_rc1/RUNBOOK_DEPLOY_ROLLBACK.md:135-145`
- Modify: `docs/RUNBOOK.md:9-31`
- Modify: `docs/RELEASE_BUILD.md:31-43`
- Modify: `tests/test_privacy_surfaces.py`

**Interfaces:**
- Consumes: the production-canary workflow in the authoritative RC1 runbook.
- Produces: a policy that permits restricted, owner-approved public-dealer canaries without persisting registrations in Git.

- [ ] **Step 1: Add a failing documentation-policy test**

Append to `tests/test_privacy_surfaces.py`:

```python
import re


def test_production_canary_policy_keeps_real_registrations_out_of_git():
    runbook = (ROOT / "docs" / "release_rc1" / "RUNBOOK_DEPLOY_ROLLBACK.md").read_text(
        encoding="utf-8"
    )
    canary = runbook.split("## 8. Production canary", 1)[1].split(
        "## 9. Rollback triggers", 1
    )[0]

    assert "current public dealer listing" in canary
    assert "not supplied by an AutoSafe customer or user" in canary
    assert "restricted release evidence" in canary
    assert "release-owner approval" in canary
    assert "Create one synthetic report" not in canary
    assert re.search(r"\b[A-Z]{2}[0-9]{2}\s?[A-Z]{3}\b", canary) is None
```

- [ ] **Step 2: Run the policy test and verify it fails**

Run:

```bash
python3 -m pytest tests/test_privacy_surfaces.py::test_production_canary_policy_keeps_real_registrations_out_of_git -q
```

Expected: FAIL because the current runbook still says to create a synthetic production report and does not define the public-listing restrictions.

- [ ] **Step 3: Replace the production-canary step with the approved policy**

In `docs/release_rc1/RUNBOOK_DEPLOY_ROLLBACK.md`, replace step 3 and retain the existing numbering:

```markdown
3. For a live DVSA/V55 canary, use a vehicle from a current public dealer
   listing only after release-owner approval. It must not have been supplied by
   an AutoSafe customer or user. Keep the registration, listing URL, approval,
   and check timestamp only in restricted release evidence; never put them in
   Git, public tickets, analytics, or unrestricted logs. Staging and automated
   fixtures remain synthetic.
```

Add a concise `Production canary data` section to `docs/RUNBOOK.md` with the same four requirements and a pointer to the authoritative RC1 procedure. Update `docs/RELEASE_BUILD.md` to state that Railway's `RAILWAY_GIT_COMMIT_SHA` is consumed at build time and `GIT_SHA` remains the local fallback.

- [ ] **Step 4: Run documentation and privacy gates**

Run:

```bash
python3 -m pytest tests/test_privacy_surfaces.py tests/test_ci_workflow.py -q
python3 check_internal_links.py
python3 scripts/claim_sweep.py
```

Expected: all commands exit 0.

- [ ] **Step 5: Scan the candidate diff for restricted identifiers**

Run:

```bash
git diff --check
git diff --name-only
git diff --no-color | rg -n "report_token[\"':=[:space:]]+[A-Za-z0-9_-]{16,}|/app/report/[A-Za-z0-9_-]{16,}|[A-Z]{2}[0-9]{2}[[:space:]]?[A-Z]{3}"
```

Expected: the final scan produces no output. If it matches synthetic examples outside the changed canary section, narrow the scan to the changed lines and verify no real registration or report token was added.

- [ ] **Step 6: Commit the documentation policy**

```bash
git add docs/release_rc1/RUNBOOK_DEPLOY_ROLLBACK.md docs/RUNBOOK.md docs/RELEASE_BUILD.md tests/test_privacy_surfaces.py
git commit -m "docs: define privacy-safe production canaries"
```

---

### Task 3: Verify, publish, deploy, and complete the monitoring watch

**Files:**
- Modify: no repository files after Tasks 1-2.
- Restricted local state: Codex automation definitions for the 4-hour, 24-hour, and 48-hour checkpoints.

**Interfaces:**
- Consumes: the candidate branch, GitHub CI, Railway production service `autosafe-rc1-app`, the existing restricted prediction/comparison report tokens, and deployment timestamp `2026-07-17T15:37:07Z`.
- Produces: merged/deployed build identity fix, immediate checkpoint evidence, and three scheduled read-only checkpoint jobs.

- [ ] **Step 1: Run fresh complete local verification**

Run:

```bash
python3 -m pytest tests/ -q
npm test -- --reporter=dot
python3 scripts/claim_sweep.py
python3 check_internal_links.py
npm run typecheck
npm run lint
npm run build
```

Expected: every command exits 0. Report exact test totals rather than “about” counts.

- [ ] **Step 2: Publish the candidate through a pull request**

Create branch `fix/v55-release-followups` from the current approved-spec commit, push it, and open a PR whose body states:

- Railway's Git SHA is now baked into `.frontend_sha`;
- local `GIT_SHA` and staging `FRONTEND_BUILD_SHA` behaviour remain supported;
- the canary policy stores real registrations only in restricted release evidence; and
- no V55 contract or inference code changed.

Expected: the PR contains the approved spec, implementation plan, two implementation commits, and no restricted identifiers.

- [ ] **Step 3: Require all CI jobs green on the exact head SHA, then merge**

Run GitHub checks against the exact PR head SHA. Do not merge while any check is pending, skipped unexpectedly, or failing. After all seven required jobs succeed, merge the PR and record the merge SHA.

- [ ] **Step 4: Verify the Railway deployment and live release identity**

Poll deployment status until the merge SHA is live, then fetch:

```bash
curl -fsS https://www.autosafe.one/health
curl -fsS https://www.autosafe.one/ready
curl -fsS https://www.autosafe.one/api/version
```

Expected: health and readiness are `ok`; `backend_sha` and `frontend_sha` both equal the merge SHA; `frontend_bundle_hash` is 64 hexadecimal characters; `build_timestamp` is from the new deployment.

- [ ] **Step 5: Run the immediate read-only monitoring checkpoint**

Using the two tokens from restricted release evidence, GET both saved reports and verify:

- the V55 report remains `vehicle_prediction` / `model_v55` / `model_prediction`;
- the pre-deploy report remains `comparison`;
- both are still saved and shareable; and
- the V55 page renders the truthful above-50% copy on desktop and at 390px without horizontal overflow.

Inspect Railway logs since `2026-07-17T15:37:07Z` for the documented persistence, typed-error, invalid-replay, and 5xx markers. Record every unavailable metric/dashboard/provider ID as an evidence gap.

- [ ] **Step 6: Schedule the remaining checkpoints**

Create three active, one-run local Codex automations against `/Users/henrirapson/autosafe-rc` for:

- 4-hour checkpoint: `2026-07-17T19:37:07Z`;
- 24-hour checkpoint: `2026-07-18T15:37:07Z`; and
- 48-hour checkpoint: `2026-07-19T15:37:07Z`.

Each prompt must repeat the read-only checks from Step 5, prohibit POST requests and production-row creation, keep report tokens out of the automation name and output, and classify unavailable evidence as unknown. Store the required tokens only in the local automation prompt as restricted operational state, never in Git.

- [ ] **Step 7: Final verification and handoff**

Confirm:

- production serves the merge SHA with matching backend/frontend identity;
- both existing saved result kinds replay unchanged;
- the immediate checkpoint is complete;
- all three automation definitions are active at the intended UTC times; and
- the working tree is clean and local `main` matches `origin/main`.

Report the PR, merge SHA, live identity, exact test totals, checkpoint status, automation names/times, and any evidence gaps.
