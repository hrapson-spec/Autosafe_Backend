# AutoSafe RC1 release packet

This directory is the decision packet for PR #32 on
`release/product-truth-rc1`. It separates source/test evidence from operational
evidence and authorises neither merge nor production deployment.

## Reading order

1. [DECISION_RECORD.md](DECISION_RECORD.md) — invariants, hard gates, and
   owner-only approvals.
2. [RCA_CLOSURE_MATRIX.md](RCA_CLOSURE_MATRIX.md) — original failures, shared
   causes, corrections, and regression evidence.
3. [PRIVACY_RECONCILIATION.md](PRIVACY_RECONCILIATION.md) — actual data flow,
   logging/analytics boundary, retention, and residual risks.
4. [RAILWAY_STRIP_RISK_MEMO.md](RAILWAY_STRIP_RISK_MEMO.md) — platform-specific
   candidate-deploy gate that local Docker cannot close.
5. [RUNBOOK_DEPLOY_ROLLBACK.md](RUNBOOK_DEPLOY_ROLLBACK.md) — exact-SHA staging,
   deployment, canary, monitoring, and rollback procedure.
6. `FINAL_RELEASE_REPORT.md` — final SHA, evidence counts, CI/staging state,
   residual risks, and GO/NO-GO. It is created only after candidate gates.

Supporting controls live in `docs/STAGING.md`, `docs/MONITORING.md`, and
`docs/LIA_RISK_CHECKS.md`.

## Evidence hierarchy

1. Exact source, schema, migration, and tests in this repository.
2. Fresh local outputs on a clean committed candidate.
3. GitHub Actions and artifacts for that exact SHA, including a clean Docker
   build and real-PostgreSQL acceptance.
4. Isolated Railway candidate evidence for source packaging, public routing,
   proxy logs, browser requests, analytics, persistence, and sharing.
5. Explicit owner acceptance and merge authority.

Old-SHA test results, a dirty local build, a healthy endpoint with unknown or
mismatched identity, or code that merely contains a retention script cannot
close a hard gate.

## Dataset baseline

The checked-in primary comparison artifact, `prod_data_clean.csv.gz`, contains
254,145 aggregate rows representing 148,509,908 tests and 39,969,903 failures,
an exact dataset-wide failure rate of 26.9139638817903%. Its repository artifact
revision is 2026-01-29. The artifact does not encode the source records' coverage
date; the revision date must not be presented as a claim that DVSA source data
is current through that date. `scripts/claim_sweep.py` independently recalculates
the totals and rejects stale public figures.

## Core implementation map

- Contract, provenance, and error taxonomy: `report_contract.py`
- Mileage and evidence ladder: `report_service.py`, `database.py`
- Atomic create/idempotency/share retrieval: `report_routes.py`
- Durable report and lead storage: `database.py`, `create_leads_table.py`
- Browser API and retry identity: `services/reportApi.ts`, `App.tsx`
- Truthful presentation/recommendations: `components/ReportCopy.tsx`,
  `components/ReportDashboard.tsx`, `utils/recommendation.ts`
- Evidence-scoped public routes: `seo_pages.py`, `templates/seo_model.html`,
  `templates/seo_model_age.html`
- Privacy-safe paths/referrers/log correlation: `utils.py`, `dvsa_client.py`
- Consent and analytics boundary: `index.html`, `static/consent.js`,
  `utils/analytics.ts`
- Check/lead retention: `scripts/retention_sweep.py`,
  `scripts/lead_retention_sweep.py`
- Release identity and acceptance: `report_routes.py`,
  `scripts/staging_acceptance.py`
- Contract/public-claim gates: `openapi.json`,
  `scripts/check_openapi_drift.py`, `scripts/claim_sweep.py`
- Reproducible and audited browser dependency graph: `package.json`,
  `package-lock.json`
- Retired unsupported outbound publishers: `agents/`, `data_stories/`, and
  their dedicated configuration/dependencies are absent; the claim sweep
  retains glob coverage for any future replacement.

RC1 implements no later-outcome linkage pipeline. Any future attempt to connect
reports to subsequent MOT outcomes is a separate data-source, privacy, and
evaluation decision outside this release.

## Scope boundary

`main` auto-deploys to Railway. Approval of this packet or PR review is not
approval to merge. Only an explicit owner GO after all exact-SHA gates can
authorise that action.
