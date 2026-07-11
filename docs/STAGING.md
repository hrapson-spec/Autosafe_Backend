# Staging

Use an isolated environment for release acceptance before any production
decision. Local staging is intentionally small; GitHub staging builds the real
image from a clean checkout.

## Local real-PostgreSQL path (low disk)

The application image is large, so a low-disk Mac should run only a named
`postgres:16-alpine` container and start Uvicorn from the project virtualenv.
Use a unique container name/port and remove only that container afterwards.

```bash
SHA="$(git rev-parse HEAD)"
BUILD_TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

npm run build

docker run -d --name autosafe-rc-staging-pg -p 55432:5432 \
  -e POSTGRES_PASSWORD=staging_only_pw postgres:16-alpine

export DATABASE_URL=postgresql://postgres:staging_only_pw@127.0.0.1:55432/postgres
export VRM_HMAC_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export BASE_URL=http://127.0.0.1:8100
export GIT_SHA="$SHA"
export FRONTEND_BUILD_SHA="$SHA"
export BUILD_TIMESTAMP

python create_risk_checks_table.py
python create_leads_table.py
python migrations/add_report_contract_columns.py
python scripts/seed_staging_data.py
python build_db.py

uvicorn main:app --host 127.0.0.1 --port 8100 --no-access-log
```

In another shell with the same `DATABASE_URL`:

```bash
SHA="$(git rev-parse HEAD)"
python scripts/staging_acceptance.py \
  --base-url http://127.0.0.1:8100 \
  --expect-frontend-bundle true \
  --expected-backend-sha "$SHA"
```

DVSA and admin credentials remain unset, so this path uses the explicit demo
vehicle source while exercising real PostgreSQL persistence. Demo provenance is
asserted; it must never be mistaken for production DVSA evidence.

Stop Uvicorn, then remove the disposable container:

```bash
docker stop autosafe-rc-staging-pg
docker rm autosafe-rc-staging-pg
```

## Retention rehearsal

Seed synthetic check records, leads, and lead assignments older than one month,
then run both jobs against the disposable database:

```bash
python scripts/retention_sweep.py --months 1
python scripts/retention_sweep.py --months 1 --execute --batch 50
python scripts/lead_retention_sweep.py --months 1
python scripts/lead_retention_sweep.py --months 1 --execute --batch 50
```

Required evidence: dry runs mutate nothing; check execution removes plaintext
VRN/postcode/payload/token and leaves the keyed digest; lead execution deletes
assignments before leads; both verification queries report zero stale rows; no
output contains the seeded identifiers or contact data.

## GitHub clean-image path

The `staging-evidence` job in `.github/workflows/ci.yml`:

1. checks out the exact GitHub SHA;
2. runs `docker compose ... up --build --wait app`, which completes migration
   and seed dependencies and starts the built application;
3. runs the acceptance service separately with `docker compose ... run --rm
   --build --no-deps acceptance`, so the acceptance exit status is the gate;
4. prints bounded logs on failure; and
5. always tears down the disposable volumes.

It does not use `--exit-code-from acceptance` on the initial `up`: the migration
container is a successful one-shot service, and attached exit propagation would
stop the stack before acceptance.

Validate configuration without building locally:

```bash
GIT_SHA="$(git rev-parse HEAD)" docker compose -f docker-compose.staging.yml config
```

## Environment

Required: `DATABASE_URL`, `VRM_HMAC_KEY` (at least 32 characters), `BASE_URL`,
`GIT_SHA`, `FRONTEND_BUILD_SHA`, `BUILD_TIMESTAMP`, and `PORT` where the runner
requires it. Production-only credentials remain absent from local staging.

## Acceptance coverage

`scripts/staging_acceptance.py` checks health/readiness/version identity,
frontend bundle hashing, OpenAPI/legacy route shape, report create/persist,
idempotent replay and conflict, retrieval, expiry/error taxonomy, normalisation,
and real PostgreSQL evidence selection. PostgreSQL and SQLite are expected to
use the same weighted cohort arithmetic; only their declared source differs.

Playwright covers browser lifecycle and sharing separately against the built
SPA. Railway source stripping, proxy logs, public routing, and consent behaviour
can be closed only on the isolated Railway candidate.
