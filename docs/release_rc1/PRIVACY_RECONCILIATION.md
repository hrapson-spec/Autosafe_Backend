# RC1 privacy and retention reconciliation

## Bottom line

RC1 materially reduces identifier exposure: report creation uses a POST body,
share URLs contain an opaque bearer token instead of a VRN or postcode,
application access logging is disabled, application log fields are scrubbed,
analytics are suppressed on saved-report routes, and both check-record and
lead retention have executable, dry-run-first jobs.

Those controls do not by themselves prove production compliance. Release still
depends on the database migration, secrets, scheduled retention jobs, processor
configuration, and a synthetic Railway log review. This document is an
engineering reconciliation, not legal advice.

The notice wording was cross-checked on 2026-07-11 against the ICO's current
guidance on the [legitimate-interests three-part
test](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/lawful-basis/legitimate-interests/what-is-the-legitimate-interests-basis/),
[cookies and similar
technologies](https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/guide-to-pecr/cookies-and-similar-technologies/),
and [international
transfers](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/international-transfers/),
plus DVSA's current [MOT history API privacy
notice](https://www.gov.uk/government/publications/dvsa-privacy-notices/mot-history-api-privacy-notice).
That source check is not a substitute for verifying the actual processor
contracts, account regions, transfer mechanism, or obtaining owner/legal
approval.

## Implemented data flow

| Stage | Data | RC1 handling | Primary evidence |
|---|---|---|---|
| Browser input | VRN, postcode in the current homepage form, optional mileage | Sent in the JSON body of `POST /api/v2/reports`; the API permits postcode to be absent for report/email paths, while the current homepage UI requires it. Legacy `reg`, `registration`, and `postcode` query parameters are stripped before analytics can load | `components/HeroForm.tsx`, `services/reportApi.ts`, `App.tsx`, `index.html`, `static/consent.js` |
| Vehicle lookup | Normalised VRN and returned vehicle/MOT data | Used to identify the vehicle and its latest recorded MOT evidence; upstream failures are typed | `report_routes.py`, `dvsa_client.py`, `report_contract.py` |
| Comparison | Vehicle model, age, and available mileage | Selects the closest supported aggregate cohort. Postcode does not select or alter the comparison cohort | `report_service.py`, `database.py` |
| Report response | Vehicle facts, provenance, comparison result, optional share data | Postcode is never returned. A saved report receives `/app/report/{token}`; a failed save receives no report ID, token, expiry, or share URL | `report_routes.py`, `report_contract.py` |
| Check-record storage | Normalised identifiers, selected/latest MOT fields, comparison/provenance, saved payload, idempotency key, expiry | PostgreSQL stores the opaque report token in plaintext because it is the bearer lookup key. It is not hashed. Stored referrers retain the HTTP(S) origin only; path, query, fragment, and credentials are discarded | `database.py`, `report_routes.py`, `utils.py` |
| Public retrieval | Opaque bearer token | Token lookup returns the saved payload, or typed not-found, expired, or storage-unavailable states. Possession of a live token grants report access | `database.py`, `report_routes.py` |
| Lead storage | Email and the contact/vehicle fields needed for an emailed report, reminder, or consented garage enquiry | Postcode is nullable. Mileage source and comparison scope are persisted so downstream email/garage output does not invent provenance | `create_leads_table.py`, `database.py`, `lead_distributor.py` |

## Purpose and minimisation

Registration is required for the vehicle/MOT lookup. Age and available mileage
select the aggregate comparison cohort. Postcode is captured in the operational
check record under the current notice, but it is not used in scoring; garage
matching uses it only after the user submits the separate consented enquiry.
That distinction must remain accurate in product copy and privacy records.

RC1 stores selected/latest MOT fields and the report payload, not a claim that a
complete MOT history or vehicle diagnosis has been retained. Component and
repair sections are omitted when their source evidence is incomplete, including
when any contributing aggregate row lacks a component value.

RC1 implements no later-outcome linkage pipeline. A future proposal to connect
report records to subsequent MOT outcomes requires its own lawful source,
purpose, minimisation design, notice, evaluation protocol, and approval.

## Retention controls

### Check records

- Default window: 24 calendar months.
- `scripts/retention_sweep.py` is dry-run by default and requires
  `VRM_HMAC_KEY` of at least 32 characters for `--execute`.
- On eligible `risk_checks` rows it creates a keyed HMAC-SHA256 of the
  normalised VRN when a VRN exists, then nulls plaintext registration,
  postcode, saved report payload, and report token, and stamps
  `pseudonymised_at`.
- Postcode is removed, not hashed. Remaining rows and the keyed VRN digest are
  still pseudonymised data and must remain access-controlled.
- `migrations/pseudonymize_backlog.py` provides the equivalent one-time,
  owner-cutoff workflow for records covered by the earlier notice.

### Leads

- Default window: 12 calendar months.
- `scripts/lead_retention_sweep.py` is dry-run by default.
- `--execute` deletes dependent `lead_assignments` and then the corresponding
  old `leads` in bounded transactions, and fails if verification finds stale
  rows.
- Output contains database row IDs and counts only, not contact or vehicle
  fields.

The migration deliberately drops `NOT NULL` from both
`risk_checks.registration` and `leads.postcode`. Those nullability changes are
one-way compatibility changes and are not restored by migration rollback.

## Logging and analytics boundary

- Uvicorn application access logging is disabled. Application events use
  correlation IDs, keyed VRN digests where needed, exception type names,
  redacted route shapes, and origin-only referrers.
- Report bearer tokens are redacted from application log paths and referrers.
- Google Ads code loads only after explicit consent and sends no automatic page
  view.
- Product fonts and build assets are local/system resources; pages do not
  preconnect to or fetch Google Fonts before consent. The CSP omits remote
  font and unused CDN hosts.
- Umami is configured with automatic tracking disabled. Custom events accept a
  fixed field allowlist, and all custom tracking is disabled on
  `/app/report/...`, including after SPA navigation.
- Standalone legal/guide pages use the shared consent gate. Sensitive legacy
  query keys are removed before optional analytics load.
- The obsolete Reddit and press/data-story publishers are removed. They cannot
  send third-party content or log Reddit text, generated replies, contact
  names, or contact addresses from the release image.

Railway edge/proxy logs, database logs, processor dashboards, browser network
requests, and backups are outside the application logger. They require direct
candidate-environment verification; code review cannot close that gate.

## Required deployment evidence

- [ ] `VRM_HMAC_KEY` is randomly generated, at least 32 characters,
      access-controlled, backed up, and absent from logs.
- [ ] The expand migration completed and both nullability changes were
      verified.
- [ ] Check-record backlog and rolling retention dry runs were reviewed; the
      production job is scheduled, monitored, and owned.
- [ ] Lead retention dry run and staging execution rehearsal passed; the
      production job is scheduled, monitored, and owned.
- [ ] Fresh create/share requests put no VRN or postcode in URLs.
- [ ] Synthetic Railway edge, proxy, application, database, and analytics
      evidence contains no raw VRN, postcode, email content, request body, or
      bearer token.
- [ ] The deployed privacy/terms copies match the actual processors, regions,
      retention schedules, and data flow.
- [ ] Access, deletion, objection, token-disclosure, and incident procedures
      have named owners.

## Residual risks

- The saved-report token is a plaintext bearer credential in the database and
  URL. Leakage is unauthorised disclosure even though the token is opaque.
- A token expires after 90 days, but the database value remains until the
  24-month sweep unless an earlier deletion request or incident process removes
  it.
- HMAC key disclosure weakens pseudonymisation; key loss prevents consistent
  future joins.
- Retention code that is not scheduled and monitored provides no operational
  retention guarantee.
- Postcode is retained with check records despite not affecting the comparison;
  the owner should keep this necessity under review.
- External processors and platform logs can create copies outside the
  application database and must be configured and checked directly.
- Processor account regions, agreements, and any restricted-transfer mechanism
  have not been established from repository evidence. They remain a production
  blocker, not an inference from vendor marketing pages.
