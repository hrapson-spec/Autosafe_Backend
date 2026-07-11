# RC1 decision record

**Date:** 2026-07-11

**Branch:** `release/product-truth-rc1`

**Pull request:** #32

**Decision owner:** Henri Rapson

**Current state:** release candidate under final verification; no merge or
production deployment is authorised by this record.

## Decision

RC1 is eligible for an owner release decision only when every hard gate below
is green on one exact candidate SHA and the owner accepts the Railway-specific
risk in D7. Until then, the production decision is **NO-GO**.

## Decisions and invariants

### D1 — One browser report contract

The browser creates a report through `POST /api/v2/reports` and restores a
shared report through `GET /api/v2/reports/{token}`. Legacy endpoints remain
compatibility surfaces; they are not the RC1 browser path.

### D2 — Unknown is not zero and is never invented

Mileage is user-entered, observed from recorded MOT history, explicitly
estimated, or missing. Missing evidence remains `null`. Zero is preserved only
when zero is the actual recorded value.

### D3 — Evidence controls specificity

The service reports one historical failure rate for the closest supported
comparable-vehicle cohort. It does not report an exact-vehicle prediction,
diagnosis, pass probability, reliability score, or AutoSafe Index. Evidence
scope, counts, source, and mileage provenance travel with the result. Component
and repair sections are omitted when unsupported. Component evidence is shown
only when every contributing aggregate row has a value for that component; a
partially populated cohort is treated as unsupported, not averaged selectively.

### D4 — PostgreSQL and SQLite mean the same thing

All three evidence rungs use sample-size-weighted aggregates over the same
model/variant match in both PostgreSQL and SQLite. A matched database fallback
changes `prediction_source`; it must not silently change the cohort arithmetic.
If no matched cohort is displayed, `prediction_source=dataset_reference`
identifies the checked-in aggregate number while `match_scope` records whether
the store was reached but empty or unavailable.

The dataset-wide reference is 39,969,903 failures across 148,509,908 tests
(26.9139638817903%) in 254,145 aggregate rows. The checked-in artifact revision
is 2026-01-29, but the artifact contains no source-record coverage date. RC1
therefore makes no claim that the underlying records are current through that
date.

### D5 — Persistence is best-effort; sharing is explicit

One create operation mints a report ID and opaque token, attempts one durable
save, and returns a typed degraded result with no share identity when storage
cannot be confirmed. The browser reuses one idempotency key across unresolved
retries of the same logical submission. Reuse of that key for another VRN is a
typed `409 idempotency_conflict`.

### D6 — Privacy boundary

Postcode is submitted in a POST body and never returned or placed in a share
URL. Saved-report URLs contain `/app/report/{token}`. The token is an opaque
plaintext bearer key in PostgreSQL, not a token hash; this risk is explicit.
After 24 months the check-record workflow HMACs the VRN and removes plaintext
VRN, postcode, payload, and token. After 12 months the lead workflow deletes
lead rows and dependent assignments. Application log correlation uses a keyed
VRN digest, not enumerable plain SHA-256.

### D7 — Railway source-stripping risk is owner accepted, not inferred

Docker and CI validate `.dockerignore`, but Railway applies `.railwayignore`
before Docker receives the context. The exact candidate must be deployed to a
non-production Railway service and its `/api/version`, static bundle, report
flow, persistence, sharing, logging, and analytics boundaries must be verified.
See [RAILWAY_STRIP_RISK_MEMO.md](RAILWAY_STRIP_RISK_MEMO.md).

- [ ] Owner accepts the residual Railway risk after candidate evidence is
      attached.

### D8 — Database rollback is expand-compatible

The migration adds nullable columns/indexes and drops old `NOT NULL`
constraints from `risk_checks.registration` and `leads.postcode`. Application
rollback means redeploying the previous image. Do not restore either constraint
during an incident; existing valid rows may contain nulls by design.

### D9 — Release identity is cryptographic and exact

`/api/version` must expose the exact backend SHA, frontend build SHA, build
timestamp, contract version, and a full 64-character SHA-256 of the JavaScript
entry referenced by built `static/index.html`. Acceptance rejects unknown or
mismatched identity.

### D10 — No production action by implication

Approval of code, the packet, or the pull request is not approval to merge or
deploy. Because `main` auto-deploys, the owner must explicitly authorise the
merge after the final report says GO.

### D11 — No unimplemented outcome-linkage claim

RC1 has no pipeline linking a generated report to a later MOT result. Product,
evaluation, and privacy documents may describe such linkage only as separately
approved future work, not as an implemented capability or release benefit.

### D12 — Retire unreviewed outbound publishing

The unsupported insights generator, press-email publisher, and automated
Reddit reply agent are not release surfaces. They generated stale dataset
figures or individual-outcome language, depended on retired templates/routes,
and could place third-party content or media-contact details in local logs.
RC1 removes those tools and their dedicated dependencies/configuration. The
claim sweep includes their former source paths so any deliberate replacement
must satisfy the same product-truth gate before it can ship.

## Hard gates

- Backend tests, frontend typecheck/lint/unit tests, Playwright E2E, production
  build, dependency audits, OpenAPI drift, public-claim sweep, migration dry
  runs, and real PostgreSQL acceptance pass on the exact candidate SHA.
- Check-record and lead retention execute successfully against disposable
  staging records, including post-run verification.
- GitHub Actions, including the clean Docker staging build, is green on that
  same SHA. No stale check counts.
- Repeated clean frontend builds produce the same entry-bundle hash.
- The complete diff is reviewed for correctness, privacy, release integrity,
  and unrelated changes.
- The Railway candidate satisfies D7 and synthetic log/analytics inspection
  finds no raw identifiers or bearer tokens.
- Monitoring, retention schedules, ownership, and rollback authority exist in
  the deployed environment.
- The owner approves residual risks and explicitly authorises the merge.

## Owner decision

- [ ] **GO:** all hard gates are green, D7 is accepted, and merge is
      explicitly authorised.
- [ ] **NO-GO:** one or more gates or approvals remains open.

Owner: ____________________  Date/time: ____________________
