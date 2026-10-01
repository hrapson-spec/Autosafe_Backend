# OA-004 evidence: displayed-result measurement interface (collection OFF)

Branch `oa/004-render-acknowledgement`, based on `7325720` (production). Ticket
OA-004; semantics from `product/commercial/organic-acquisition/MEASUREMENT.md`.
Date of run: 2026-10-01, local Node 26 (CI uses Node 20).

## What this is, and is not

Interface and client plumbing that lets the app say "a supported result was
actually displayed" separately from "the API returned" or "the user clicked".
**No data leaves the browser.** There is no transport in the new code, the default
sink is a no-op, `ACQUISITION_COLLECTOR_ENABLED = false`, and the collector
endpoint, storage, session persistence, retention and legal basis all await the
OA-005 decision. Acknowledgements are client-reported; they prove neither accuracy
nor model qualification, and nothing here is ingestion proof.

## Changes

New: `utils/resultAcknowledgement.ts` (`classifyResult`), `utils/acquisitionEvents.ts`
(typed events, sink, ids, in-memory completion markers), `components/ResultErrorBoundary.tsx`,
`docs/acquisition/EVENT_SCHEMA_v1.md`, `docs/acquisition/event_schema_v1.json`, this file,
and test files listed below.

Edited (structural, minimal): `components/ReportDashboard.tsx` (optional `onRendered`
post-commit effect), `components/ReportUnavailable.tsx` (optional `onShown` mount
callback, fixed reason enum only), `components/ReportScreen.tsx` (typed lazy-load
failure, boundary wrap without re-indenting `<Suspense>`, acknowledgement handlers;
no `<Helmet>` content touched), `App.tsx` (operation id, three emissions, operation id
in navigation state; `trackConversion('risk_check')` / `trackFunnel('reg_entered')`
unchanged), `App.test.tsx` (probe shows the operation id; 8 new tests).

Not changed: `utils/analytics.ts` (`analyticsAllowed`), Umami/gtag behaviour, public
copy, lockfile, backend.

## State table and proving tests

| State (MEASUREMENT.md) | render_delivered / supported_result / group | Test |
|---|---|---|
| Valid vehicle_prediction / model_prediction | true / true / prediction; `sample_nonzero` key absent | `resultAcknowledgement.test.ts` "row 1"; `ReportScreen.acquisition.test.tsx` "vehicle prediction omits sample_nonzero" |
| Valid comparison / exact_band | true / true / exact_comparison | "row 2"; "exact comparison from a fresh check" |
| Valid comparison / age_band_only, model_average | true / true / broader_supported_comparison | "row 3" (x2); "age_band_only / model_average is a broader supported comparison" |
| population_default | true / false / dataset_reference | "row 4"; "population_default renders as dataset_reference and is not supported" |
| unavailable scope, any data source (fully degraded) | true / false / unavailable; emitted as `result_unavailable` (reason `unavailable`) | "row 5"; "fully degraded (unavailable scope, demo / real vehicle data) mounts as result_unavailable/unavailable, not result_rendered" |
| demo report (any other contract-valid scope, D-004) | true / false / demo; emitted as `result_rendered` | `resultAcknowledgement.test.ts` "demo data"; `ReportScreen.acquisition.test.tsx` "D-004: a demo report (...)" |
| demo report with the unavailable scope (D-004 precedence) | true / false / unavailable; emitted as `result_unavailable` | `resultAcknowledgement.test.ts` "demo x unavailable => unavailable"; `ReportScreen.acquisition.test.tsx` "fully degraded (unavailable scope, demo ...)" |
| ReportUnavailable mounted after failed retrieval | `result_unavailable`, reason not_found / expired / error | "failed retrieval: result_unavailable with a fixed reason" (5 codes + non-ReportApiError); "an unsaved route without an inline payload is unavailable" |
| Loading (spinner / API 200 alone) | nothing emitted | "loading never emits"; `ReportScreen.lazyDelay.test.tsx` |
| Rejected lazy chunk | false / false / error; unavailable error view shown; `render_failed` `lazy_load`; no result events | `ReportScreen.lazyReject.test.tsx`; `ResultErrorBoundary.test.tsx` "chunk load failure" |
| Render throw | same view; `render_failed` `render` | `ReportScreen.acquisition.test.tsx` "a render throw shows..." ; `ResultErrorBoundary.test.tsx` |
| Invalid contract / numeric (mounted) | false / false / error; `render_failed` `contract_invalid` | "row 6"; NaN / Infinity / >1 / negative / null / undefined / string risk; total_tests 0 / null / non-integer / negative / NaN / string; failures > tests / negative / non-integer / without tests; kind x scope matrix; unknown enums; mounted-view cases (4) |
| Remount / StrictMode | at most one emission per operation | "StrictMode ... once" (x3), "a remount of the same operation does not emit again" |
| Repeated deliberate check | new operation id, own emission | `App.test.tsx` "a later deliberate check of the same vehicle, in the same App instance, is a new operation"; `ReportScreen.acquisition.test.tsx` "repeated deliberate check ..." |
| Retry of same unresolved operation | same operation id, no second `check_started` | `App.test.tsx` "a retry of the same unresolved operation keeps the operation id..." |
| Restored / shared link | `entry_mode=restored_link`, no `operation_id`, no `check_started`/`report_created` | "restored / shared links" (3) |
| Inline unsaved success | `persistence_mode=inline_unsaved`, counts if supported | "inline unsaved success counts when supported"; `App.test.tsx` "inline persistence-degraded success" |
| check_failed | fixed category + stage, no message | `App.test.tsx` "check_failed carries a fixed category and stage..."; "a non-ReportApiError failure is category unknown" |
| Third-party pin | `trackConversion('risk_check')` / `trackFunnel('reg_entered')`: same args, once, order, after createReport, before navigation | `App.test.tsx` "pins the existing third-party calls..." (full call log asserted) |
| Report-route analytics boundary | no fetch, sendBeacon, gtag, umami; events carry no token/VRM | "privacy and third-party boundary on the report route" |
| Safe-field privacy | every event type serialised; no forbidden key; no token/VRM/make/model/URL value; only number is `schema_version` | `acquisitionEvents.test.ts` "safe fields only" (3) |
| No transport | source of the three new modules has no fetch / sendBeacon / XHR / WebSocket / EventSource / Image / navigator | `acquisitionEvents.test.ts` "contains no network transport" (x3); built bundle has no `__setAcquisitionSinkForTests` or `api/acquisition` string (grep, below) |
| Schema shape | emitted events validate; forbidden fields, bad enums, combination rules rejected; TS and JSON enums agree | `acquisitionEvents.test.ts` "event schema ..." (6) using a small hand checker (`acquisitionSchemaCheck.testutil.ts`; no new dependency) |
| Disclosure text mounted | the disclosure text that `scope_visible` v1 assumes exists is mounted (collapsed) for every scope; the flag itself is data-derived (D-003) | "scope_visible tracks what the real dashboard renders" (6 scopes) |

Mutation spot checks (temporary, reverted, files restored byte-identical): disabling the
dedup marker failed 4 tests; counting `dataset_reference` as a supported group initially
survived (the fixtures carry null counts, which masked it), so a test with a valid
sample on reference/unavailable scopes was added and then killed it; firing `onRendered`
during render failed at least 10 existing and new tests.

## Verification commands (worktree, `GATE_NO_HOOK=1 GATE_SELF_PRIO=3`)

| Command | Exit | Result | Baseline (`work/organic_acquisition/baseline`) |
|---|---|---|---|
| `npm run typecheck` | 0 | clean | 0 |
| `npm run lint` | 0 | clean | 0 |
| `npm test` | 1 | 22 files (21 pass, 1 fail); 410 tests: 401 passed, 9 failed | 15 files; 281 tests: 272 passed, 9 failed (exit 1) |
| `npm run build` | 0 | built; `git status` shows `static/` outputs ignored | 0 |
| `.venv/bin/python -m pytest tests/ -q` | 0 | 529 passed, 1 skipped | 529 passed, 1 skipped |
| `.venv/bin/python scripts/claim_sweep.py` | 0 | "clean" | 0 |
| `.venv/bin/python scripts/check_openapi_drift.py` | 0 | no drift | 0 |
| `npm run test:e2e -- e2e/token-screens.spec.ts e2e/report-and-reset.spec.ts e2e/form-lifecycle.spec.ts` (first pass) | 0 | 15 passed (chromium-1243, own preview build on 4173, port was free beforehand) | not baselined |
| `playwright test` (FULL suite, after review fixes; temporary config on port 4199, `reuseExistingServer: false`, own build) | 0 | 29 passed | not baselined |

The 9 `npm test` failures are the same 9 as baseline (identical failing test names,
`components/ReportDashboard.test.tsx`, `localStorage.clear` not a function under Node 26)
and are unrelated to this change. New tests: +129 (all pass). Bundle grep after build:
`__setAcquisitionSinkForTests` 0 files, `__resetAcquisitionStateForTests` 0, `api/acquisition`
0, `sendBeacon` 0; the event names and `oa-metric-v1-draft` are present (the typed emitters
are bundled; they reach a no-op sink).

## Decisions and ambiguities (for review)

1. **scope_visible v1 (decided: DECISIONS.md D-003).** The visibility requirement is met by
   the scope class shown without interaction on the result card (prediction / "<make model>
   comparison, not a prediction for <REG>" / "Dataset-wide reference comparison"). The
   exact / age-band / model-average detail (collapsed `<details>`, via `buildScopeDisclosure`)
   is not part of the visibility test; it is reported through `match_scope` and
   `outcome_group`. `scope_visible` remains a data-derived, non-discriminating invariant
   (true for every contract-valid report; it does not observe the DOM). A DOM-observed
   check would be a future metric version. `metric_version` stays `oa-metric-v1-draft`.
2. **Numeric or contract invalidity means not delivered.** A mounted view with an invalid
   rate or sample is reported as `render_failed` `contract_invalid`, never as
   `result_rendered` with `rate_valid=false`. Consequence: on an emitted `result_rendered`,
   `rate_valid` is always true and `sample_nonzero` is informative only for reference scopes.
   The alternative (emit `result_rendered` with false flags) is a one-function change.
3. **Fully degraded report (scope `unavailable`), any data source** is emitted as
   `result_unavailable` (reason `unavailable`), per the MEASUREMENT.md events table. Lead
   ruling (DECISIONS.md D-004 "Precedence"): the unavailable scope takes precedence over
   demo, so the repository's degraded fixture (`fixtureUnavailableDegraded`, a demo report)
   classifies as `unavailable`, as it did before D-004. Demo applies only to otherwise
   renderable scopes.
4. **`check_started` once per logical operation.** A retry of the same unresolved operation
   keeps the operation id and does not re-announce (no attempt index: not approved).
5. **Demo data (decided: DECISIONS.md D-004).** A report with `vehicle_data_source = demo`
   gets `render_delivered=true`, `supported_result=false`, `outcome_group='demo'` (new enum
   member) for every otherwise-renderable contract-valid scope (not the `unavailable` scope,
   which takes precedence), including a demo prediction (the contract does not
   forbid one; covered by tests). Contract-invalid demo reports remain `error`.
6. **Reload.** Browsers keep `history.state` across a reload or tab restore, so a restored
   fresh-check report route still carries its operation id: with the in-memory marker gone it
   may emit a second `result_rendered` for that operation, and `entry_mode` can be wrong
   (it may claim `fresh_check`). Documented; the collector must count once and treat
   `entry_mode` as unreliable after reload. Not exercised in a real browser.
7. A rejected lazy chunk stays rejected for that `lazy()` instance until a reload; the
   fallback view offers the existing "Check a vehicle" button.
8. `landing_observed` is typed and in the schema but **not emitted** anywhere (it needs
   the OA-005 session/landing design). `session_id`, `release_sha`, `received_at` are
   collector-side and omitted from the client event types.

## Review findings (coordinator follow-up, 2026-10-01)

1. **MEDIUM, fixed: result_rendered for a torn-down result.** Reproduced by mutation: with
   the acknowledgement called directly from the dashboard effect, a descendant whose passive
   effect throws gives events `result_rendered, render_failed` with the result gone from the
   DOM. Fix: `ReportDashboard` schedules `onRendered` with `setTimeout(0)` and clears it in
   the effect cleanup (the boundary's re-render unmounts the view before the timer fires), and
   `ReportScreen` also latches a render failure (`renderFailedRef`) and skips the
   acknowledgement. Tests: `ReportScreen.effectThrow.test.tsx` (real dashboard, throwing
   descendant effect, with and without StrictMode: events are exactly `[render_failed]`; it
   fails under the immediate-call mutation); `ReportScreen.acquisition.test.tsx`
   "onRendered fires once, after the commit, and is cancelled if the view unmounts first"
   (fake timers, includes StrictMode, exactly once). Residual: a render error AFTER the timer
   has fired can still yield both `result_rendered` and `render_failed` for one operation;
   aggregation must let `render_failed` win.
2. **LOW, fixed: restored-link dedup per mount.** The per-mount id and the failure latch are
   reset when the route token changes (nothing stored or derived from the token). Test:
   "restored link navigated within the same mount (A -> B)" gives two `result_rendered`
   (fails with the reset removed).
3. **LOW, documentation made honest (logic unchanged).** `scope_visible` v1 = "scope
   disclosure text available for this state": data-derived, does not observe the DOM, true for
   every contract-valid report, i.e. currently non-discriminating; the visible card states
   prediction vs `<make model> comparison` vs dataset-wide reference, and the
   exact/age/model distinction is inside a collapsed `<details>`. Product decision pending
   before collection. Updated in `EVENT_SCHEMA_v1.md`, the `event_schema_v1.json`
   description (and the property description), and the code comment. The earlier wording in
   this file that the rule "tracks the DOM" is withdrawn; the DOM test only shows the
   disclosure text is mounted.
4. **Documented only.** Reload / tab restore keeps `history.state`, so `entry_mode` can be
   wrong (claims `fresh_check`), not only the count. (The demo-data half of this item is
   superseded by D-004, below.)

## Metric decisions implemented (DECISIONS.md D-003, D-004)

- **D-004:** `OutcomeGroup` gains `demo`; `classifyResult` returns delivered / not supported /
  `demo` for a contract-valid demo report on any scope except `unavailable` (which takes
  precedence, per the lead ruling); `result_rendered` carries it (schema enum; the `unavailable`
  match scope is removed from `result_rendered` as unreachable; demo never supported,
  sample_nonzero rules preserved); mapping table in `EVENT_SCHEMA_v1.md` updated. Tests: `resultAcknowledgement.test.ts`
  "demo data" (demo x exact / age / model-average / population_default / prediction, plus demo x unavailable => unavailable,
  never supported, invalid stays error, real-data twins keep their groups),
  `ReportScreen.acquisition.test.tsx` (each non-unavailable demo scope emits `result_rendered` demo, never
  `result_unavailable`; demo + unavailable emits `result_unavailable`), `acquisitionEvents.test.ts` (schema rules for demo events).
- **D-003:** documentation only (code comment, `EVENT_SCHEMA_v1.md`, schema description and
  property description, this file). `metric_version` unchanged.

## Not executed

- Node 20 / CI run. (The full Playwright suite was run after the review fixes: 29 passed.)
- A Playwright lifecycle test reading the sink: skipped, because a browser-side test hook
  would require exposing the sink outside the module (contrary to "not exposed on window
  in production builds"); the same lifecycle is covered in vitest with an injected sink.
- Any transmission, collector, staging, ingestion/aggregation receipt, consent
  accept/reject/withdraw, header/proxy logging checks: all OA-005.
- Real-browser reload deduplication; rebase onto the parallel `ReportScreen.tsx` Helmet
  branch (kept edits structural to ease it; not attempted).

## Limits

Collector OFF; no ingestion proof; client-reported only; not model qualification; not an
approval of identifiers, storage, retention or legal basis; Node 26 local run vs Node 20 CI.
