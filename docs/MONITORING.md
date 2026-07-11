# Monitoring & Alerting Setup

This file describes required monitoring; it does not prove that an external
monitor or alert route has been configured. Record provider screenshots/IDs and
owners in the release evidence before treating a check as operational.

## Free Uptime Monitoring (Item 4)

### Option A: UptimeRobot (Recommended)
1. Go to [uptimerobot.com](https://uptimerobot.com)
2. Create free account (50 monitors free)
3. Add monitor:
   - **Type:** HTTPS
   - **URL:** `https://www.autosafe.one/health`
   - **Interval:** 5 minutes
   - **Alert contacts:** Your email

### Option B: BetterUptime
1. Go to [betteruptime.com](https://betteruptime.com)
2. Free tier includes: 5 monitors, 3-minute interval
3. Same setup as above

---

## Simple Analytics (Item 6)

### Option A: Plausible (Privacy-focused)
1. Go to [plausible.io](https://plausible.io) (~€9/month)
2. Add tracking script to `index.html`:
```html
<script defer data-domain="autosafebackend-production.up.railway.app" src="https://plausible.io/js/script.js"></script>
```

### Option B: Umami (Free Self-hosted)
1. Deploy Umami on Railway (free)
2. Add tracking script to pages

### Option C: GoatCounter (Free)
1. Go to [goatcounter.com](https://goatcounter.com)
2. Add tracking script:
```html
<script data-goatcounter="https://YOUR-CODE.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>
```

---

## Staging Environment (Item 8)

### Create on Railway:
1. In Railway dashboard, click **+ New Project**
2. Select **Deploy from GitHub repo** (same repo)
3. Name it `autosafe-staging`
4. Set environment variables (same as production):
   - `DATABASE_URL` (point to separate staging DB)
   - OR use same DB with read-only
5. Use a different domain: `autosafe-staging.up.railway.app`

### Workflow:
1. Test changes locally
2. Push to `staging` branch → auto-deploys to staging
3. Verify on staging URL
4. Merge to `main` → auto-deploys to production

---

## RC1 additions (2026-07-11)

### Release identity and availability

Monitor both endpoints from outside Railway every five minutes:

- `GET https://www.autosafe.one/health`
- `GET https://www.autosafe.one/api/version`

Alert after two consecutive failures. On every deployment, record the expected
commit SHA and verify that `/api/version` returns that non-`unknown` backend
identity before accepting traffic. A healthy endpoint with the wrong identity
is a failed release.

### Application signals

`report_routes.py` emits structured, correlation-ID-bearing log markers. Create
saved searches/counters for:

- `report_persist_failed` — an insert or idempotency replay failed;
- `report_persist_unavailable` — PostgreSQL was unavailable and the response
  intentionally disabled sharing;
- `report_api_error error_code=dvsa_unavailable` — live vehicle history was
  unavailable; and
- `report_api_error error_code=report_not_found` — an opaque share token was
  absent or malformed.

Suggested initial alerts, to be tuned from a clean 48-hour baseline:

- any `report_persist_failed` or `report_persist_unavailable` event in 15
  minutes: page the release owner;
- five `dvsa_unavailable` responses in 10 minutes, or more than 5% of report
  attempts when a denominator is available: investigate upstream health;
- three times the baseline `report_not_found` rate for 15 minutes: inspect
  routing, BASE_URL, token handling, and abuse; and
- HTTP 5xx above 1% for five minutes or two failed external health probes:
  rollback/canary decision.

These thresholds are release defaults, not evidence that Railway alerting is
already configured. Save the query/alert IDs and destination test in the
release record.

### Privacy operations

`scripts/retention_sweep.py` is dry-run by default and prints candidate counts,
batch progress, and post-run verification without printing raw PII. Run it on a
documented schedule with `--execute` only in the controlled job. Alert on:

- non-zero process exit;
- `stale_plaintext > 0` after execution;
- `pseudonymised_with_payload > 0` after execution; or
- a missed scheduled run.

Record row counts and timestamps, not registrations, postcodes, request bodies,
or share tokens.

### Analytics and error tracking

Umami/Google funnel events must contain only the documented aggregate fields.
Do not send registration, postcode, report token, request body, or free text.
Sentry-style error tracking is not implemented in RC1; if added later, explicit
request-body and URL-token scrubbing is a prerequisite.

### 48-hour release watch

For an authorised production deploy, check identity/health immediately, then
review persistence, typed API errors, 5xx, latency, privacy-job state, and one
synthetic share flow at 1 hour, 4 hours, 24 hours, and 48 hours. Name the human
owner and rollback authority in the deployment record.
