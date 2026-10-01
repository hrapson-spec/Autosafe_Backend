# First-party acquisition collector (OA-005)

Status: **implemented, collection OFF.** Governing decision: `DECISIONS.md` D-005 in the
organic-acquisition work area (UK GDPR Art. 6(1)(f); cookieless; memory-only identifiers).
Event semantics: [`EVENT_SCHEMA_v1.md`](EVENT_SCHEMA_v1.md) and
[`event_schema_v1.json`](event_schema_v1.json). Evidence: [`OA-005_EVIDENCE.md`](OA-005_EVIDENCE.md).

Purpose: measure, in aggregate, whether visitors arriving from search reach a displayed,
supported result. Product and service improvement only: no advertising, profiling,
personalisation or per-person decision.

## Three independent switches (all OFF)

| Switch | Where | Effect when off | How it flips |
|---|---|---|---|
| `ACQUISITION_COLLECTOR_ENABLED` | `utils/acquisitionEvents.ts` | SPA installs no transport; the bundler drops the transport code from the build (no `api/acquisition` string in the bundle); events go to a no-op sink | code change + deploy |
| `var ENABLED` | `static/acquisition-landing.js` | public pages do nothing: no request, no link rewriting | code change + deploy |
| `ACQUISITION_INGEST_ENABLED` | server environment | `POST /api/acquisition/events` returns 404, no table is created, the retention job is not scheduled | env change (no code deploy) |

Rollback is any of these: set the two code constants back to `false` (stops all new
collection), and/or unset `ACQUISITION_INGEST_ENABLED` (fastest: the server stops accepting
and the clients' requests simply 404, which they ignore). Neither deletes stored rows;
`python migrations/add_acquisition_tables.py --rollback` does (drops both tables).

## Architecture

```
public page (templates/*.html, static/guides/*.html)
  static/acquisition-landing.js   source_group from document.referrer ORIGIN, landing_id,
        |                         landing_observed, appends ?al=&src= to own links to /app
        v
SPA (index.tsx -> utils/acquisitionLanding.ts)   reads al/src, strips them immediately, holds
        |                                        them in memory; direct landing on / or /app
        v                                        mints its own landing_id
utils/acquisitionEvents.ts   fetch sink (keepalive, credentials omit, no-referrer, no-store);
        |                    one retry on network error/5xx within 10 s; no queue, no storage
        v
POST /api/acquisition/events  (acquisition_routes.py)  strict validation, rate limit, is_bot,
        |                                              received_at, 202 / 400 / 413 / 429 / 503
        v
acquisition_events (raw, 90 d) --daily rollup, exactly once--> acquisition_daily (25 months)
  acquisition_store.py  Postgres (production, existing Railway DB) / SQLite (local + tests only)
```

### Wire format

The body is a JSON object of at most **2048 bytes**, `Content-Type: application/json`, with
`additionalProperties: false` at every level. Envelope fields on every event:

| Field | Rule |
|---|---|
| `schema_version` | integer `1` |
| `metric_version` | `^[a-z0-9][a-z0-9.-]{0,31}$` (currently `oa-metric-v1-draft`) |
| `event_id` | random UUID v4 text, lower case; **the idempotency key** |
| `session_id` | random UUID, one per document, memory only |
| `landing_id` | random UUID, optional; required on `landing_observed` |
| `page_family` | `home app guide make model comparison pillar problem_hub other_public` |
| `source_group` | `google_organic other_search direct referral unknown internal paid_search internal_test` |
| `release_sha` | optional, `^[0-9a-f]{7,40}$` (SPA only; see below) |

then the event-specific fields exactly as in `event_schema_v1.json` (`landing_observed`
carries `observation_state`; `result_rendered` accepts `outcome_group` `demo` per D-004 and
never `match_scope=unavailable`). Cross-field combination rules are enforced server-side and
checked against the JSON schema over a full grid in `tests/test_acquisition_collector.py`.

The server adds only `received_at` and `is_bot`. `is_bot` comes from a conservative
User-Agent pattern (clear crawlers, headless browsers, monitors, HTTP libraries; an absent
User-Agent counts as a bot). **The User-Agent, IP address, Referer, query string and URL are
never stored.** The IP address is used by the in-memory rate limiter only. `source_group`
`internal` is a same-site referrer; `paid_search` is a landing whose own URL carried a paid-click
marker (below); `internal_test` is synthetic traffic. Page family is a
category, never a path.

| Response | When |
|---|---|
| 202 `{"status":"accepted"}` | accepted, **or a duplicate `event_id`** (indistinguishable), or `Sec-GPC: 1` (nothing recorded) |
| 400 `{"detail":"invalid_event"}` (or `invalid_json`, `invalid_content_type`) | any deviation: unknown/forbidden field, bad enum, bad UUID, wrong type, illegal combination |
| 404 | `ACQUISITION_INGEST_ENABLED` is off |
| 413 | body over 2048 bytes (checked on `Content-Length` and while streaming) |
| 429 | more than 120 requests per minute per client IP (slowapi, memory only, per worker) |
| 503 | the event could not be recorded (store down, slow beyond 2 s, or no store configured) |

Every response is `Cache-Control: no-store`. The body is parsed by hand, so a rejected body
is never echoed and FastAPI's 422 (which echoes input) never occurs.

**Why 503 and not 202 on a recording failure.** The client ignores the response, so the user
flow cannot be affected either way. 503 lets the transport's single same-`event_id` retry
succeed if the store recovers within seconds, and does not report a lost write as accepted.
A 2 s timeout and a 10 s circuit breaker keep the failure fast and local to this route.

### Logging

Application logs never contain a request body, `event_id`, `session_id`, `landing_id` or
`operation_id` for this route. The route logs only fixed reason codes
(`acquisition_event_rejected reason=...`) and exception type names
(`acquisition_record_failed exception_type=...`). Production already runs with
`--no-access-log`. Tests capture logs at DEBUG and assert none of the IDs, IP or User-Agent
appear.

### Identifiers and session rule

All identifiers are random per page load and held in memory only; none is derived from the
registration, postcode, report token or any input. A session is one document lifetime plus
the landing handoff on the CTA link. A reload, new tab or typed URL starts an **unattributed
session**: there is no 30-minute window and no stored dedup. A reload is additionally not a
landing (D-006, below), so it does not inflate the denominator. This is an accepted coverage
limitation and must be reported with the KPI.

Handoff: a public page appends `?al=<landing_id>&src=<source_group>` to the site's **own
same-origin links to `/app`** and to links explicitly marked `data-acq-cta` that point at `/`
(the main CTA boxes; see "Judgement calls" in the evidence file), and exposes the same query
to the SEO registration form that navigates to `/app` itself. The SPA validates both against
the exact shapes we mint (lower-case UUID; `src` in the handoff subset, never `internal_test`),
holds them in memory and removes them with `history.replaceState` in `index.tsx`, before
`root.render`, i.e. before any effect and so before the first Umami page view
(`utils/analytics.ts` `trackPageView`, fired from an `App` effect). Umami's loader filter also
reduces URLs to the path and referrers to the origin, and the global `Referrer-Policy` is
`strict-origin-when-cross-origin`, so the parameters are never sent to a third party.

### Reload is not a landing, and paid search is not organic (D-006)

* **Reload.** `landing_observed` is emitted only when
  `performance.getEntriesByType('navigation')[0].type === 'navigate'`, in both
  `static/acquisition-landing.js` and the SPA (`utils/acquisitionLanding.ts`). A `reload`,
  `back_forward` or `prerender` load emits nothing, mints no `landing_id` and rewrites no links.
  **Fallback:** if Navigation Timing is unavailable or returns no entry the landing **is**
  emitted (fail open; there is no storage to dedupe with, and dropping every landing from a
  browser without the API would be worse than a rare double count). Reading the navigation
  type is not device storage.
* **Paid search.** If the landing URL query has `gclid`, `gbraid` or `wbraid` (any case), or
  `utm_medium` equal to `cpc`, `ppc` or `paid` (any case), `source_group` is `paid_search`,
  taking precedence over the referrer classification. The code only tests presence/equality;
  neither the parameter nor its value is ever sent, stored, logged or copied into a link (tests
  assert no `gclid` value appears in any emitted payload or rewritten link). Existing
  stripping of `al`/`src`, `reg`, `postcode` etc. is unchanged. `paid_search` can be handed to
  the app in `src=`. Other paid traffic without these markers (for example untagged campaigns)
  is still indistinguishable from organic.

### Report routes

`/app/report/*` is never a landing, and the public script never runs there. The first-party
events from report routes carry no token, report ID or URL (`referrerPolicy: 'no-referrer'`,
body fields are enums, booleans and random IDs). Third-party analytics stay off those routes,
unchanged.

### Release identity

The SPA sends `release_sha` when the build defines `__RELEASE_SHA__` (vite `define` from
`RAILWAY_GIT_COMMIT_SHA` or `GIT_SHA`, which the Docker frontend stage already receives as
build args; anything not a 7-40 character hex string is dropped). The public-page script has
no build identity and omits it.

### Storage and retention

Tables (`migrations/add_acquisition_tables.py`, also created by the collector on first use
when ingest is enabled; identical DDL from `acquisition_store.py`):

* `acquisition_events`: one row per event, `UNIQUE (event_id)`, typed columns only
  (no IP, User-Agent, Referer, query, URL, free text), plus `received_at`, `rolled_up_at`.
* `acquisition_daily (day, page_family, source_group, event, outcome_group, entry_mode,
  persistence_mode, is_bot, count)`: no IDs. `day` is the **UTC** calendar day of `received_at`
  (record this next to Umami's and Search Console's own reporting timezones).

The retention job (`acquisition_routes.run_retention_once`) is one transaction:

1. roll raw events older than 1 day into `acquisition_daily`, marking each rolled row with
   `rolled_up_at` **in the same statement** that counts it, so a rerun, a crash and retry, or a
   concurrent second worker cannot count an event twice;
2. delete raw events older than 90 days (only rows already rolled up);
3. delete aggregate rows older than 25 calendar months.

It runs in a background task 20 s after startup (it never delays the lifespan or `/health`)
and then every 24 h, retrying hourly after a failure. Under `uvicorn --workers 2` both workers
schedule it; a transaction-level Postgres advisory lock (`pg_try_advisory_xact_lock`) makes the
second skip, and the `rolled_up_at` marker keeps the result exactly-once even without the lock
(SQLite uses `BEGIN IMMEDIATE`). Observed in the local two-worker run: one worker logged
`skipped_locked=True`.

An event replayed more than 90 days after first receipt would be accepted as new (its raw row
and idempotency record are gone). That is accepted: the client never replays.

## Enable procedure and gate checklist (D-005 "Enable gate")

`ACQUISITION_COLLECTOR_ENABLED` turns on only after **all** of:

- [ ] the collector, **privacy notice**, retention job and tests are merged (the notice text in
      `static/privacy.html` and `components/PrivacyPage.tsx` is part of the enable gate: review
      it against what is actually collected, and against this document, immediately before
      flipping the flag; it goes live on merge while collection is still off);
- [ ] **synthetic staging events received and aggregated as expected** (the OA-005 receipt):
      set `ACQUISITION_INGEST_ENABLED=1` on the staging service (client flags stay false), then
      `python scripts/acquisition_synthetic_check.py --base-url https://<staging> --allow-remote`
      (uses `internal_test`, excluded from the KPI); confirm in the staging database that the
      rows arrived, run `python scripts/acquisition_metric.py` and a retention pass, check that
      `acquisition_daily` counts equal the rows sent, and that a second pass adds nothing; delete
      the synthetic rows afterwards. The local half of this receipt is recorded in
      `OA-005_EVIDENCE.md`;
- [ ] **a production synthetic event has been checked, then deleted**: set
      `ACQUISITION_INGEST_ENABLED=1` on production (client flags still false), `curl` one
      well-formed `internal_test` event, confirm the row in Railway (Henri only), then
      `DELETE FROM acquisition_events WHERE source_group = 'internal_test'` and confirm it is
      gone; if a rollup pass ran meanwhile also remove the matching `acquisition_daily` row;
- [ ] in a real browser against staging with the flags flipped: the network log shows only the
      documented requests (same-origin, no cookies, no `Referer`, bodies as specified), no
      cookie or web-storage entry is created by the collector, `navigator.globalPrivacyControl`
      suppresses everything, and report routes send no token or URL;
- [ ] then flip **both** `ACQUISITION_COLLECTOR_ENABLED` and `var ENABLED` to `true` in one
      reviewed change. Tests assert the OFF state and must be edited deliberately in that
      change, so enabling cannot happen by accident:
      `tests/test_acquisition_wiring.py::test_collection_is_off_in_this_branch`,
      `utils/acquisitionEvents.test.ts` "the collector flag is false",
      `utils/acquisitionTransport.test.ts` "the flag is false and the installer installs nothing",
      `utils/acquisitionLandingScript.test.ts` "shipped file is OFF",
      `utils/acquisitionJourney.test.tsx` "collector flag false", and
      `e2e/acquisition-landing.spec.ts` (asserts no collector request). Merging to `main` auto-deploys and is Henri's decision (not covered by D-005).

## Primary metric: how to run it

Definition (MEASUREMENT.md, metric version `oa-metric-v1-draft`):

* **eligible organic landings** (denominator): `landing_observed`, `source_group =
  'google_organic'` (`paid_search` is its own group and is in neither count), `is_bot` false, `page_family` public (all families except `app`), one per
  `landing_id`, `received_at` within the window;
* **completions** (numerator): of those, landings with a `result_rendered` where
  `supported_result` is true sharing the `landing_id`, **excluding** any operation that also has
  a `render_failed` (render_failed wins). A landing counts once however many results it showed.

```bash
DATABASE_URL="postgresql://..." python scripts/acquisition_metric.py --from 2026-10-02 --to 2026-10-30
python scripts/acquisition_metric.py --sqlite /path/to/local.sqlite --from ... --to ...   # local only
```

The script (read-only, prints no identifier) runs `primary_metric_sql` from
`acquisition_store.py`; the same SQL text runs on Postgres and SQLite. Only the last 90 days
of raw events exist: a window reaching further back is incomplete (the script warns). Older
periods exist only as `acquisition_daily` event counts, which are **not** session-deduplicated:
use them for year-on-year *event* comparisons (`landing_observed` for `google_organic` public
families versus `result_rendered` with a supported `outcome_group`), never as the session KPI.

**Coverage caveats that must accompany any figure from this metric**

1. It measures *observed* landings only. Invisible: visitors with Global Privacy Control,
   JavaScript blocked, a content blocker that blocks the request, or an old cached page without
   the script.
2. No storage means no new-tab/typed-URL attribution: those start unattributed sessions. A
   reload is not counted as a landing (Navigation Timing `navigate` only); a browser without
   that API fails open and may double count a reload.
3. Attribution passes only through the site's own `/app` links, marked CTA links and the SEO
   registration form. A visitor who reaches the app any other way (typed URL, bookmark, a
   non-CTA link) has no `al`; a direct SPA landing is then its own landing (with the referrer
   it actually had, usually `internal` or `direct`).
4. `google_organic` is derived from the referrer origin, except that landings whose URL carried
   a paid-click marker are `paid_search` and excluded from numerator and denominator. Paid
   clicks without such a marker (untagged) remain indistinguishable from organic.
5. Landings on SPA routes other than `/` and `/app` (e.g. `/app/guides/*`) are not landings.
6. Restored/shared report links are never attributed to a landing and are excluded from the
   denominator (MEASUREMENT.md).
7. Completions are client-reported acknowledgements: they prove neither accuracy nor
   comprehension. Demo reports are never supported results (D-004).
8. Bot filtering is a conservative User-Agent pattern; unlisted automation is counted.
9. The date window is UTC; `render_failed` pairing needs an `operation_id`.
10. Anyone can POST well-formed events (the endpoint is unauthenticated, same as a page
    request); rate limiting and `is_bot` are the only mitigations. Treat very large single-day
    spikes as suspect and inspect before reporting.

Report the numerator, denominator, window, dates covered, missing periods and these caveats
together; no retrospective denominator change.

## Tests

`tests/test_acquisition_collector.py` (route, privacy, logging, schema parity, retention,
exactly-once, primary metric, background job; SQLite always, Postgres when
`ACQUISITION_TEST_PG_DSN` points at a throwaway local server), `tests/test_acquisition_wiring.py`
(OFF state, page wiring, notice), `utils/acquisitionTransport.test.ts`,
`utils/acquisitionLanding.test.ts`, `utils/acquisitionLandingScript.test.ts`,
`utils/acquisitionJourney.test.tsx`, plus the OA-004 suites.
