# Bounded service measurement v2

Status: OFF pending the enable gate. `oa-journey-30m-v2` replaces the unreleased draft metric. The separate `acquisition_v2_*` tables do not reinterpret v1 rows. `LIA_ACQUISITION_MEASUREMENT.md` in the parent directory records the privacy assessment; the public privacy notice and free objection control are part of the enable gate.

## Metric and window

Name: **Observed Google-organic public arrivals reaching a supported fresh-check render within 30 minutes**. Denominator: distinct observed arrival IDs with source `google_organic`, a public page family and non-bot classification. Numerator: those arrivals with a fresh `check_started` plus a delivered supported `result_rendered` for the same operation, with no `render_failed` for that operation within the fixed window. Dataset references, demos, unavailable states and restored/shared links are not supported fresh-check completions. A separate successful operation can complete a journey whose first operation failed. Repeated checks/renders do not add another denominator or numerator.

The window starts at the client arrival minute, ends 30 minutes later, and uses server receipt/admission within that half-open interval. This is approximately 29–30 elapsed minutes, not an inactivity timeout. There is no grace or activity extension. A late failure within the window cancels its operation; events arriving after the window are ignored, including delayed network retries. Client clock skew can make an otherwise legitimate journey unobserved. There is no claim about errors after the window or eventual MOT results. Aggregate UTC day is the arrival minute's UTC day, even when a render occurs after midnight.

The same reducer calculates provisional raw counts and final retained counts. The job finalises only closed windows, adds event/landing counts and deletes all individual rows in one transaction. Ingestion and finalisation share the database lock; ingestion rechecks expiry under that lock. Reruns/concurrent workers cannot duplicate the rollup. Expired events cannot recreate deleted IDs. The canonical metric reads retained counts only; `raw_metric` is provisional and must not be used for a completed-day report. Event counts are diagnostics, not distinct checks or people. Aggregates carry metric version and the frozen pilot label. Keep metric versions separate.

## Attribution and coverage

`static/acquisition-landing.js` is the sole browser attribution implementation, used by static pages, SEO templates and the SPA. It reads the referrer origin and checks paid-marker presence only; URLs and marker values are never transmitted. One sessionStorage context carries source, arrival ID, start minute and pilot label through Google → guide → model → app. No link or URL fragment is decorated. The pilot label identifies the entry page, not every page later touched.

An internal hop or reload/back-forward preserves a valid context but emits no new arrival. A fresh external/direct navigation starts a new observation; a new tab may be a separate observation, and duplicated tabs may inherit sessionStorage. Do not call the denominator users or sessions. An expired context is not revived by internal navigation. A direct saved-report route, missing Navigation Timing, blocked storage/randomness, GPC/objection or failed transport remains unobserved. A restored report in an existing context is a diagnostic event and never a fresh-check completion. Unobserved visits are **unknown**, not zero, and there is no reliable total-visitor coverage fraction. Do not divide by GSC clicks to manufacture a conversion rate.

Paid markers (`gclid`, `gbraid`, `wbraid`, `msclkid` or recognised paid `utm_medium`) suppress collection. Untagged ads can still resemble organic referrers; the classification is observed Google-referrer traffic after known paid-marker exclusion, not proof of unpaid origin. Same-origin navigation without a valid context is unobserved. UA bot filtering is only a heuristic; this is an unauthenticated client-reporting endpoint, not a fraud-proof business ledger.

## Storage, privacy and controls

The endpoint validates all fields and combinations, limits bodies to 2048 bytes and rate-limits abuse. It accepts no direct customer/report/vehicle identifier, query/URL/referrer or free text. No body content is logged. Receipt timestamps are minute-grained; possible correlation with hosting logs remains a residual risk. GPC is honoured server-side as well as in the browser. Transport has no credentials/referrer/cache and at most one retry with the same event ID within ten seconds; objection or expiry stops retries.

The top-of-page measurement control stops new transmission and clears the local context. Only an off preference is kept in localStorage, for 90 days, solely to remember that choice. Browser context expires after 30 minutes; server raw data is normally deleted within about 35 minutes of its start, as part of aggregation. The job runs every five minutes and retries failures after one minute, including when ingestion is disabled. Job failures can extend retention; operational monitoring must catch overdue deletion. Aggregates are kept for three calendar months. Detailed small cells are restricted; externally published statistics must suppress/combine cells below five. SQL deletion is not a provider-backup or network-log erasure guarantee.

Automatic Umami collection is retired; historical exports remain separate evidence with known coverage gaps. Google Ads retains its separate explicit-consent mechanism and receives none of these IDs. Never join acquisition IDs to customer/report/garage data or logs. Qualified enquiries and commercial outcomes follow `COMMERCIAL_MEASUREMENT.md` and are reported separately.

## Schema

Body fields: strict event schema version 2 and metric version `oa-journey-30m-v2`; random UUID event/session/landing IDs; optional operation UUID and release SHA; integer `window_start_minute`; allowlisted `pilot_group`; page/source classes; event-specific enum/boolean fields. `event_schema_v2.json` defines the acknowledgement fields; envelope validation is in `acquisition_routes.py`. No v1 body is accepted. The `session_id` is random document context, not a durable session measure.

Frozen pilot labels: `none`, `cost`, `checklist`, `corsa`, `c3`, `clio208`, `polofiesta`, `yarisjazz`. Exact public-path mapping is in the shared script. These labels are preallocated for the selected existing URLs; content hypotheses and actual release receipt are separate. No claim that pilot content is already released follows from a label existing.

## Enable gate and rollback

1. Pass combined local checks and all required GitHub checks, including real-image PostgreSQL acceptance.
2. Verify hosting DPA/transfer/backup/log boundaries in the private acceptance receipt; inspect rendered notice and objection control, and no automatic Umami loader.
3. Merge and deploy the disabled client code through normal CI. Migrate v2 tables. Migration is additive; no old table or customer record is dropped.
4. Enable server ingestion while clients remain disabled. Send only labelled `internal_test` events to production, confirm stored receipt and one-row dedup, GPC suppression, aggregate/deletion after the real window and disabled-ingest rollback. Synthetic internal-test rows never enter the primary metric.
5. Only after that receipt, flip both client constants together in a separate checked release. Record exact SHA, actual production activation/verification time and any coverage gap. Confirm the live switches agree and retention is functioning.
6. Roll back by disabling `ACQUISITION_INGEST_ENABLED` and reverting both client constants; keep the retention job running. Database rollback/drop is for disposable staging only; preserve completed aggregate evidence in production. Do not execute the synthetic script's future-clock retention against a live database.

Pilot Day 1 is the first complete UTC reporting day after the later of verified collection and pilot release. GSC's Pacific reporting dates remain separately labelled. Review after 28 complete days, extend to 56 if too little exposure or coverage, and record expand/hold/rework. There is no automatic traffic target or causal attribution from an uncontrolled before/after comparison.
