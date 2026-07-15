# AutoSafe

AutoSafe is a UK vehicle-record and MOT-comparison service. The current RC1
browser flow creates a typed report from recorded DVSA vehicle/MOT details and
the closest supported aggregate comparison group.

The displayed percentage is a historical group failure rate. It is not an
inspection, diagnosis, pass probability, reliability score, or prediction of a
particular vehicle's next MOT result. Every report carries its mileage source,
match scope, sample size, persistence state, and data-source status.

## Current product flow

1. The React/Vite SPA sends `POST /api/v2/reports` with a registration,
   postcode, optional mileage, and an idempotency key. The API contract permits
   postcode to be absent for non-homepage callers; the current homepage form
   requires it.
2. `report_routes.py` resolves recorded vehicle/MOT data and
   `report_service.py` selects the closest supported evidence rung:
   `exact_band`, `age_band_only`, `model_average`, or an explicitly labelled
   dataset-wide reference.
3. PostgreSQL stores a saved report and opaque bearer token. A successful save
   can be restored through `GET /api/v2/reports/{token}` for 90 days; a failed
   save returns the report without a share credential.
4. PostgreSQL is the primary comparison store. The SQLite database built from
   `prod_data_clean.csv.gz` is the read fallback and uses the same weighted
   arithmetic.

The checked-in comparison artifact contains 254,145 aggregate rows representing
148,509,908 tests and 39,969,903 failures (26.9139638817903%). Its repository
artifact revision is 2026-01-29. The artifact does not encode a source-record
coverage date, so RC1 makes no claim about the latest underlying test date.

Legacy prediction endpoints remain available for compatibility, but the SPA
does not call them.

## Local setup

Prerequisites: Node 20, Python 3.11, and the dependencies required by
`requirements.txt`.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
npm ci
npm run build
.venv/bin/uvicorn main:app --reload
```

The app is then available at `http://localhost:8000`. Vite writes ignored build
outputs to `static/index.html` and `static/assets/`; never commit those files.

## Verification

```bash
.venv/bin/python -m pytest tests/ -q
npm run typecheck
npm run lint
npm test
npx playwright test
.venv/bin/python scripts/check_openapi_drift.py
.venv/bin/python scripts/claim_sweep.py
.venv/bin/python migrations/add_report_contract_columns.py --dry-run
GIT_SHA=$(git rev-parse HEAD) docker compose -f docker-compose.staging.yml config
```

The claim sweep independently recalculates the primary dataset totals before it
checks public product copy.

## Key locations

- `report_contract.py` — typed v2 wire contract and dataset metadata.
- `report_routes.py` — create/replay/share API and release identity.
- `report_service.py` — mileage provenance and evidence ladder.
- `database.py` — PostgreSQL persistence and weighted comparison queries.
- `App.tsx`, `components/`, `services/`, `utils/` — SPA source.
- `scripts/retention_sweep.py` — 24-month check-record pseudonymisation.
- `scripts/lead_retention_sweep.py` — 12-month lead deletion.
- `docs/release_rc1/README.md` — release packet, gates, and current verdict.
- `docs/release_rc1/RUNBOOK_DEPLOY_ROLLBACK.md` — controlled deployment and rollback.

Production deployment is a separate owner-authorised operation. Building or
testing this repository does not authorise a Railway production deploy.
