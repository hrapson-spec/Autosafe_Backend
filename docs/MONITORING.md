# Monitoring and alerting — RC1

This file defines the required signals and release thresholds. It is not proof
that Railway monitors, saved searches, alerts, or retention schedules exist.
Record provider IDs, tested destinations, and named owners before GO.

## External availability and identity

Probe these public endpoints every five minutes:

- `GET https://www.autosafe.one/health`
- `GET https://www.autosafe.one/ready`
- `GET https://www.autosafe.one/api/version`

Alert after two consecutive availability failures. On every deployment compare
the full expected SHA with both `backend_sha` and `frontend_sha`, require a
64-character `frontend_bundle_hash`, the expected contract version, and a real
build timestamp. A healthy service with the wrong identity is a failed release.

Application access logging is intentionally disabled, so availability, status
rate, and latency dashboards should use Railway/platform metrics that have been
verified not to retain sensitive URLs or request bodies.

## Application events

Create counters/saved searches for the structured markers emitted by the v2
route:

- `report_persist_failed`
- `report_persist_unavailable`
- `report_api_error error_code=dvsa_unavailable`
- `report_api_error error_code=storage_unavailable`
- `report_api_error error_code=idempotency_conflict`
- `report_api_error error_code=report_not_found`
- `idempotency_lookup_unavailable`
- `idempotency_replay_payload_invalid`

Initial release thresholds, to be tuned from a clean 48-hour baseline:

- any persistence failure/unavailable event in 15 minutes: page the release
  owner;
- five DVSA/storage-unavailable responses in 10 minutes, or more than 5% of
  report attempts when a trustworthy denominator exists: investigate;
- any invalid stored replay payload: investigate contract/data drift;
- three times baseline not-found/conflict rate for 15 minutes: inspect routing,
  client retry identity, token handling, and abuse;
- HTTP 5xx above 1% for five minutes or two failed probes: rollback decision.

Do not add raw VRNs, postcodes, tokens, referrer paths/queries/fragments,
request bodies, or free-form exception text to make these events easier to
search. Only a validated HTTP(S) referrer origin may be persisted.

## Retention operations

### Check records

Run `scripts/retention_sweep.py --execute` on the approved schedule. Alert on:

- non-zero exit;
- `stale_sensitive_fields > 0` after execution;
- `pseudonymised_with_sensitive_fields > 0`; or
- missed/stale scheduled execution.

### Leads

Run `scripts/lead_retention_sweep.py --execute` on the approved schedule. Alert
on:

- non-zero exit;
- `stale_leads > 0` after execution;
- foreign-key/dependent-assignment deletion failure; or
- missed/stale scheduled execution.

Record counts, timestamps, duration, and job identity only. Do not record
registration, postcode, email, name, phone, message text, or bearer token.

## Analytics boundary

Current code already implements the allowed boundary; do not paste additional
third-party tracking scripts into templates:

- Google Ads loads only after explicit consent, with denied defaults,
  personalised ads denied, and automatic page views disabled.
- Product fonts/assets remain local or use system fallbacks; do not reintroduce
  pre-consent remote font/CDN requests or widen the CSP for them.
- Umami uses `data-auto-track=false`; only fixed-name custom funnel events are
  sent.
- Event data uses a fixed allowlist. Registration, postcode, email, token,
  request body, referrer path/query/fragment, and free text are forbidden.
- `/app/report/...` disables custom analytics, including SPA navigation into a
  saved report.
- Standalone pages load `/static/consent.js`; legacy sensitive query keys are
  removed before analytics.

The Railway candidate must verify these claims in browser network traffic and
in each processor dashboard using synthetic values. Sentry-style error tracking
is not implemented; request-body and bearer-URL scrubbing is a prerequisite to
adding it.

## 48-hour production watch

For an explicitly authorised production release, check identity/health at
deployment and review persistence, typed errors, 5xx, latency, retention job
state, consent/analytics behaviour, and one synthetic share flow at 1 hour, 4
hours, 24 hours, and 48 hours. Record the human owner and rollback authority.

## Evidence required for GO

- external monitor IDs and a successful alert-destination test;
- Railway metric/dashboard and saved-search IDs;
- check-record and lead job schedule IDs plus last successful rehearsal;
- screenshots/exported evidence from synthetic log and analytics inspection;
- named monitoring, privacy, and rollback owners.
