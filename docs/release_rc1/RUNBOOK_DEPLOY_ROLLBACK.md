# RC1 deploy and rollback runbook

## Authority boundary

This runbook is executable only after the owner records GO in
`DECISION_RECORD.md`. Merging to `main` triggers production deployment; do not
merge as a test. Use an isolated Railway candidate service first.

## 1. Freeze one candidate

```bash
git fetch origin
git switch release/product-truth-rc1
git pull --ff-only
git status --short
git rev-parse HEAD
```

The worktree must be clean. Record the full SHA. Every local result, GitHub
check, Docker image, Railway deployment, and `/api/version` response must refer
to that same SHA.

## 2. Run the hard local gates

```bash
pytest tests/ -q
npm run typecheck
npm run lint
npm test -- --run
npm run build
python scripts/check_openapi_drift.py
python scripts/claim_sweep.py
GIT_SHA="$(git rev-parse HEAD)" docker compose -f docker-compose.staging.yml config
```

For real-Postgres acceptance, use the disposable local recipe in
`docs/STAGING.md`, or the CI stack:

```bash
export GIT_SHA="$(git rev-parse HEAD)"
docker compose -f docker-compose.staging.yml up --build --wait app
docker compose -f docker-compose.staging.yml run --rm --build --no-deps acceptance
docker compose -f docker-compose.staging.yml logs app migrate postgres
docker compose -f docker-compose.staging.yml down -v --remove-orphans
```

Teardown runs even after a failed acceptance attempt. Do not remove unrelated
containers or volumes.

## 3. Check configuration without exposing values

Required production settings include:

- `DATABASE_URL`
- `BASE_URL=https://www.autosafe.one`
- `VRM_HMAC_KEY` with at least 32 random characters
- DVSA credentials required by the live data path
- `CORS_ORIGINS`, `PORT`, and existing service credentials

Verify presence and length only. Never paste secrets, raw VRNs, postcodes,
request bodies, or share tokens into the PR or release packet.

## 4. Prepare the database

Back up the production database and record the restore point. Preview and then
run the expand migration against the candidate database first:

```bash
python migrations/add_report_contract_columns.py --dry-run
python migrations/add_report_contract_columns.py
: "${NOTICE_EFFECTIVE_AT:?set the owner-approved privacy-notice timestamp}"
python migrations/pseudonymize_backlog.py --before "$NOTICE_EFFECTIVE_AT"
python scripts/retention_sweep.py
```

Both privacy commands are dry-run by default. Run them with `--execute` only
after the key, timestamp, row counts, and dry-run output are reviewed. Configure
the retention sweep as a scheduled job and alert on a non-zero exit or stale
plaintext rows.

## 5. Railway candidate deploy

Deploy the exact SHA to an isolated Railway service and separate database.
Complete every item in `RAILWAY_STRIP_RISK_MEMO.md`. Run the staging acceptance
script against the public candidate URL with the expected SHA, then exercise
fresh-context and reload sharing in a real browser.

## 6. Pre-merge decision

Confirm:

- the local gates are green on the candidate SHA;
- all required GitHub Actions checks are green on that SHA;
- the full diff and release packet have approval;
- the Railway candidate evidence is attached and D6 is owner-accepted;
- monitoring and rollback ownership are assigned; and
- `FINAL_RELEASE_REPORT.md` says GO.

The owner must then explicitly authorise merging PR #32.

## 7. Production canary

After the authorised merge and Railway deployment:

1. Verify Railway and `GET /api/version` identify the merged SHA.
2. Verify `/health`, `/`, `/app`, and static assets.
3. Create one synthetic report with non-personal test data.
4. Open its opaque share URL in a fresh browser and after reload.
5. Confirm the row exists, expiry is set, and no URL/log contains VRN or
   postcode.
6. Check typed error/degraded behaviour without creating customer impact.
7. Monitor the RC1 events and service health for 48 hours as described in
   `docs/MONITORING.md`.

## 8. Rollback triggers

Rollback immediately for identity mismatch, migration errors, sustained 5xx,
report creation/retrieval failure, false evidence presentation, PII leakage,
or an unexplained persistence failure rate above the agreed threshold.

## 9. Application rollback

1. Stop new deployment activity and record the incident timestamp.
2. Redeploy the last known-good Railway image/commit.
3. Verify `/api/version`, `/health`, and the legacy user path.
4. Preserve logs and affected report IDs without copying PII into tickets.
5. Leave the additive database schema in place. The old application can ignore
   nullable new columns.

Do **not** restore `registration NOT NULL` during an incident. The migration
marks that constraint as a one-way compatibility change
(`migrations/add_report_contract_columns.py:1-37`, `:47-84`). Column/index
removal is a later, separately reviewed cleanup—not an emergency rollback.

## 10. Data/privacy incident path

If raw identifiers, tokens, or request bodies appear in logs or analytics,
disable the affected route or deployment, preserve restricted evidence,
rotate exposed secrets/tokens where applicable, notify the privacy owner, and
follow the incident process before resuming traffic.
