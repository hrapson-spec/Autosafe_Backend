# RC1 privacy and retention reconciliation

## Bottom line

RC1 materially reduces identifier exposure: report creation uses a POST body,
share links use opaque tokens, postcode is excluded from report responses, and
aged operational records can be pseudonymised with keyed HMAC values. Release
still depends on supplying the HMAC secret, running the backlog migration, and
scheduling/monitoring retention in the deployed environment.

This is an engineering reconciliation, not legal advice. The product owner
remains responsible for confirming the privacy notice, lawful basis, processor
arrangements, and retention policy.

## Data-flow inventory

| Stage | Data | RC1 handling | Evidence |
|---|---|---|---|
| Browser input | VRN, postcode, optional mileage | Sent in the JSON body of `POST /api/v2/reports`; not placed in the request URL | `services/reportApi.ts:106-132` |
| Upstream lookup | Normalised VRN and vehicle/history data | Used to obtain recorded MOT evidence; dependency failure is typed, not silently replaced | `report_routes.py:568-676`; `report_contract.py:95-113` |
| Report response | Vehicle/report facts and opaque share URL | VRN may be displayed to the requesting user; postcode is not returned; share URL is `/report/{token}` | `report_routes.py:180-245` |
| Operational storage | Report ID/token hash, report payload, normalised identifiers, provenance, expiry | One durable insert with an expiry; retrieval uses the token-derived key | `database.py:1196-1343` |
| Public sharing | Opaque token | No VRN or postcode in the URL; token retrieval has not-found/expired/unavailable states | `report_routes.py:679-728`; `components/ReportCopy.tsx:217-274` |
| Aged records | VRN/postcode in `risk_checks` | Rows older than 24 months receive keyed HMACs and plaintext fields are nulled | `scripts/retention_sweep.py:127-147`, `:188-212`, `:327-334` |
| Existing backlog | Historical plaintext identifiers | Separate resumable migration applies the same HMAC/null discipline | `migrations/pseudonymize_backlog.py:245-265` |

## Purpose and minimisation

The service needs a registration to obtain the vehicle and recorded MOT
history, postcode for regional comparison/garage functionality, and optional
mileage to choose the correct evidence cohort. The v2 response carries the
fields needed to render and share the report. It does not echo postcode and it
does not encode either identifier in the share URL.

Component detail is withheld when the evidence cannot support it, which also
avoids generating additional inferred data without a defensible source
(`report_service.py:416-505`).

## Retention and pseudonymisation

- Operational retention threshold: 24 months
  (`scripts/retention_sweep.py:127-147`).
- Pseudonyms: keyed HMAC, not a reversible encryption scheme and not an
  unkeyed hash (`scripts/retention_sweep.py:188-212`).
- Plaintext removal: registration/VRN and postcode fields are nulled after
  successful pseudonym creation (`scripts/retention_sweep.py:327-334`).
- Database preparation: RC1 makes the relevant plaintext columns nullable and
  adds report contract fields/indexes
  (`migrations/add_report_contract_columns.py:47-84`).

Pseudonymised data remains personal data when it can be related back with
additional information. Access to the HMAC key and database must therefore
remain restricted.

## Logging and analytics boundary

Release review must verify that application, proxy, analytics, error tracking,
and Railway logs do not capture request bodies, raw VRNs, postcodes, or opaque
share tokens. Monitoring should use aggregate event names, counts, status
codes, report IDs where operationally necessary, and release identity—not raw
customer inputs.

Sentry/error-tracking integration is not implemented by RC1. Do not add it by
copying full request payloads; configure explicit scrubbing first.

## Data-subject and governance reconciliation

The existing privacy page describes the service's inputs, uses, sharing,
retention, and rights (`components/PrivacyPage.tsx:69-97`, `:197-250`). The
legitimate-interest risk checks and safeguards are recorded in
`docs/LIA_RISK_CHECKS.md:11-88`. Before production release, the owner should
confirm that those statements match the deployed processors and actual
operational retention job.

## Required deploy evidence

- [ ] `VRM_HMAC_KEY` is present, randomly generated, at least 32 characters,
      access-controlled, backed up, and not logged.
- [ ] The schema migration completed and row counts were reconciled.
- [ ] Backlog pseudonymisation dry run and live run were reviewed.
- [ ] The 24-month sweep is scheduled, monitored, and has an accountable owner.
- [ ] Fresh report and share URLs contain no postcode or VRN.
- [ ] Railway/proxy/application logs were sampled with synthetic data and show
      no raw identifier, token, credential, or body.
- [ ] The deployed privacy page and LIA match the actual providers and flow.
- [ ] A deletion/access-request lookup and evidence-preserving incident path
      have named owners.

## Residual risks

- An opaque bearer token grants access to its report; leakage must be handled
  as unauthorised disclosure even though the URL has no obvious identifier.
- HMAC key loss prevents consistent future lookup; key disclosure weakens
  pseudonymisation.
- A retention script that exists but is not scheduled provides no operational
  retention guarantee.
- External processors and platform logs can create copies outside the
  application database; their settings and retention must be checked directly.
