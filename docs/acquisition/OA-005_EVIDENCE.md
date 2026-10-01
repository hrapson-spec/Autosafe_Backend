# OA-005 evidence: first-party acquisition collector (collection OFF)

Branch `oa/005-first-party-collector`, stacked on `oa/004-render-acknowledgement` at
`f19c85d` (D-004 `demo` outcome group and the `unavailable`-over-`demo` precedence merged in).
Spec: `DECISIONS.md` D-005 (authoritative) plus MEASUREMENT.md "Minimal first-party interface"
and "Attribution, replay and edge cases". Run date 2026-10-01, local Node 26 (CI uses Node 20),
Python 3.11 venv, local throwaway PostgreSQL 16.2.

## What this is, and is not

The collector, storage, retention job, client transport, landing attribution, privacy notice
and tests. **Nothing is enabled.** Three independent switches are off (client constant, public
script constant, server `ACQUISITION_INGEST_ENABLED`); see `COLLECTOR.md`. No production
request, push, PR, merge, third-party service, cookie, web storage or lockfile change was made.

> **The privacy notice text is part of the enable gate.** It goes live on merge while
> collection is still off (present tense is deliberate: the notice must precede processing).
> It must be re-read against `COLLECTOR.md` immediately before the flags are flipped. The
> test `tests/test_acquisition_wiring.py` pins its D-005 facts in both `static/privacy.html`
> and `components/PrivacyPage.tsx`.

## D-005 clause by clause

| D-005 clause | Where it is met | Proving test |
|---|---|---|
| Legitimate interests, product-improvement purpose only | notice section "First-party measurement of visits and check outcomes" in both privacy pages; purpose/data/retention table rows | `test_privacy_notice_covers_d005_in_static_and_react_pages` |
| PECR: nothing stored on or read from the device; no cookies/localStorage/sessionStorage/IndexedDB; no fingerprinting | transport and landing code hold ids in memory only; no storage token in either landing asset or the transport | `test_landing_assets_use_no_storage_cookie_or_beacon`; `acquisitionEvents.test.ts` "exactly one transport (fetch) and no storage"; `acquisitionTransport.test.ts` "never touches web storage, cookies or IndexedDB"; landing-script "touches no storage and sets no cookie" |
| Random per-load ids, never derived from inputs: `session_id`, `operation_id`, `landing_id` | `randomId()` (CSPRNG; refuses without secure random); script `randomId()` returns null and measures nothing without it | `acquisitionTransport.test.ts` session id; landing tests ("mints a landing id"); script "measures nothing when no secure random source exists" |
| `landing_id` carried to `/app` only as `?al=&src=` on own CTA links; app reads it, removes it with `history.replaceState`, does not store it | `static/acquisition-landing.js` rewrites same-origin `/app` links and marked CTA links; `utils/acquisitionLanding.ts` validates, holds in memory, strips in `index.tsx` before `root.render` | `acquisitionLandingScript.test.ts` "rewrites only same-origin /app links..."; `acquisitionLanding.test.ts` handoff, invalid al/src ignored, strip keeps other params/hash/state; Playwright `acquisition-landing.spec.ts` |
| Never sent to third parties (Umami path-only filter, `strict-origin-when-cross-origin`) | strip runs before any analytics page view; filter and header unchanged | ordering tests below; `test_collector_is_same_origin_only_by_csp`; `test_report_route_headers_do_not_gain_collector_headers` |
| Session = one document lifetime plus the CTA handoff; reload/new tab/typed URL unattributed; no 30-min window, no reload dedup | no storage anywhere | documented as coverage limit 2 in `COLLECTOR.md`; notice text |
| Source group from `document.referrer` **origin** only; google_organic / other_search / referral / direct / unknown (+ `internal`); referrer never sent | `classifyReferrer` (SPA) and `classifyReferrer` (script), kept in step | `acquisitionLanding.test.ts` 20-case table incl. `google.com.evil.example`, `mail.google.com`; script parity test over 14 referrers; "never ... derived from the referrer path or query" |
| Fields: only OA-004 enums/booleans/random ids plus `release_sha`; server adds only `received_at` and derived `is_bot` | strict pydantic models, `extra='forbid'`, `strict=True`; `AcquisitionRecord` columns | 24 forbidden fields x 4 event types -> 400; 25 bad-landing and 19 bad-result mutations -> 400; `test_record_type_has_no_field_that_could_carry_a_request_secret` |
| No IP/User-Agent/Referer/query/URL stored; UA used only for `is_bot` | route reads UA once for `is_bot`; IP only in slowapi memory | `test_no_ip_user_agent_referer_or_url_is_stored` (also scans the raw SQLite file bytes); live Postgres row inspected (below) |
| Landing path stored only as allowlisted page family | `page_family` enum replaces `landing_path` (schema v1 amended) | schema parity tests; script page-family table (13 route shapes) |
| Objection: `navigator.globalPrivacyControl === true` -> client sends nothing; policy says so + email | transport, SPA landing and public script all check it; server also honours `Sec-GPC: 1` | `acquisitionTransport.test.ts` GPC (2); landing-script GPC; SPA landing GPC; `test_global_privacy_control_header_records_nothing` |
| Storage: new `acquisition_events` table in existing Railway Postgres; no new processor | `acquisition_store.PostgresStore` over the existing asyncpg pool; migration `migrations/add_acquisition_tables.py` | store tests run on Postgres (see counts); live two-worker run |
| Retention: raw 90 days then deleted by scheduled idempotent job; aggregates (no IDs) 25 months | `run_retention` (rollup >1 day exactly once, delete >90 d, delete aggregates >25 months), startup + daily background task | `TestRetention` (10 tests, 9 of them store-parametrised over SQLite and PostgreSQL, frozen clock); `TestBackgroundRetention` (3) |
| Access: Henri only via Railway; no public dashboard | no read route exists; metric script is operator-run | n/a (no route to test); `routes` has only the POST |
| Logging: no body, `session_id`, `landing_id`, `operation_id` for the route; production already `--no-access-log` | route logs fixed reason codes and exception type names only | `test_logs_never_contain_body_ids_ip_or_user_agent`; `test_rejection_reasons_are_fixed_codes`; live log grep (below) |
| Bearer routes: collector may run on `/app/report/*`, sends no token/report id/URL, `referrerPolicy: 'no-referrer'`; third-party analytics stay off | report routes are not landings; events carry only enums/booleans/random ids; transport sets `no-referrer` | `acquisitionTransport.test.ts` request shape; journey test asserts no token/registration/postcode/make in any body; script "never runs on a report route" |
| Notice before enabling | both privacy pages | notice tests; gate item in `COLLECTOR.md` |
| Enable gate | checklist in `COLLECTOR.md`; OFF-state tests must be edited deliberately | wiring + OFF tests |

MEASUREMENT.md items also met: 2 KB body limit (413), 202 for accepted and duplicate, 400
malformed, 429 abuse, `render_failed` wins in the metric query, one retry only on network/5xx
within 10 s with the same `event_id` and never on 4xx/429, no queue.

## Verification

Baseline = `oa/004-render-acknowledgement` tip `f19c85d` (same Node 26 environment).

| Check | Baseline | This branch |
|---|---|---|
| pytest (`tests/`) | 529 passed, 1 skipped | **671 passed, 2 skipped** (+142 passed; the extra skip is the Postgres-only advisory-lock test) |
| pytest, acquisition files with a local Postgres (`ACQUISITION_TEST_PG_DSN`) | n/a | **153 passed, 1 skipped** (every store test ran on SQLite and PostgreSQL; the skip is the SQLite leg of the Postgres-only test) |
| vitest | 401 passed, 9 failed (410) | **563 passed, 9 failed (572)**: +162 passed, the same 9 `components/ReportDashboard.test.tsx` failures that pre-exist under Node 26 (nothing else fails) |
| Playwright (installed chromium 1243, no temporary config needed) | 29 passed | **32 passed** (3 new in `e2e/acquisition-landing.spec.ts`) |
| `npm run typecheck`, `npm run lint`, `npm run build` | exit 0 | exit 0, 0 warnings |
| `scripts/claim_sweep.py` | clean | clean |
| `scripts/check_openapi_drift.py` | clean | clean after regenerating `openapi.json` with `--write`: **43 added lines, 0 removed** (the new `POST /api/acquisition/events`); this snapshot change is intended |
| Built bundle with the flag off | n/a | no `api/acquisition` or `keepalive` string in any `static/assets/*.js`: the transport is tree-shaken out |

Mutation check on the ordering claim: moving `initAcquisitionLanding()` after `root.render(...)` in
`index.tsx` fails `utils/acquisitionLanding.test.ts` ("the source order in index.tsx is ...").
The two behavioural ordering tests do **not** fail on that mutation by themselves (React renders
asynchronously, so a later synchronous call still precedes the first effect); the source-order
test is the discriminating one. The ordering claim therefore rests on: strip is synchronous at
module evaluation, before `root.render`; `trackPageView` fires only from an `App` effect; Umami is
`data-auto-track=false` and its payload is reduced to path and referrer origin by
`autosafeUmamiBeforeSend`.

## Local part of the OA-005 receipt (synthetic, throwaway database, never production)

Real `uvicorn main:app --workers 2 --no-access-log` against a throwaway local PostgreSQL 16.2
(`DATABASE_URL`, `ACQUISITION_INGEST_ENABLED=1`, database created empty so the collector created
its own tables), driven by `scripts/acquisition_synthetic_check.py --organic`. Receipt JSON:
`docs/acquisition/receipts/OA-005_local_receipt_postgres_run2.json`.

Seven scripted journeys (22 unique events) plus a same-`event_id` resend, all 202:

| Journey | Eligible organic landing | Counts in numerator |
|---|---|---|
| J1 guide landing -> started -> created -> supported prediction rendered | yes | **yes** |
| J2 home landing -> dataset reference only (unsupported) | yes | no |
| J3 model landing, no attempt | yes | no |
| J4 `other_search` guide landing, completes | no (not Google organic) | n/a |
| J5 make landing -> check_failed | yes | no |
| J6 comparison landing -> rendered then `render_failed` same operation | yes | no (render_failed wins) |
| J7 crawler User-Agent organic landing | no (bot) | n/a |

Independent expectation (from the table above): **denominator 5, numerator 1**.

| Measure | Result |
|---|---|
| Events sent / all HTTP 202 / duplicate resend 202 | 22 / yes / yes |
| Raw rows in `acquisition_events` | 22 (duplicate added no row) |
| Primary metric query | **denominator 5, numerator 1** (rate 0.2), matching expectation |
| Rollup first pass | 22 rolled up; `acquisition_daily` total 22 (landing 7, check_started 5, report_created 4, result_rendered 4, check_failed 1, render_failed 1) |
| Rollup rerun | 0 rolled up, totals unchanged (exactly once) |
| Metric after rollup | 5 / 1 (raw rows are kept until 90 days) |
| Worker startup | both workers scheduled the job; one logged `skipped_locked=True` (advisory lock), the other `rolled_up=0` (empty table) |
| Server log grep | 0 of the 36 distinct event/session/landing ids, no User-Agent, no client IP |
| Stored row | typed enum/boolean/uuid columns only; no IP/User-Agent/Referer/URL column exists |
| Live responses | 400 invalid_event (body not echoed), 413 at 3000 bytes, 202 with `Sec-GPC: 1`; all `Cache-Control: no-store`, security headers and CSP unchanged |

**A defect in my own tooling was found and kept on the record.** The first run
(`OA-005_local_receipt_postgres_run1_superseded.json`) reported `metric_matches_expectation:
false` because the script expected a denominator of 4; I had miscounted the eligible landings
(the five are J1, J2, J3, J5, J6). The collector's 5/1 was correct; the expectation constant
was fixed and the tables truncated for run 2. Nothing else differed between the runs.

Two further defects were found and fixed by repetition testing, not by the first green run:
(1) `slowapi`'s fixed window let the 429 test pass or fail depending on whether a minute boundary
fell inside the loop (the test now sends until the first 429 and bounds the count);
(2) the schema-ensure cache was keyed by `id(store)`, so a recycled object address in the test
suite skipped table creation (5 failures in one 30-run loop plus two earlier one-offs, all `OperationalError`/503); the flag now lives
on the store object and a test pins that. 40 further runs of both acquisition test files with
Postgres enabled were clean.

## Judgement calls and deviations (items 1-5 accepted in D-006; item 6 superseded)

1. **Server flag `ACQUISITION_INGEST_ENABLED`** (default off, 404 when off). Not in D-005; it makes
   the D-005 gate steps possible (production synthetic event with the client still off), is the
   fastest rollback lever, and keeps DDL and the retention job from running at all while off.
2. **503, not 202, on a recording failure** (justified in `COLLECTOR.md`): the client ignores the
   response; 503 lets its single retry succeed after a brief outage and does not report a lost
   write as accepted. 2 s timeout and a 10 s breaker keep it local to this route.
3. **`internal` source group** (same-site referrer) added to the schema-v1 enum next to
   `internal_test`, as the brief specified, so internal navigation is not counted as `direct`.
   `landing_observed` now carries `page_family` instead of the OA-004 `landing_path` (D-005:
   the path is never stored); `docs/acquisition/event_schema_v1.json` and `EVENT_SCHEMA_v1.md`
   were amended accordingly. That event was defined but never emitted, so nothing was in use.
4. **CTA coverage.** Most public CTAs link to `/`, not `/app`. Besides links to `/app`, the script
   also rewrites links that point at `/` **and carry `data-acq-cta`** (the CTA boxes in
   `templates/seo_base.html`, `seo_index.html`, `seo_make.html` and the guide pages), and the SEO
   registration form (which navigates to `/app` itself) appends the same query. Without this, almost
   no organic landing could be attributed. If you read D-005 as `/app` links only, remove the
   `data-acq-cta` attributes and the form edit; the SPA handles both paths identically. These
   edits touch `templates/seo_base.html`, which the oa/006 worker is also editing: expect a small
   merge conflict there.
5. **`release_sha`:** the SPA sends it from a new vite `define` (`__RELEASE_SHA__`, from
   `RAILWAY_GIT_COMMIT_SHA`/`GIT_SHA`, which the Docker frontend stage already receives as build
   args; non-hex becomes absent). The repo had no frontend SHA constant. The public-page script
   has no build identity and omits it. Not verified inside a real Docker build (see not executed).
6. **No reload dedup** was my original reading of D-005; **superseded by D-006** (reload is not a landing, below).
7. **Public set excludes `app`.** "Eligible landing" = a public page family; a Google visit landing
   directly on `/app` is stored (`page_family=app`) and shown separately by the metric script but
   is not in the primary denominator. `/` (home) is included.

## D-006 amendments (lead rulings of 2026-10-01)

Accepted as built: `ACQUISITION_INGEST_ENABLED`, 503 on recording failure, `page_family`, the
`internal` source group, `data-acq-cta` and the SEO-form handoff. Two amendments implemented:

**1. Reload is not a landing.** `landing_observed` is emitted only when
`performance.getEntriesByType('navigation')[0].type === 'navigate'`
(`isFreshNavigation()` in `utils/acquisitionLanding.ts`, same logic in
`static/acquisition-landing.js`). `reload`, `back_forward` and `prerender` emit nothing, mint no
`landing_id`, and (public script) rewrite no links and set no handoff query. **Fallback,
documented in `COLLECTOR.md`:** if the API is unavailable, throws, or returns no entry, the
landing is emitted. Tests: SPA (`acquisitionLanding.test.ts`: navigate / reload / back_forward /
prerender / no entry / throwing API / jsdom default) and public script
(`acquisitionLandingScript.test.ts`: the same four types plus missing entry and `performance`
undefined). Effect on the KPI: a reload of an organic landing page no longer adds a second
denominator row; the residual double count exists only in browsers without the API.

**2. Paid search is not organic.** A landing URL carrying `gclid`, `gbraid` or `wbraid`
(any key case) or `utm_medium` equal to `cpc`/`ppc`/`paid` (any case, trimmed) has
`source_group = paid_search`, ahead of referrer classification (`hasPaidSearchMarker`,
`classifySource`; script `hasPaidMarker`). Only presence/equality is tested. `paid_search` was
added to the client type, the SPA handoff allowlist, the server enum, `event_schema_v1.json`,
`EVENT_SCHEMA_v1.md` and `COLLECTOR.md`. The privacy notice now lists "paid search" among the
arrival categories and says the marker is tested in the browser and never sent or kept; it also
no longer says a reload starts a new measurement. The metric query already filters
`source_group = 'google_organic'`, so paid landings and their completions are in neither
numerator nor denominator; the query docstring, `scripts/acquisition_metric.py` (new output
field `paid_search_landings_excluded`) and `COLLECTOR.md` state this. Tests: marker table (15
cases incl. look-alikes `gclid_not`, `utm_medium=cpcx`, `utm_source=cpc`), precedence over a
Google referrer, and for SPA and script a **leak test**: for `gclid`, `gbraid`, `wbraid` and
`utm_medium` landings the serialised event, context, handoff query and rewritten links contain
none of the parameter names, values (including an unrelated `utm_campaign` value) or the
referrer; server tests accept `paid_search` and reject `gclid`/`gbraid`/`wbraid`/`utm_*` as
fields; the schema-parity grid includes `paid_search`; the primary-metric store test includes a
completing paid landing that must not count. Existing `al`/`src` stripping is unchanged (the
tracking parameters themselves stay in the address bar as before).

**Local receipt re-run including a paid_search journey**
(`docs/acquisition/receipts/OA-005_local_receipt_postgres_run3_d006.json`; same real two-worker
uvicorn and throwaway Postgres 16.2, tables dropped first so the collector recreated them):
J8 is a `paid_search` guide landing that completes with a supported prediction. 26 unique events
(22 + J8's 4) plus the resend, all 202; raw rows 26; **primary metric denominator 5, numerator 1
(unchanged: J8 excluded)**; `acquisition_daily` total 26, rerun adds 0; the metric script reports
`paid_search_landings_excluded: 1`; landing rows by source: google_organic 6 (5 eligible + the bot),
other_search 1, paid_search 1; 0 of 42 ids in the server log. Runs 1 and 2 are retained as before.

Verification after the amendments: pytest **671 passed, 2 skipped** (Postgres-enabled acquisition
files 153 passed, 1 skipped); vitest **563 passed, 9 failed** (the same 9
`ReportDashboard.test.tsx` failures only); Playwright 32 passed; typecheck, lint, build and
`claim_sweep` exit 0; OpenAPI drift clean with no regeneration needed (the route body is an opaque
object in the snapshot, so the enum change does not alter it).

## Not executed

- **Production and staging**: no request to either; the staging synthetic receipt and the production
  synthetic event/delete steps of the enable gate are not done (procedure in `COLLECTOR.md`).
- **A real browser with the flags flipped on** (network capture, cookie/storage inspection, GPC in
  a real browser): the flags were not flipped in any build. The transport, handoff and ordering are
  covered by jsdom/vitest and the Playwright OFF-state spec only.
- **A real Docker build** (`__RELEASE_SHA__` from build args, Railway migration/restart behaviour):
  Docker was not running; `release_sha` is therefore proven only by unit tests.
- **Postgres outage behaviour live**: the 503/timeout/breaker path is tested with fake failing and
  slow stores, not by stopping the database under a running server.
- **Railway Postgres specifics** (pool limits, managed-DB permissions for `CREATE TABLE`): the
  collector creates its tables with the app's own role; if that role cannot, run
  `migrations/add_acquisition_tables.py` as owner before enabling ingest.
- flake8 and ruff are not installed in the venv; not run on the new files.

## Escalations

None blocking. Items for the lead: judgement calls 1 and 4 above (accepted in D-006); the paid-search limitation
(resolved for tagged clicks by D-006; untagged paid traffic still looks organic); and the pre-existing, separate
client storage that D-005's "no web storage" does not cover but a privacy review may want to see:
`sessionStorage` (`autosafe_pending_registration`, the SEO registration-form handoff) and
`localStorage` (`autosafe_consent`, the Ads consent choice). Neither was changed.
