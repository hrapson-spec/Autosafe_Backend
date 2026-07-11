# AutoSafe RC1 release packet

This directory is the decision packet for PR #32 on
`release/product-truth-rc1`. It records what was fixed, what was verified,
what remains operationally unverified, and the exact deploy/rollback boundary.

## Decision order

1. Read [DECISION_RECORD.md](DECISION_RECORD.md) for the release decision and
   owner-only approvals.
2. Read [RCA_CLOSURE_MATRIX.md](RCA_CLOSURE_MATRIX.md) for the five original
   failures, their causes, fixes, and regression evidence.
3. Read [RAILWAY_STRIP_RISK_MEMO.md](RAILWAY_STRIP_RISK_MEMO.md) before any
   Railway deployment. This is the remaining platform-specific risk.
4. Follow [RUNBOOK_DEPLOY_ROLLBACK.md](RUNBOOK_DEPLOY_ROLLBACK.md) exactly.
5. Use [PRIVACY_RECONCILIATION.md](PRIVACY_RECONCILIATION.md) for the privacy
   and retention review.
6. Treat [FINAL_RELEASE_REPORT.md](FINAL_RELEASE_REPORT.md) as authoritative
   for the final candidate SHA, test counts, live CI state, and verdict. It is
   generated only after the final review and gates complete.

## Scope boundary

The packet authorises neither a merge nor a production deployment. `main`
auto-deploys to Railway, so both actions require an explicit owner decision.

## Evidence hierarchy

- Source and regression tests in this repository are the primary evidence.
- GitHub Actions logs and artifacts for the final candidate SHA are the durable
  integration evidence.
- The local real-Postgres acceptance run is supporting evidence only. Its raw
  files are deliberately not committed; the final report records the result
  and the exact reproducible command.
- A successful local Docker build cannot close the Railway source-stripping
  risk. Only a real Railway candidate deploy can do that.

## Core implementation map

- Contract and error taxonomy: `report_contract.py:35-272`
- Mileage and evidence provenance: `report_service.py:135-505`
- Atomic create/share retrieval: `report_routes.py:568-788`
- Durable report storage: `database.py:1196-1343`
- Browser API boundary: `services/reportApi.ts:73-146`
- Truthful report presentation: `components/ReportCopy.tsx:84-274`
- Retention and pseudonymisation: `scripts/retention_sweep.py:127-334`
- Release identity acceptance: `scripts/staging_acceptance.py:133-158`
