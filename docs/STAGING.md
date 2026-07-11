# Staging

Isolated environment for release acceptance testing before a Railway deploy.
Two ways to run it — pick the one that matches where you are.

## Run locally (small, no image build)

The application Docker image is multi-GB (CatBoost, PaddleOCR, OpenCV) — do
not build it on a low-disk dev machine. Local staging runs a small
`postgres:16-alpine` container plus `uvicorn` directly from this worktree's
`.venv`:

```bash
docker run -d --name autosafe-rc-staging-pg -p 55432:5432 \
    -e POSTGRES_PASSWORD=staging_only_pw postgres:16-alpine
export DATABASE_URL=postgresql://postgres:staging_only_pw@localhost:55432/postgres

python create_risk_checks_table.py
python create_leads_table.py
python migrations/add_report_contract_columns.py
# create+seed a small mot_risk fixture (make='ZZTEST MODEL') — see
# scripts/staging_acceptance.py's check 4j docstring for the exact seed —
# so the Postgres evidence-ladder path has real rows to serve.
python scripts/seed_staging_data.py
python build_db.py   # SQLite fallback at /tmp/autosafe.db, mirrors the Docker CMD

export VRM_HMAC_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
export BASE_URL=http://127.0.0.1:8100
uvicorn main:app --host 127.0.0.1 --port 8100   # DVSA_*/ADMIN_API_KEY unset -> demo mode

python scripts/staging_acceptance.py --base-url http://127.0.0.1:8100
```

Stop and remove the container when done: `docker stop autosafe-rc-staging-pg && docker rm autosafe-rc-staging-pg`.

## Run in CI

`docker-compose.staging.yml` builds the real application image and runs the
same bootstrap + acceptance suite as containers (`migrate` → `app` → `acceptance`,
gated with `--exit-code-from acceptance`). This is the path CI wires up; it
needs more disk/CPU than the local recipe above, which is why the two paths
differ. Validate the compose file itself with `docker compose -f
docker-compose.staging.yml config` (do not `up` it on a low-disk machine).

## Env vars

`DATABASE_URL`, `VRM_HMAC_KEY` (>= 32 chars), `BASE_URL`, `PORT`. DVSA_* and
`ADMIN_API_KEY` are deliberately unset in staging (demo mode; admin routes
return 503/401, exercised separately with a brief restart when needed).

## What `scripts/staging_acceptance.py` asserts

Health/readiness/version, `openapi.json` contract shape (legacy `/api/risk`
untouched, v2 routes typed), the v2 create → idempotency → fetch → error-taxonomy
flow, legacy `/api/risk` `/api/vehicle` `/api/stats` byte-shape parity, and the
Postgres-vs-SQLite evidence-ladder split. Exits non-zero on any failed check.
Retention-sweep rehearsal and the legacy/v2 rate-limit shape checks are run as
separate steps (they need an `ADMIN_API_KEY` restart / rapid-fire requests) —
see the release evidence packet for that walkthrough.
