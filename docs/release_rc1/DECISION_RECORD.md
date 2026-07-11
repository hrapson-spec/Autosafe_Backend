# RC1 decision record

**Date:** 2026-07-11

**Branch:** `release/product-truth-rc1`

**Pull request:** #32

**Decision owner:** Henri Rapson

**Current state:** release candidate under final verification; no merge or
production deployment authorised by this record.

## Decision

RC1 is eligible for an owner release decision only when every hard gate below
is green on one exact candidate SHA and the owner accepts the Railway-specific
risk in D6. Until then, the decision is **NO-GO**.

## Decisions and invariants

### D1 — One browser report contract

The browser creates a report through `POST /api/v2/reports` and restores a
shared report through `GET /api/v2/reports/{token}`. Legacy endpoints remain
compatibility surfaces; they are not the RC1 browser path
(`services/reportApi.ts:106-146`, `report_routes.py:768-788`).

### D2 — Unknown is not zero and is never invented

Mileage is user-entered, observed from recorded MOT history, estimated only
when the source explicitly supports that classification, or missing. Missing
evidence remains `null`; it is not converted into a reassuring zero
(`report_contract.py:41-62`, `report_contract.py:165-190`,
`report_service.py:135-179`).

### D3 — Evidence controls specificity

The evidence ladder selects the closest available comparable-vehicle cohort.
Component and repair detail is emitted only when supported; degraded evidence
returns an honest unavailable state (`report_service.py:416-505`). Public copy
describes this comparison service and is guarded by `scripts/claim_sweep.py`.

### D4 — Persistence is best-effort, sharing is explicit

One create operation mints the report ID and opaque share token, attempts one
durable save, and returns a typed degraded result when persistence is
unavailable. Retrieval distinguishes not-found, expired, and temporarily
unavailable states (`report_routes.py:568-728`, `database.py:1196-1343`).

### D5 — Privacy boundary

Postcode is submitted in a POST body, never a share URL. Share URLs contain an
opaque token. Long-lived operational records replace plaintext VRN and postcode
with HMAC identifiers under the retention workflow
(`services/reportApi.ts:106-146`, `report_routes.py:180-245`,
`scripts/retention_sweep.py:127-334`).

### D6 — Railway source-stripping risk is owner accepted, not inferred

The Docker build and CI validate `.dockerignore`, but Railway applies
`.railwayignore` before Docker receives the context. The candidate must be
deployed to a non-production Railway environment and its `/api/version`
identity, static bundle, report flow, persistence, and share retrieval must be
verified. See [RAILWAY_STRIP_RISK_MEMO.md](RAILWAY_STRIP_RISK_MEMO.md).

- [ ] Owner accepts the residual Railway risk after the candidate deploy
      evidence is attached.

### D7 — Database rollback is expand-compatible

The migration adds nullable columns and indexes and drops the old registration
`NOT NULL` constraint. Application rollback means redeploying the previous
image; do not try to restore the old `NOT NULL` constraint during an incident
(`migrations/add_report_contract_columns.py:1-84`).

### D8 — No production action by implication

Approval of code, this packet, or the pull request is not approval to merge or
deploy. Because `main` auto-deploys, the owner must explicitly authorise the
merge after the final report says GO.

## Hard gates

- Backend tests, typecheck, lint, frontend unit tests, frontend production
  build, OpenAPI drift check, public-claim sweep, and real-Postgres staging
  acceptance all pass on the exact candidate SHA.
- GitHub Actions is green on that same SHA; no stale check result is counted.
- The complete diff has been reviewed for correctness, privacy, release
  integrity, and unrelated changes.
- The Railway candidate deploy satisfies D6.
- The owner approves the residual risks and explicitly authorises the merge.

## Owner decision

- [ ] **GO:** all gates are green, D6 is accepted, and merge is authorised.
- [ ] **NO-GO:** one or more gates or approvals remains open.

Owner: ____________________  Date/time: ____________________
