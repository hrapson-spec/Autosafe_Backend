# OA-005 v2 acceptance status — 2 October 2026

The current candidate is `oa-journey-30m-v2`; **collection remains OFF**.
The current specification is `COLLECTOR.md` and the privacy assessment is
`../LIA_ACQUISITION_MEASUREMENT.md`. The privacy notice text is part of the enable gate.

Implemented: shared sessionStorage attribution without decorated links; a fixed
30-minute admission/failure window; one raw/aggregate reducer; atomic aggregation
and deletion; version/pilot aggregate keys; GPC and a visible objection switch;
retired automatic Umami loading; fail-closed admission when deletion is overdue.

Local verification so far: 463 frontend tests; 208 focused backend/privacy/report
checks with two skips; TypeScript and lint passed. Real PostgreSQL and real-image staging passed in CI run 36989872050, including HTTP receipt/dedup/GPC, failure precedence, aggregate deletion and disposable rollback. The browser run passed the new measurement-control journey but found two old tests still expecting retired Umami requests; those assertions have been corrected. Fresh CI, live migration, production synthetic receipt and client enablement remain pending. These local counts do not establish production
collection or a running pilot. Additional changes require current reruns.

The original v1 evidence below is retained as a historical record, **superseded
for current behaviour and privacy claims**. It describes the old 90-day/raw and
25-month/aggregate policy and fragment attribution, which must not be used as
the v2 enable receipt.

---

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
| `landing_id` carried to `/app` only as a single parameter on own CTA links; app reads it, removes it with `history.replaceState`, does not store it (**form superseded by D-007.1: URL fragment `#al=&src=`, consumed by index.html's first head script**) | `static/acquisition-landing.js` rewrites same-origin `/app` links and marked CTA links; `index.html` head script consumes the fragment; `utils/acquisitionLanding.ts` validates the window variable and holds it in memory | `acquisitionLandingScript.test.ts` "rewrites only same-origin /app links..."; `acquisitionLanding.test.ts` handoff, invalid al/src ignored, strip keeps other params/hash/state; Playwright `acquisition-landing.spec.ts` |
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
| pytest (`tests/`) | 529 passed, 1 skipped | **699 passed, 2 skipped** (+170 passed; the extra skip is the Postgres-only advisory-lock test) |
| pytest, acquisition files with a local Postgres (`ACQUISITION_TEST_PG_DSN`) | n/a | **187 passed, 1 skipped** (every store test ran on SQLite and PostgreSQL; the skip is the SQLite leg of the Postgres-only test) |
| vitest | 401 passed, 9 failed (410) | **568 passed, 9 failed (577)**: +167 passed, the same 9 `components/ReportDashboard.test.tsx` failures that pre-exist under Node 26 (nothing else fails) |
| Playwright (installed chromium 1243, no temporary config needed) | 29 passed | **35 passed** (6 in `e2e/acquisition-landing.spec.ts`, 3 of them with Ads consent accepted) |
| `npm run typecheck`, `npm run lint`, `npm run build` | exit 0 | exit 0, 0 warnings |
| `scripts/claim_sweep.py` | clean | clean |
| `scripts/check_openapi_drift.py` | clean | clean after regenerating `openapi.json` with `--write`: **43 added lines, 0 removed** (the new `POST /api/acquisition/events`); this snapshot change is intended |
| Built bundle with the flag off | n/a | no `api/acquisition` or `keepalive` string in any `static/assets/*.js`: the transport is tree-shaken out |

Mutation checks: (D-007.1) disabling the `replaceState` in `index.html`'s head script fails 5 of the 6
Playwright specs in `e2e/acquisition-landing.spec.ts` (the fragment-removal, consent-accepted
third-party, report-route and SEO-form specs); removing the slowapi redaction filter fails all 5
`TestNoClientIpInLogs` tests. (Earlier, D-005 era: the query-string handoff's ordering claim rested on
a source-order test because React renders asynchronously; that mechanism is gone.)

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

## D-007 privacy-review hardening (lead rulings of 2026-10-01)

Implemented in full; LIA added at the lead's request:
[`../LIA_ACQUISITION_MEASUREMENT.md`](../LIA_ACQUISITION_MEASUREMENT.md) (structure of
`docs/LIA_RISK_CHECKS.md`; sourced from D-005 to D-007; linked from `COLLECTOR.md`).

| D-007 item | Change | Proving test / evidence |
|---|---|---|
| 1. Handoff in the URL **fragment** | `static/acquisition-landing.js` appends `#al=&src=` (existing query kept; a link that already has a fragment is left alone); `index.html` gets a new **first executing inline head script** that moves `al`/`src` into `window.__autosafeLandingHandoff` and removes them with `replaceState` (path, query, history state and other fragment content kept) before gtag or Umami exist; `utils/acquisitionLanding.ts` reads that variable once and never the URL's fragment or `al`/`src` query (old `?al=` links are no longer interpreted); SEO registration form uses `window.autosafeLandingHandoff`; notice and docs reworded | vitest: head-script-first and ahead of gtag/Umami (source order), script behaviour in jsdom (fragment removal, other-fragment and no-op cases), variable consumption, `index.tsx` reads no URL; Playwright with Ads consent **accepted** (gtag.js and Umami really injected): location fragment empty at the moment every external script is attached, 0 of the non-same-origin requests/bodies/headers/Referer and the gtag `dataLayer` contain the landing id or `google_organic`, bearer report route also consumed, SEO form `location.assign('/app#al=...')` keeps the fragment across the navigation (commit URL recorded) and the app consumes it. A browser-keeps-fragment check was needed because the SEO form is a JS-intercepted submit, not a GET form action; no storage alternative was needed |
| 2. No client IP in any log | `_RedactRateLimitKey` filter on the `slowapi` logger (all routes; `([redacted])` replaces the key, IPv4/IPv6 literals scrubbed from any slowapi message) | `TestNoClientIpInLogs`: 429s with a known `X-Forwarded-For` on the collector (ingest on and off, the limiter runs first) and on a report route, plus IPv6 and idempotent-install; the warning and endpoint remain. The leak was reproduced before the fix: `ratelimit 120 per 1 minute (203.0.113.77) exceeded at endpoint: /api/acquisition/events` |
| 3. Retention runs whatever the ingest flag says | `start_background_retention` returns None only when no store is configured; `run_retention_once` creates tables only when ingest is on and otherwise runs only if all three tables exist | tests: ingest off + tables exist -> rows deleted; background task runs with ingest off and deletes; ingest off + no tables or a partial schema -> nothing created |
| 4. Landing-level aggregates | new table `acquisition_landing_daily (day, source_group, page_family, landings, landings_with_supported_result)` (counts only, 25 months), filled from the same rows and in the same statement/transaction as the existing rollup; a landing already counted is never counted again; `scripts/acquisition_metric.py` reports it as `landing_aggregate_*` | `TestLandingAggregates` on SQLite and PostgreSQL: reproduces the raw metric (render_failed wins, bots/other sources/paid/app excluded) before and after raw deletion, counts only, exactly once across reruns and concurrent runs, duplicate landing id counted once, young landings wait, 25-month deletion |
| 5. `received_at` to the minute | truncated when the record is built | `TestReceivedAtGranularity`; live row seconds all 0 |
| 6. Client flags must agree | test pins equality; checklist item | `test_the_two_client_flags_agree` |
| 7. Gate additions | migration before `ACQUISITION_INGEST_ENABLED` (runtime DDL is also serialised on an advisory lock); confirm and record Railway HTTP-log retention (**open, not recorded**) | `COLLECTOR.md` checklist; LIA section 7 |
| 9. Bot regex | named crawlers plus `\bbot\b`, `[a-z]bot/\d`, `crawler`, `\bspider\b` instead of `bot\b` | Cubot UA variants (3) are not bots; 8 crawler/generic bot UAs are |

**Local receipts after D-007**

1. *Real browser, real server, flags flipped in a temporary build (reverted, never committed; the
   Playwright spec is kept as `docs/acquisition/receipts/OA-005_browser_receipt.spec.ts.txt`)*:
   Playwright chromium against a real two-worker `uvicorn` and a throwaway Postgres 16.2, external hosts
   aborted, createReport mocked. Journeys: A organic guide landing (referrer google) -> click the CTA ->
   check -> displayed supported result; B organic landing, no attempt; C paid landing
   (`gclid=...&utm_medium=cpc`) -> CTA -> check -> result; D organic guide **reloaded**. Observed
   (`OA-005_local_browser_receipt_d007.json`): the CTA href was `/#al=<id>&src=google_organic`; the
   navigation commit URL carried the fragment and the app's next URL was clean; guide and app events
   shared **one** landing id (two sessions, one per document); source `google_organic`; no `Referer`, no
   cookie header, `localStorage`/`sessionStorage`/cookies all empty; the only external requests were
   attempted Umami script loads (aborted); the paid journey was `paid_search` and nothing from the query
   (name or value) was in any payload; the reload produced **no** second `landing_observed`; stored
   `received_at` seconds all 0.
2. *Aggregates reproduce the raw metric on those browser-generated rows*
   (`OA-005_local_browser_aggregate_receipt_d007.json`): raw metric **3 / 1** (eligible organic
   landings / with a supported result); after the rollup `acquisition_landing_daily` gave **3 / 1**
   (google_organic guide 3 / 1, paid_search guide 1 / 1, the paid row not in the organic figure); rerun
   rolled 0 landings; after simulating 100 days (raw rows 0) the aggregate still gave **3 / 1**.
3. *Synthetic scripted run, now including aggregate checks*
   (`OA-005_local_receipt_postgres_run4_d007.json`): 26 unique events + resend all 202, raw rows 26,
   metric **5 / 1**, rolled up 26 events and 7 landings, rerun 0, aggregate **5 / 1** and **5 / 1**
   again with raw rows 0; all six receipt checks true; slowapi/log inspection: no `203.0.113` or UA in
   the server log.

Verification after D-007: pytest **699 passed, 2 skipped** (Postgres-enabled acquisition files **187
passed, 1 skipped**); vitest **568 passed, 9 failed** (the same 9 `ReportDashboard.test.tsx` failures
only); Playwright **35 passed**; typecheck, lint, build, `claim_sweep` exit 0; OpenAPI drift clean (no
regeneration needed); flags confirmed OFF in the committed tree.

D-007.8 (accepted as-is) is recorded in the LIA: present-tense notice slightly ahead of enabling; a reused
copied CTA link counts once per landing; a `restored_link` render carrying an in-memory landing id counts
with `entry_mode` reported; a client may post `internal_test` (excluded from the metric).

## Not executed

- **Production and staging**: no request to either; the staging synthetic receipt and the production
  synthetic event/delete steps of the enable gate are not done (procedure in `COLLECTOR.md`).
- **A real browser with the flags flipped on, against staging or production:** only done locally (D-007
  section: Playwright chromium against a real two-worker uvicorn and a throwaway Postgres, flags
  flipped in a temporary build and reverted). GPC in a real browser was not exercised there (GPC is
  covered in jsdom and by the server `Sec-GPC` test).
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
