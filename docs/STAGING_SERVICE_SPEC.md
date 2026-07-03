# Staging Railway Service — specification (owner decision D6)

**Purpose:** the only reliable way to catch `.railwayignore` artifact-stripping
and Railway-specific deploy semantics before production (`.dockerignore` ≠
`.railwayignore`; a local docker build cannot catch a Railway strip — this
class of failure has shipped before: the calibrator.json hotfix `91f637f`).
Required by REMEDIATION_PLAN task 3.3's acceptance test.

## Ruling (Henri, 2026-07-03)
Separate staging Railway service — **not** production, **not** a fork pointed
at production data. Separate env vars, separate database, anonymized or
synthetic lead data only.

## Setup (once `railway login` is done)

1. **New Railway project** `autosafe-staging` (separate project, not an
   environment of the prod project — hard blast-radius isolation), deploying
   branch `staging` of `hrapson-spec/autosafe_backend`.
2. **Separate PostgreSQL** provisioned inside that project. Seed with:
   - schema only (`create_risk_checks_table.py`, `create_leads_table.py`,
     migrations) — no production rows, ever;
   - `scripts/seed_staging_data.py` (to be written with 2.3): ~200 synthetic
     `risk_checks` rows + ~20 synthetic garages/leads (faker-generated VRMs
     like `ZZ99 ZZZ` that cannot collide with real plates; obviously-fake
     emails `staging+n@example.invalid`).
3. **Env vars — deliberately different from prod:**
   - `DATABASE_URL` → the staging PG (never the prod URL)
   - `DVSA_*` → leave UNSET initially; staging exercises deploy/bundle/load
     semantics, and the DVSA-dependent paths fall back exactly as prod does
     on DVSA outage (that fallback behaviour is itself worth staging). If a
     staging DVSA key is later granted, set it here — never share the prod
     key (429 lockout on the shared key would take down live prediction).
   - `ADMIN_API_KEY`, `VRM_HMAC_KEY` → fresh random values, not prod's.
   - `PREDICTIONS_ENABLED=true`, `V58_PREDICTIONS_ENABLED=true` (staging
     enables what prod gates).
   - No Resend key (emails must not send from staging) — `RESEND_API_KEY`
     unset; lead-email code already no-ops without it (verify in canary).
4. **No public traffic:** Railway-generated domain only, `robots.txt`
   deny-all served when `RAILWAY_ENVIRONMENT != production` (small main.py
   guard, rides the 3.3 PR), and no DNS.

## What runs against staging (gate wiring)
- 3.3 acceptance: push branch → staging deploys → automated probe asserts
  `/health` reports v58 bundle loaded + `model_version` — catching
  railwayignore strips pre-production.
- 2.3 capture canary rehearsal: run `migrations/*` + `post_deploy_canary.py`
  against staging first.
- Rollback drills (3.8): kill-switch flips, `MODEL_VERSION` ladder, forced
  bundle-corruption refusal — all rehearsed here.

## Cost
One hobby-tier service + one small PG. If idle cost matters, the service can
be scaled to zero between gate runs; deploys wake it.
