# Legitimate Interests Assessment — vehicle check records (`risk_checks`)

**Controller:** AutoSafe (sole trader — Henri Rapson)

**Review date:** 2026-07-11

**Status:** engineering reconciliation for owner/legal review; not legal advice

## 1. Processing actually implemented

When a user requests a report, RC1 stores a check record containing the
normalised VRN, optional postcode, selected vehicle attributes, latest recorded
MOT date/result, mileage and its provenance, the aggregate comparison result and
evidence scope, safe referrer, saved report payload, opaque share bearer token,
idempotency key, and expiry. It does not store a claim that the complete MOT
history or an exact-vehicle diagnosis exists.

The browser sends inputs in a POST body. Postcode is not returned, placed in the
share URL, or used to choose the comparison cohort. The service reports the
historical failure rate of the closest supported comparable-vehicle group; it
does not predict or determine the checked vehicle's next MOT result.

Purposes recorded for owner review:

- **P1 — Service operation and support:** create/retrieve a saved report,
  resolve idempotent retries, investigate failures/abuse, and honour rights
  requests.
- **P2 — Comparison quality evaluation:** assess whether the aggregate cohorts
  and displayed rates remain useful against later lawful outcome evidence.
- **P3 — Aggregate service monitoring:** measure availability, persistence,
  evidence-source mix, and funnel health without sending identifiers to
  analytics.

Garage enquiries, reminders, and emailed-report requests are separate lead
records. Garage disclosure is consent-based and is not justified by this LIA.

## 2. Purpose test

P1 is a genuine interest: a saved report cannot be restored without a durable
payload and bearer lookup key, and support/rights handling needs a way to locate
the record. P2 is a genuine quality interest only if any later outcome source
and linkage method are lawful, technically validated, proportionate, and
accurately disclosed. P3 is a genuine service-reliability interest.

No RC1 code implements a later-outcome linkage pipeline. This LIA therefore does
not claim that a particular DVSA open-data fingerprint, re-query method, or
calibration process is already operating or approved. Before implementing one,
record the source, licence/API terms, fields, matching error rate, access
controls, opt-out/deletion effect, and a refreshed balancing decision.

## 3. Necessity and minimisation test

- The VRN is necessary transiently to obtain vehicle/MOT data.
- A saved payload and opaque token are necessary only while public share
  retrieval is offered. The token is a plaintext bearer credential and must be
  treated as sensitive.
- Model, age, and available mileage are necessary for cohort selection. Mileage
  provenance prevents an estimate being presented as an observation.
- Postcode does not affect the RC1 comparison calculation. Its durable storage
  is therefore a specific minimisation question for the owner: retain it only
  while a documented operational/evaluation purpose remains necessary, or
  remove it earlier. Garage matching can collect postcode in its separate,
  consented form.
- Unsupported component and repair fields are omitted rather than inferred.
- Application access logging is disabled; remaining log correlation uses a
  keyed digest and safe fixed fields.

Less intrusive controls adopted by RC1 include POST-body transport, stripping
legacy sensitive query keys, no postcode in responses, analytics suppression on
saved-report routes, fixed analytics event fields, 90-day report expiry,
24-month check-record pseudonymisation, and rights-based earlier deletion.

## 4. Balancing test

- **Nature of data:** VRN and postcode can indirectly identify or locate an
  individual and are personal data in context. The saved report token grants
  access to vehicle-linked information. The data is not special-category data,
  but it is not anonymous or harmless.
- **Expectations:** a user can reasonably expect a report request and an
  explicitly offered share link to be processed. Longer operational/evaluation
  retention is acceptable only if the deployed notice is prominent and
  accurate and objection/deletion is practical.
- **Impact:** misuse or leakage could reveal vehicle/location-linked
  information. The comparison is informational and has no legal or similarly
  significant effect, but false certainty could still influence spending or
  safety decisions; product copy therefore limits specificity.
- **Safeguards:** least-specific supported evidence, no diagnosis, typed
  degradation, bearer-route analytics suppression, log scrubbing, restricted
  database/secret access, 90-day report expiry, 24-month plaintext removal,
  12-month lead deletion, and access/erasure/objection handling.
- **Disclosure:** garage data is shared only after explicit consent. Check
  records are not sold or shared for another controller's purpose under this
  design.

**Provisional balance:** legitimate interests can support P1 and proportionate
P3 if the safeguards operate in production. P2 remains conditional on a
separately documented lawful outcome source/linkage implementation. Continued
postcode retention needs explicit owner necessity approval.

## 5. Retention and rights

Check records are retained in identifiable form for no more than 24 calendar
months under the published policy, subject to earlier rights requests and
incidents. `scripts/retention_sweep.py` then:

1. creates a keyed HMAC-SHA256 of the normalised VRN where one exists;
2. nulls registration, postcode, report payload, and report token; and
3. stamps `pseudonymised_at` and verifies that no plaintext/payload/token
   remains on processed rows.

The remaining row and HMAC are pseudonymised, not anonymous, and remain within
the data-protection boundary. Leads and dependent assignments are deleted after
12 calendar months by `scripts/lead_retention_sweep.py`.

Users can request access, rectification, erasure, restriction, portability
where applicable, or object to legitimate-interest processing. Requests use
the contact route in the deployed privacy notice and should be answered within
the applicable statutory period. Do not place identifiers in ordinary tickets
or logs while locating a record.

## 6. Earlier-notice backlog

Records created before the corrected notice effective time require an
owner-approved cutoff and the dry-run-first
`migrations/pseudonymize_backlog.py` process. Execution against production is
not established by the existence of the script; record the cutoff, counts,
operator, verification, and incident-safe evidence separately.

## 7. Operational conditions

This assessment is conditional on:

- successful migration and nullable identifier fields;
- strong, separately controlled HMAC secrets;
- scheduled/monitored retention jobs with verified zero-stale results;
- Railway/processor log and analytics inspection with synthetic values;
- processor agreements, regions, and international-transfer safeguards
  matching the notice;
- a documented rights and bearer-token incident path; and
- owner review of why postcode remains in check records.

## 8. Review triggers

Review before any new outcome-linkage pipeline, new data source, new purpose,
new processor or recipient, material model/cohort change, use of postcode in
scoring, retention change, DVSA/licensing guidance change, security incident,
or by 2027-07-11, whichever occurs first.
