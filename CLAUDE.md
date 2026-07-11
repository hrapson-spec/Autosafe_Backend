# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AutoSafe is a UK MOT failure risk service. **Current RC1 user path (truth):**
the React UI calls `POST /api/v2/reports`; the backend resolves recorded MOT
history and returns the closest supported comparable-vehicle rate with explicit
mileage/evidence provenance, persistence state, and an opaque share token. It
is not a diagnosis or an exact-vehicle prediction. `GET /api/v2/reports/{token}`
restores a persisted share. Legacy `/api/vehicle`, `/api/risk`, and
`/api/risk/v55` routes remain compatibility surfaces but are not the RC1
browser flow. `scripts/claim_sweep.py` enforces the public-claim boundary in CI.
The release packet is `docs/release_rc1/README.md`; the earlier remediation plan
of record is `work/reviews/REMEDIATION_PLAN_2026-07-03.md`.

**Production URL:** https://www.autosafe.one
**Deployment:** Railway.app (auto-deploys from `main` branch)
**GitHub:** https://github.com/hrapson-spec/autosafe_backend

## Commands

```bash
# Backend (from project root)
uvicorn main:app --reload                    # Dev server on port 8000
pip install -r requirements.txt              # Install Python deps

# Frontend (from project root)
npm ci                                       # Install exact locked Node deps
npm run dev                                  # Vite dev server on port 3000
npm run build                                # Production build

# Tests
pytest tests/ -v                             # All backend tests
pytest tests/test_api.py -v                  # API endpoint tests
pytest tests/test_banding.py -v              # Single test file
npm run typecheck                            # TypeScript gate
npm run lint                                 # ESLint gate
npm test                                     # Vitest suite
npm run test:e2e                             # Playwright against configured server
python scripts/check_openapi_drift.py        # Contract snapshot gate
python scripts/claim_sweep.py                # Public truth gate

# Linting
flake8 main.py database.py --max-line-length=120 --ignore=E501,W503
ruff check . --ignore E501
mypy --ignore-missing-imports .              # Non-blocking

# ML model training (from work/ directory)
python train_catboost_production_v55.py      # Train V55 model
python calculate_ensemble_auc.py             # Evaluate ensemble AUC

# Research tests (from work/ directory)
pytest tests/property_tests.py -v            # Property-based invariants
pytest tests/as_of_validation.py -v          # History feature correctness

# Docker
docker build --build-arg GIT_SHA=$(git rev-parse HEAD) -t autosafe:rc .
GIT_SHA=$(git rev-parse HEAD) docker compose -f docker-compose.staging.yml config
# Staging/deploy/rollback: docs/release_rc1/RUNBOOK_DEPLOY_ROLLBACK.md

# Schema and privacy operations (dry-run unless explicitly noted)
python migrations/add_report_contract_columns.py --dry-run
python scripts/retention_sweep.py
```

## Architecture

### System Flow
```
Browser (React 19 + Vite + Tailwind)
  → FastAPI backend (main.py)
    → v2 report contract (report_routes.py + report_service.py)
    → DVSA MOT History API (OAuth 2.0, via dvsa_client.py)
    → explicit comparable-evidence ladder (PostgreSQL / SQLite source data)
    → PostgreSQL report persistence and opaque-token retrieval
  → versioned JSON response with provenance, nullable unsupported detail,
    persistence state, and share URL
```

### Directory Layout

- **Root level** — Production backend Python code (main.py, database.py, model_v55.py, etc.). This is the ONLY deploy source: the root Dockerfile does `COPY . .` and runs `uvicorn main:app`; Railway builds from the GitHub repo.
- **backend/** — ⚠ UNVERSIONED local shadow copy (not in git: absent from origin/main, untracked, not gitignored — verified 2026-06-11). It is NEVER deployed and is NOT authoritative; do not edit it expecting production effect, and do not trust it as a mirror (it has drifted, e.g. a stale pre-fix model_v55.py until 2026-06-11). Treat root as the single source of truth; delete or formally track backend/ as part of repo-hygiene cleanup.
- **App.tsx, index.tsx, components/, services/, utils/** — authoritative React/Vite SPA source at repository root
- **work/** — ML research: model training, ablation studies, validation scripts, feature experiments
- **features/** — Feature engineering experiments
- **docs/** — ARCHITECTURE.md, DATABASE.md, RUNBOOK.md, MONITORING.md, TROUBLESHOOTING.md
- **static/** — tracked legal/guide/consent/image sources plus ignored Vite outputs (`index.html`, `assets/`)
- **components/** — React TypeScript components (HeroForm, ReportDashboard, GarageFinderModal)
- **catboost_production_v55/** — Trained model artifacts (model.cbm, platt_calibrator.pkl, encoders)

### Key Backend Modules

| File | Role |
|------|------|
| `main.py` | FastAPI app — all API endpoints, CORS, rate limiting, static file serving |
| `database.py` | PostgreSQL (asyncpg) + SQLite dual-database access layer |
| `model_v55.py` | CatBoost inference with Platt calibration |
| `feature_engineering_v55.py` | Extracts 104 features from DVSA vehicle history |
| `dvsa_client.py` | DVSA MOT History API client (OAuth 2.0 client credentials) |
| `confidence.py` | Wilson score confidence intervals |
| `lead_distributor.py` | Lead assignment to garages + parallel email via asyncio.gather() |
| `email_service.py` | Resend email integration |
| `consolidate_models.py` | Make/model string normalization |
| `regional_defaults.py` | Postcode → corrosion index lookup |
| `repair_costs.py` | Component repair cost estimation |

### API Endpoints

- `POST /api/v2/reports` — RC1 browser report creation and persistence
- `GET /api/v2/reports/{token}` — Opaque shared-report retrieval
- `GET /api/version` — Backend/frontend release identity
- `GET /api/risk?make=&model=&year=&mileage=&postcode=` — Legacy compatibility lookup
- `GET /api/makes` / `GET /api/models?make=` — Vehicle catalogue
- `POST /api/submit-lead` — Lead capture
- `GET /api/find-garages?postcode=&radius_miles=` — Garage finder
- `GET /health` — Health check
- `POST /api/admin/export-risk-checks` — Admin (requires X-API-Key header)

### Dual Database Strategy

- **PostgreSQL (Railway):** Primary for all reads/writes — mot_risk, leads, garages, risk_checks tables
- **SQLite (autosafe.db):** Fallback for comparison-data reads, bootstrapped from `prod_data_clean.csv.gz` via `build_db.py`; it is not report-share persistence

### ML Architecture (work/ directory)

Three-cohort gated ensemble routes vehicles by test history:
- **Veterans** (n_prior_tests >= 1): Full V55 model with behavioral features
- **True Rookies** (n_prior_tests == 0, age <= 3): Rookie model with vehicle specs
- **No Prior Recorded** (n_prior_tests == 0, age > 3): Conservative baseline

Feature categories: test history, advisory trends, mileage behavior, regional corrosion, neglect score, temporal patterns, vehicle attributes.

## Critical Rules

### Temporal Leakage Prevention
Features must only use data available at prediction time. Use `filter_prior_rows()` for as-of filtering. The `min_history_depth` in contracts specifies required prior test cycles.

### Red Line Thresholds (work/ validation)
| Metric | Max |
|--------|-----|
| join_drop_rate | 0.5% |
| missingness_spike | 10pp |
| cohort_shift | 5pp |
| ECE | 0.10 |
| invariant_violation_rate | 0.1% |

### Kill Switches
- `PREDICTIONS_ENABLED=false` disables all V55 predictions
- `MODEL_VERSION` env var enables rollback

### Environment Variables (see .env.example)
Required as applicable: `DATABASE_URL`, `BASE_URL`, `VRM_HMAC_KEY`,
`DVSA_CLIENT_ID`, `DVSA_CLIENT_SECRET`, `DVSA_TOKEN_URL`, `DVSA_SCOPE`,
`DVSA_API_KEY`, `ADMIN_API_KEY`, `RESEND_API_KEY`, `CORS_ORIGINS`, `PORT`.
Release builds also carry `GIT_SHA`; Railway may expose
`RAILWAY_GIT_COMMIT_SHA` at runtime.

## CI/CD

GitHub Actions (`.github/workflows/ci.yml`):
- **frontend-build:** install, typecheck, lint, Vitest, build, untracked-output guard
- **test:** pytest + public-claim sweep (flake8 is advisory)
- **contract-drift:** OpenAPI snapshot verification
- **security-check:** advisory secret-pattern and dependency-audit output
- **build-check:** source-built Docker image validation
- **e2e:** Playwright against the built SPA with artifacts
- **staging-evidence:** exact-SHA real-image/Postgres acceptance
- Triggers on push to main/staging and all PRs

## RC1 release gotchas

- `static/index.html` and `static/assets/` are generated, ignored outputs. Never
  stage them. The Docker frontend stage is authoritative.
- `.railwayignore` is applied before Docker and cannot be proven by a local
  build. Complete release-packet decision D6 on a Railway candidate service.
- `npm ci` and the root lockfile are the reproducible frontend install path.
- OpenAPI and public-claim snapshots are hard gates; update them only when the
  intended contract/evidence boundary changes.
- Merging `main` auto-deploys production. Code approval is not deploy approval.

## Known Gotchas

- **Cycle history bucketing:** the old "84.8% of n_prior_tests=0 have history" defect was FIXED 2026-06-18 (canonical pipeline rebuild; ~1.4% residual recoverable history remains, concentrated in old NO_PRIOR_RECORDED vehicles)
- **Segment lookup:** Analysis code uses string keys, production uses tuples — keep consistent
- **SQLite fallback** can silently change semantics vs PostgreSQL — treat as read-only fallback only
- **CatBoost requires libgomp1** — already handled in Dockerfile but needed for local Linux dev
- **Data locations for ML research:** Dev/OOT test sets are in `{iCloud}/AutoSafe/stratified_samples/`

## Gotchas added 2026-07-03 (remediation session)
- **Consent gate lives in the vite SOURCE template** (root `index.html`) and `templates/seo_base.html`; edits to `static/index.html` are clobbered by `npm run build`.
- **NEVER `git add -A` in this repo root** — untracked junk + the nested `work/` gitlink get committed. Stage explicit paths.
- **`work/` is a separate git repo** → private remote `autosafe-research` (evidence chain; `verify_headline.py` must pass from a fresh clone). Its `legacy-product` remote is fetch-only by design — never push to it.
- **Privacy is Option B** (notices disclose storage; 24-month retention; LIA at `docs/LIA_RISK_CHECKS.md`); PECR consent-mode default-denied — no gtag before Accept, including SEO pages.
- The iCloud `AutoSafe/` tree is FROZEN (see its README_FROZEN); snapshot lives in the research repo.
