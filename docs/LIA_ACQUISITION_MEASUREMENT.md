# Legitimate Interests Assessment — first-party acquisition measurement (`acquisition_events`)

**Controller:** AutoSafe (sole trader — Henri Rapson)

**Review date:** 2026-10-01

**Status:** engineering reconciliation for owner/legal review; not legal advice. Sourced from
`DECISIONS.md` D-005, D-006 and D-007 in the organic-acquisition work area (the authority for
these decisions until Henri amends it). The collector is implemented with **collection OFF**;
this assessment describes the processing that would begin at the enable gate in
[`acquisition/COLLECTOR.md`](acquisition/COLLECTOR.md) and is conditional on that gate being met.
Evidence: [`acquisition/OA-005_EVIDENCE.md`](acquisition/OA-005_EVIDENCE.md).

## 1. Processing actually implemented

When a visitor loads a public page or the check tool, or moves through a check, the browser sends
a small same-origin request ("measurement event") to `POST /api/acquisition/events`. An event holds
only: an event type (public page or app opened, check started, report created, result displayed /
unavailable / failed); random identifiers created in the browser for the page visit (`session_id`),
for one deliberate check (`operation_id`) and for the landing visit (`landing_id`); an allowlisted
page category (`page_family`, never a path); an arrival category (`source_group`: Google organic,
paid search, other search, referral, direct, internal, unknown); fixed outcome enums and booleans
(result kind, match scope, whether the result was supported, saved or unsaved); the software
release id; the receipt time (stored **truncated to the minute**); and a derived yes/no `is_bot`
flag.

The server stores **no** IP address, User-Agent, Referer, query string, URL, registration,
postcode, report token or id, vehicle make or model, failure rate, sample size, email or free text.
The User-Agent is read once to compute `is_bot` and discarded. The client IP is used only by the
in-memory rate limiter. A redaction filter removes the IP from the rate-limiter's own warning
message on every route (D-007.2), so no application log carries a client IP for this processing.

Nothing is stored on or read from the visitor's device (no cookies, localStorage, sessionStorage
or IndexedDB, no fingerprinting). The identifiers exist in page memory only. The landing id reaches
the app only in the **URL fragment** of the site's own links (`#al=…&src=…`), which browsers never
send in a request or Referer; the first script in `index.html` removes it before any third-party
script exists (D-007.1). The arrival category is derived in the browser from the referrer's
**origin** only, or is `paid_search` when the landing URL carries `gclid`, `gbraid`, `wbraid` or
`utm_medium=cpc|ppc|paid`; the marker and its value are only tested, never sent or stored (D-006).
A reload or back/forward is not recorded as a landing (D-006). A browser sending
`navigator.globalPrivacyControl === true` (or `Sec-GPC: 1`) sends and records nothing.

Storage is the existing Railway Postgres (no new processor): raw events in `acquisition_events`,
daily event counts in `acquisition_daily`, and per-day landing-level counts in
`acquisition_landing_daily` (counts only, no identifiers; D-007.4). Access: the site operator only,
via Railway; there is no read route and no public dashboard.

Purpose recorded for owner review:

- **P1 — Measure, in aggregate, whether visitors arriving from search reach a displayed, supported
  result,** so broken journeys are found and the free service can be judged against the people it
  is meant to reach. Product and service improvement only.

**Not a purpose:** advertising, profiling, personalisation, any per-person decision, sale or
sharing, linkage to a vehicle, report or person, or use of the data to contact anyone. Adding any
of these requires a new assessment.

## 2. Purpose test

P1 is a genuine interest. The service's central promise is a displayed, scope-labelled result;
server-side report counts cannot show what was displayed (E04) and third-party analytics are not
permitted on bearer report routes. Knowing whether search visitors actually see a supported result
is necessary to decide whether the pages, the check flow and the evidence ladder work, and it
benefits users because failures and dead ends get fixed. It is not a speculative or unlawful
interest, and it is stated before collection begins (notice, section 6).

## 3. Necessity and minimisation test

- The measure needs a join between a landing and a later displayed result. A random per-landing id
  carried for the length of one visit is the least intrusive way to do that without identifying a
  person or storing anything on the device; a cookie, storage key or fingerprint would be more
  intrusive and is not used.
- No field identifies the vehicle, report or person; no content (registration, postcode, rate,
  sample size, make/model) is collected, because the metric does not need any.
- The arrival category uses the referrer's origin only. The landing page path is reduced to a
  category. The paid-click marker is tested, not recorded.
- Receipt time is truncated to the minute so a row cannot be joined by second to an external
  timestamped log (D-007.5).
- Raw events are kept for 90 days, only long enough to compute and audit the metric. Before they
  are deleted, identifier-free per-day landing counts are written so the metric can be compared
  year on year (D-007.4); that aggregate, not the raw event, is what is kept for 25 months.
- Retention runs whenever the tables exist, whatever the ingest flag says, so switching collection
  off never stops deletion (D-007.3).

Less intrusive controls adopted: fragment (not query) handoff; memory-only identifiers; GPC
honoured client- and server-side; no IP/UA/Referer/URL stored; no IP in logs; no third-party
involvement; one retry only, no queue or persisted backlog; collector disabled by default behind
three independent switches.

## 4. Balancing test

- **Nature of data:** pseudonymous random identifiers and categorical values. Not special-category.
  The identifiers are not derived from any input and are not usable to access a report. They may
  still be personal data in principle (a random id plus a receipt time, held with access to the
  database), so this assessment treats the processing as personal-data processing, not as
  anonymous.
- **Expectations:** a visitor to a website can reasonably expect it to measure its own pages and
  whether its service works, particularly where it is disclosed in the privacy notice, uses no
  cookies or storage, sends no data to third parties, and honours Global Privacy Control.
- **Impact:** negligible. No decision, content, contact or profile results; no recipient other than
  the existing hosting processor; raw data is short-lived and aggregates hold no identifiers.
  Residual risks: a database compromise would expose random ids with timestamps and categories (no
  identity, no vehicle, no content); a raw edge/proxy log held by the hosting provider records
  request paths and IPs, but the landing id is not in them because it travels in the fragment, and
  `received_at` is minute-truncated.
- **Safeguards:** listed in sections 1 and 3, plus rate limiting, strict schema validation
  (`extra='forbid'`, 2 KB limit), log-capture tests, retention tests with a frozen clock and a
  Postgres-backed receipt, and a documented enable gate and rollback.
- **Disclosure:** no sale or sharing. The privacy notice carries a factual entry (fields, retention,
  basis, objection) that is part of the enable gate.

**Provisional balance:** legitimate interests supports P1 as implemented. The impact on individuals
is negligible and within reasonable expectations, and the safeguards operate by construction and are
tested. The balance depends on the conditions in section 7.

## 5. Retention and rights

- Raw events: deleted 90 days after receipt by the scheduled, idempotent job
  (`acquisition_routes.run_retention_once`), which first rolls each event, exactly once, into the
  identifier-free daily aggregates.
- `acquisition_daily` and `acquisition_landing_daily`: deleted after 25 calendar months.
- Rollback of collection: set the client constants to false and/or unset
  `ACQUISITION_INGEST_ENABLED` (stops collection immediately; retention continues). Deleting
  everything: `python migrations/add_acquisition_tables.py --rollback`.

**Objection and rights.** Visitors can object by Global Privacy Control (nothing is then sent or
recorded) or by email to `autosafehq@gmail.com`, as stated in the notice. Because identifiers are
random, unlinked to the person and not shown to them, a request to locate or erase "my events"
generally cannot be fulfilled by looking anything up; the notice says so, and GPC is the reliable
route. Access, erasure and restriction requests that do identify an event (for example a visitor
who supplies their own `landing_id` from a copied link) can be met by deleting that row; the same
statutory timescales apply as for other requests. Do not place identifiers in ordinary tickets or
logs while handling a request.

## 6. Notice and transparency

The privacy notice (`static/privacy.html` and `components/PrivacyPage.tsx`) has a factual section,
table rows and a retention row for this processing. It is written in the present tense and goes
live on merge, slightly ahead of enabling; D-007.8 accepts that this over-discloses rather than
under-discloses and requires the gap to be kept short. The notice text is part of the enable gate
and is re-read immediately before the flags are flipped.

## 7. Operational conditions

This assessment is conditional on:

- the enable gate in `COLLECTOR.md` being completed: migration run before ingest is enabled,
  synthetic staging events received and aggregated, and a production synthetic event checked and
  deleted;
- **recording Railway's HTTP-log retention** for the service (request paths and IPs are logged at
  the edge; the design keeps the landing id out of them, but their retention is not otherwise
  established) — **open at the date of this review**;
- both client flags (`ACQUISITION_COLLECTOR_ENABLED` and the public script's `ENABLED`) being flipped
  together, enforced by a test, with the server flag kept as the fast rollback;
- the retention job running and being checked (log line `acquisition_retention …`, zero raw rows
  older than 90 days);
- no new field, identifier, storage mechanism, recipient or purpose being added without a refreshed
  assessment; and
- the accepted limitations being reported with any figure from the metric: observed landings only,
  new-tab and typed-URL sessions unattributed, untagged paid traffic indistinguishable from
  organic, reused copied CTA links counted once per landing, `restored_link` renders that carry an
  in-memory landing id counted with `entry_mode` reported, and a client being able to post
  `internal_test` (excluded from the metric).

## 8. Review triggers

Review before any new field or identifier, any storage on the device, any third-party processor or
recipient, any use beyond P1, any linkage to a vehicle, report or person, any change to retention
or to the fragment/query handoff, any change to the notice wording that alters what is disclosed, a
security incident, a change in Railway logging behaviour, or by 2027-10-01, whichever occurs first.
