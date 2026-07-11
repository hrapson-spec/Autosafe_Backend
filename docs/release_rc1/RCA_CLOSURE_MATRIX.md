# RC1 root-cause and closure matrix

The source-state references show the original failure at
`origin/main@657930d5f11c8d6f9d9d4c39fce70e2377aa244f`. The closure references
point to the RC1 implementation. Final closure is conditional on the exact-SHA
gates in `FINAL_RELEASE_REPORT.md`.

| Failure | Root cause | RC1 correction | Primary regression evidence | Closure |
|---|---|---|---|---|
| Invented 50,000-mile value | The browser adapter supplied `50000` when mileage was absent and the dashboard also hard-coded it (`origin/main@657930d5:services/autosafeApi.ts:130-170`, `:328-350`). | Typed provenance and priority resolution preserve user, observed, estimated, or missing states without a fabricated default (`report_contract.py:41-62`, `report_service.py:135-179`). | `tests/test_report_service_mileage.py:42-292`; `tests/test_report_contract.py:113-172`; `components/ReportCopy.test.tsx` | Code closed; exact-SHA gate required |
| Fabricated evidence defaults | The old adapter synthesized confidence, component, and comparison values when source evidence was absent (`origin/main@657930d5:services/autosafeApi.ts:130-170`). | Evidence is resolved through an explicit cohort ladder. Missing values remain null and component/repair detail is omitted when unsupported (`report_service.py:416-505`, `report_contract.py:165-190`). | `tests/test_report_service_banding.py`; `tests/test_report_contract.py:212-232`; `components/ReportCopy.test.tsx` | Code closed; exact-SHA gate required |
| Reports did not persist | The browser used legacy lookup endpoints; no single browser operation owned creation plus durable report storage. | `POST /api/v2/reports` owns identity, calculation, one persistence attempt, and a typed fail-open response. The database stores and retrieves by opaque token (`report_routes.py:568-676`, `database.py:1196-1343`). | `tests/test_report_routes.py:197-382`; `tests/test_database_v2.py`; real-Postgres staging acceptance | Code closed; operational DB migration required |
| Share links opened the homepage | Sharing used the current homepage URL rather than a stored report token (`origin/main@657930d5:components/ReportDashboard.tsx:120-131`). | The API returns `/report/{token}`; the SPA route retrieves the persisted report and exposes the same opaque URL to copy/WhatsApp sharing (`report_routes.py:180-245`, `App.tsx:181-223`, `components/ReportCopy.tsx:217-274`). | `App.test.tsx`; `services/reportApi.test.ts`; `e2e/report-share.spec.ts`; staging acceptance | Code closed; deployed BASE_URL must be verified |
| Form reset/remount | `HomePage` was defined inside `App`, so parent renders changed the component identity and remounted its state (`origin/main@657930d5:App.tsx:18-63`). | `HomePage` has module scope and `HeroForm` keeps an explicit edit lock; report navigation and reset are deliberate state transitions (`App.tsx:28-36`, `components/HeroForm.tsx:35-53`). | `App.test.tsx`; `components/HeroForm.test.tsx`; `e2e/form-lifecycle.spec.ts` on `/` and `/app` | Code closed; browser E2E gate required |
| Ambiguous failure handling | Browser/backend failures collapsed into generic or misleading states. | The v2 contract defines invalid input, unavailable dependency, not found, expired, and persistence-unavailable outcomes (`report_contract.py:95-113`, `report_routes.py:679-728`). | `tests/test_report_contract.py`; `tests/test_report_routes.py:367-421`; frontend validation/error tests | Code closed; exact-SHA gate required |
| Privacy lifecycle incomplete | Plain operational identifiers had no complete release-linked reconciliation path. | POST-body transport, opaque shares, a 24-month sweep, HMAC identifiers, nullable plaintext fields, and a backlog migration are explicit (`scripts/retention_sweep.py:127-334`, `migrations/pseudonymize_backlog.py:245-265`). | `tests/test_retention_sweep.py`; privacy review in `PRIVACY_RECONCILIATION.md` | Code closed; secret and scheduled job remain deploy tasks |
| Build/deploy identity could drift | Static output could be stale and staging acceptance did not bind the backend to the intended SHA. | Docker builds the SPA from source; static build output is untracked; `/api/version` exposes identities; acceptance now rejects `unknown` or mismatched backend SHAs (`Dockerfile:1-78`, `scripts/staging_acceptance.py:133-158`). | `tests/test_staging_acceptance.py:27-68`; CI frontend/build/staging jobs | Code closed; Railway D6 remains open |
| Public claims exceeded the evidence path | Marketing and SEO copy still described AI prediction, exact-vehicle insight, diagnosis, or unsupported source data. | Public copy now describes recorded MOT history and closest available comparable-vehicle rates; the sweep rejects those claim classes (`scripts/claim_sweep.py`). | `python scripts/claim_sweep.py`; SEO tests; frontend tests | Code closed; rendered candidate smoke check required |

## Why the failures shared a cause

The five visible symptoms were not independent UI bugs. The browser lacked one
authoritative report contract linking inputs, provenance, calculation,
persistence, presentation, and share retrieval. RC1 closes that seam with a
single v2 operation and makes every degradation explicit.

## Closure rule

“Code closed” means an implementation and targeted regression test exist. It
does not mean production-ready. A row becomes release-closed only when the full
gate suite and GitHub Actions pass on the candidate SHA, the Railway D6 check is
performed, and the owner records GO.
