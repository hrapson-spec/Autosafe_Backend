# Legitimate Interests Assessment — vehicle check records (`risk_checks`)

**Controller:** AutoSafe (sole trader — Henri Rapson) · **Date:** 2026-07-03
**Owner approval:** decision D1, 2026-07-03 (privacy Option B) — see
`work/reviews/REMEDIATION_PLAN_2026-07-03.md` §6.
**Trigger:** remediation of the notice/practice contradiction identified in
`work/frontier_engine/memos/LICENSING_PRIVACY_REVIEW_C1.md` §1 (HIGH).

## 1. The processing

When a user runs a vehicle check we store a check record: VRN and postcode as
entered, vehicle attributes and MOT history returned by the DVSA MOT History
API at that time, the risk assessment we generated, model version, and
referral metadata. VRM is personal data (indirectly identifies the keeper —
ICO ANPR guidance). Postcode compounds identifiability.

Purposes:
- **P1 Service operation & quality** — support queries, abuse prevention,
  fault diagnosis.
- **P2 Model accuracy measurement & improvement** — comparing the prediction
  we served against the vehicle's subsequently published MOT outcome
  (DVSA anonymised open data, matched by test-date/odometer fingerprint of
  the history we already hold — no additional DVSA API queries), measuring
  calibration of served predictions, and improving the model.
- **P3 Service-quality monitoring** — aggregate funnel and reliability
  metrics.

## 2. Necessity test

- P2 cannot be achieved without retaining the served prediction together with
  enough vehicle history to identify the same vehicle's next test outcome.
  Aggregated or immediately-anonymised data would break the prediction↔
  outcome link that accuracy measurement requires.
- Retention is bounded by the purpose: a vehicle's next MOT arrives within
  ~13 months of a check; 24 months covers one full outcome cycle plus
  publication lag. Beyond that the plaintext VRN/postcode serve no purpose →
  deleted or irreversibly pseudonymised (HMAC-SHA256 with a key held outside
  the database).
- Less intrusive alternative considered (Option A — store nothing): rejected
  because it makes served-prediction accuracy unmeasurable, i.e. the service
  could never verify or honestly represent its own quality.

## 3. Balancing test

- **Nature of data:** vehicle/registration data; not special category; low
  sensitivity; postcode adds locational granularity but no address.
- **Reasonable expectations:** users ask for a vehicle risk assessment; that
  the service keeps the check it produced, and checks its own accuracy, is
  within reasonable expectations **provided it is disclosed** — the privacy
  notice (updated 2026-07-03) now states it plainly. The prior notice said
  the opposite; the backlog collected under it is remediated by immediate
  pseudonymisation (see §5).
- **Impact on individuals:** minimal — no marketing use, no sharing (leads
  are separate, consent-based), no automated decision with legal effect
  (predictions are informational only, stated in the notice).
- **Safeguards:** 24-month cap with scheduled purge; HMAC pseudonymisation
  for analytics joins; access limited to the controller; encrypted-at-rest
  hosting (Railway PostgreSQL); admin export requires API key; objection →
  erasure/irreversible pseudonymisation, honoured within one month.
- **Outcome-linkage specifics (capture-USE gate 2.5):** matching a check
  record to the vehicle's later result in DVSA's *anonymised* open data is a
  linkage of data we already lawfully hold to a public dataset about the same
  vehicle. It does not identify any new individual, contact anyone, or
  enrich the record with third-party personal data; the output is a
  prediction-accuracy label. Assessed as within the same legitimate interest
  and expectations envelope as P2.

**Balance: legitimate interests upheld**, conditional on the safeguards above
remaining in force and the notice remaining accurate.

## 4. Rights and transparency

Notice updated (both copies, 2026-07-03): storage disclosed, retention
stated, objection route (autosafehq@gmail.com), ICO complaint route. Rights
honoured: access/rectification/erasure/restriction/portability/objection.
Objection handling: erase or irreversibly pseudonymise the individual's check
records.

## 5. Backlog remediation

All `risk_checks` rows created before the corrected notice went live were
collected under an inaccurate transparency statement. Remediation (owner
ruling D1): immediately pseudonymise the backlog — replace plaintext VRN with
its HMAC and null the postcode — rather than waiting for the 24-month
horizon. Implemented in `migrations/pseudonymize_backlog.py` (task 2.2);
execution against production is pending an owner-supplied `--before` cutoff
(the moment the corrected notice went live) and owner-run. This LIA's P2
purpose continues on the pseudonymised backlog (fingerprint linkage does
not require the plaintext VRN).

## 6. Review

Review this LIA on: any new purpose for check records, any sharing of check
records, any DVSA guidance change on re-query/acceptable use, or 2027-07-03,
whichever is first.
