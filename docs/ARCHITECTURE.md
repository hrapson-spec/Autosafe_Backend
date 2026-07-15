# AutoSafe Architecture

## Authoritative RC1 path

```text
Browser (React/Vite)
  -> POST /api/v2/reports
     -> DVSA vehicle and latest MOT record lookup
     -> mileage provenance resolution
     -> weighted evidence ladder
        PostgreSQL mot_risk -> SQLite risks -> labelled dataset reference
     -> typed ReportResponse
     -> PostgreSQL risk_checks persistence
  -> GET /api/v2/reports/{opaque token} for saved-report restoration
```

The v2 result is a historical comparison rate with provenance. The product does
not call the V55 model in this path and does not present the rate as a vehicle
diagnosis or future-outcome forecast.

`prediction_source` names the source of the displayed number. Matched cohort
figures use `postgres` or `sqlite`; both degraded scopes use
`dataset_reference` because their number is the checked-in aggregate constant.
`match_scope` separately distinguishes a missing model cohort
(`population_default`) from unavailable evidence stores (`unavailable`).

## Frontend

The SPA source lives at repository root (`App.tsx`, `components/`, `services/`,
`utils/`, `index.tsx`). Vite builds `static/index.html` and hashed files under
`static/assets/`. Those outputs are ignored and are built inside the Docker
frontend stage; they are not release inputs from a developer's working tree.

Standalone legal/guide HTML and SEO templates remain source-controlled under
`static/` and `templates/`. Optional analytics are consent-gated and automatic
tracking is disabled on bearer-token report routes.

## Backend boundaries

| Module | Responsibility |
|---|---|
| `main.py` | FastAPI lifecycle, legacy compatibility routes, lead/reminder endpoints, static serving |
| `report_contract.py` | Strict v2 request/response/error models and enums |
| `report_routes.py` | Typed errors, idempotency, persistence ordering, token retrieval, `/api/version` |
| `report_service.py` | Vehicle/MOT mapping, mileage provenance, evidence fallback ladder |
| `database.py` | PostgreSQL pool, v2 weighted queries, saved-report persistence, lead persistence |
| `dvsa_client.py` / `dvla_client.py` | External vehicle-data clients |

Legacy `/api/risk`, `/api/risk/v55`, and `/api/vehicle` endpoints are isolated
compatibility surfaces. They are not the browser's RC1 report flow.

## Evidence stores

- `mot_risk` in PostgreSQL is the primary aggregate comparison store.
- `prod_data_clean.csv.gz` is the checked-in primary artifact used to build the
  SQLite `risks` fallback via `build_db.py`.
- Both stores use sample-size-weighted exact-band, age-band, and model-level
  aggregation. Component evidence is suppressed unless every contributing row
  has complete component values.
- `risk_checks` stores saved report payloads, idempotency keys, provenance,
  expiry, and opaque share tokens.
- `leads` stores explicitly requested reminder, email, or garage-contact data.

## Persistence and privacy

A report ID and token are minted before insertion so the successful POST body
and stored GET replay are identical. If persistence fails, the live response is
degraded to `saved=false` and contains no durable ID, token, URL, or expiry.

Application access logging is disabled in the release image. Runtime logs use
correlation IDs, safe route shapes, and keyed VRN digests where correlation is
necessary. Check records are pseudonymised after 24 calendar months; leads are
deleted after 12 calendar months. The one-time historical backlog operation is
separate and requires an owner-supplied notice-live cutoff.

## Release identity

`GET /api/version` reports backend SHA, frontend build SHA, full JavaScript
bundle SHA-256, build timestamp, contract version, application version, and
process start time. Acceptance requires both source SHAs to equal the candidate
commit. Railway-specific build-context filtering still requires a real
candidate deployment before production approval.

See `docs/release_rc1/README.md` for the release packet and current gates.
