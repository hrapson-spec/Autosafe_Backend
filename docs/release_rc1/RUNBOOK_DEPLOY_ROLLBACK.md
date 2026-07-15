# RC1 deploy and rollback runbook

## Authority boundary

This runbook authorises no production action. Merging PR #32 to `main` triggers
production deployment, so merge requires an explicit owner GO after all gates.
Use an isolated Railway service and separate database for candidate evidence.

## 1. Freeze one candidate

```bash
git fetch origin
git switch release/product-truth-rc1
git pull --ff-only
git status --short
git rev-parse HEAD
```

The worktree must be clean. Record the full 40-character SHA. Every local
result, GitHub check, Docker image, candidate deployment, and `/api/version`
response must refer to that SHA. Any source change creates a new candidate and
invalidates the previous identity/staging evidence.

## 2. Run local gates

```bash
pytest tests/ -q
npm run typecheck
npm run lint
npm test
npm run build
npx playwright test
python scripts/check_openapi_drift.py
python scripts/claim_sweep.py
python migrations/add_report_contract_columns.py --dry-run
python migrations/add_report_contract_columns.py --rollback --dry-run
GIT_SHA="$(git rev-parse HEAD)" docker compose -f docker-compose.staging.yml config
```

Build from a clean generated-output directory twice and compare the
64-character `frontend_bundle_hash` returned by `/api/version`; both builds must
match. Do not count a build that reused untracked output as reproducibility
evidence.

## 3. Run real-PostgreSQL acceptance

Use the low-disk local recipe in `docs/STAGING.md`, or let GitHub Actions build
the complete container stack. The acceptance invocation must bind identity:

```bash
SHA="$(git rev-parse HEAD)"
python scripts/staging_acceptance.py \
  --base-url http://127.0.0.1:8100 \
  --expect-frontend-bundle true \
  --expected-backend-sha "$SHA"
```

Against the same disposable database, seed old synthetic records and rehearse
both jobs in dry-run and execute modes:

```bash
python scripts/retention_sweep.py --months 1
python scripts/retention_sweep.py --months 1 --execute --batch 50
python scripts/lead_retention_sweep.py --months 1
python scripts/lead_retention_sweep.py --months 1 --execute --batch 50
```

Review verification counts and then destroy only the named disposable database
container/volume. Never remove unrelated local containers or volumes.

## 4. Check configuration without exposing values

Required deployed settings include:

- `DATABASE_URL`
- `BASE_URL=https://www.autosafe.one` in production
- `VRM_HMAC_KEY` with at least 32 random characters
- optional separate `VRM_LOG_HMAC_KEY` with equivalent strength
- DVSA credentials for the live source path
- an exact production `CORS_ORIGINS` allowlist
- `PORT` and existing mail/administrative credentials

Verify presence, scope, and minimum length only. Never paste values, raw VRNs,
postcodes, request bodies, email content, or share tokens into the PR or packet.

## 5. Prepare the candidate database

Back up the target and record a restore point. Preview, apply, and verify the
expand migration on the isolated candidate database:

```bash
python migrations/add_report_contract_columns.py --dry-run
python migrations/add_report_contract_columns.py
```

Confirm columns/indexes and that `risk_checks.registration` and
`leads.postcode` are nullable. If backlog remediation applies, set the
owner-approved corrected-notice timestamp and review the dry run before using
`--execute`:

```bash
: "${NOTICE_EFFECTIVE_AT:?set the owner-approved notice timestamp}"
python migrations/pseudonymize_backlog.py --before "$NOTICE_EFFECTIVE_AT"
```

The rolling check and lead scripts are also dry-run by default. Production
`--execute` requires separate owner approval, reviewed counts, named job
ownership, and alerts for failed/missed runs and stale-row verification.

## 6. Deploy the isolated Railway candidate

Deploy the exact SHA to a non-production Railway service and separate database.
Complete `RAILWAY_STRIP_RISK_MEMO.md`, then:

1. Verify `/health`, `/ready`, `/openapi.json`, and `/api/version`.
2. Confirm backend SHA, frontend SHA, build timestamp, contract version, and
   full bundle hash identify the candidate.
3. Run `scripts/staging_acceptance.py` against the public URL with
   `--expected-backend-sha`.
4. Exercise `/` and `/app`, both UI variants, typed failures, a fresh report,
   idempotent retry, fresh-context share, reload, copy, and WhatsApp sharing.
5. Inspect browser requests plus Railway edge/proxy/application/database logs
   using synthetic values. Confirm no raw VRN, postcode, request body, email
   content, or bearer token appears.
6. Verify Google code does not load before consent, Umami does not auto-track,
   and saved-report routes send no custom analytics.

## 7. Pre-merge decision

Confirm all local and GitHub gates are green on the exact SHA, the full diff and
packet are approved, Railway D7 is complete, retention/monitoring/rollback have
owners, and `FINAL_RELEASE_REPORT.md` says GO. The owner must then explicitly
authorise merging PR #32.

## 8. Production canary (only after authorised merge)

1. Verify Railway and `/api/version` identify the merged SHA and bundle.
2. Verify `/health`, `/ready`, `/`, `/app`, and static assets.
3. Create one synthetic report using approved non-customer test data.
4. Open the share URL in a fresh context and after reload.
5. Confirm the row exists, expiry is set, and no URL/log/analytics event leaks
   identifiers or the bearer token.
6. Check typed error/degraded behaviour without customer impact.
7. Run the watch cadence in `docs/MONITORING.md` for 48 hours.

## 9. Rollback triggers

Rollback immediately for source/bundle identity mismatch, migration failure,
sustained 5xx, report create/retrieval failure, false evidence presentation,
raw identifier/token leakage, or unexplained persistence failures above the
agreed threshold.

## 10. Application rollback

1. Stop deployment activity and record the incident timestamp.
2. Redeploy the last known-good image/commit.
3. Verify `/api/version`, `/health`, `/ready`, and the legacy user path.
4. Preserve restricted logs and report IDs without copying PII into tickets.
5. Leave the additive schema in place; the old application ignores new nullable
   columns.

Do not restore `risk_checks.registration NOT NULL` or `leads.postcode NOT NULL`
during an incident. Column/index removal is a later, separately reviewed
cleanup, not an emergency rollback.

## 11. Privacy incident path

If identifiers, bearer tokens, credentials, request bodies, or email content
appear in logs or analytics, stop the affected path/deployment, preserve
restricted evidence, rotate exposed credentials or invalidate affected reports
where feasible, notify the privacy owner, assess notification obligations, and
complete the incident process before resuming traffic.
