# AutoSafe 90-day acquisition execution

Programme window: **7 October 2026–4 January 2027**, inclusive. Paid delivery
starts no earlier than 14 October, after measurement acceptance. Campaign limits and cash accounting are recorded in the private execution packet. No further media, new subscriptions or specialist purchases are authorised. Owner review precedes exploratory release/launch as requested
in the supplied plan. Campaign specifications are prepared locally; no account
campaign creation or spend is claimed by this implementation.

## Candidate implementation

The organic `oa-journey-30m-v2` parser, source classifier and metric remain
unchanged. The separate `/api/acquisition/paid-events` route accepts only
`paid-journey-30m-v1`, four fixed campaign groups and an explicit boolean
consent assertion. The shared storage/atomic retention infrastructure partitions
all retained counts by metric version. Paid records cannot enter organic
reporting. No new direct identifier or raw text field is accepted.

`static/paid-acquisition.js` is **disabled by default** in this candidate.
Production activation requires its exact-release acceptance and a checked
follow-up change. The endpoint additionally requires
`PAID_ACQUISITION_ENABLED=true` and the existing ingest flag. Collection
starts only after the separate paid-measurement permission; accepting Google
Ads cookies alone does not grant it. GPC and the existing objection override it.
Identifiers expire after 30 minutes, raw events normally disappear within about
35 minutes, and aggregates remain for three calendar months. Provider backup/log
limits remain those of the existing reviewed hosting arrangement. Consent
assertions and user-agent classification are browser reports, not fraud-proof
proof of identity or legal sufficiency.

Arrival/page-view payloads contain schema/metric version, random event/arrival/
document UUIDs, coarse start minute, fixed source/page/campaign categories,
`observation_state=observed`, and `consent_granted=true`. Check/result payloads
reuse the strict existing result-acknowledgement contract. Do not send raw URLs,
click IDs, registrations, report tokens, advisory text or numeric vehicle values.
The server validates the paid envelope independently before reusing that
contract; the consent assertion is discarded before persistence.

A useful arrival requires its `check_started` plus a supported delivered render
for the same random operation; a render failure cancels that operation. A second
successful operation may still qualify the arrival once. Restored reports,
unsupported reference results and report creation alone do not qualify.
Page views count only when an observed non-bot arrival receipt exists; missing
receipts are unknown. SPA/page navigation is measured, not every browser
visibility/background transition. The historical Google Ads Risk Check remains
a distinct creation event; do not use it as this programme's quality numerator.

Read aggregate counts with `scripts/paid_acquisition_metric.py --from YYYY-MM-DD
--to YYYY-MM-DD` (completed UTC days, end exclusive), through the authorised
server environment. Never print credentials, random IDs or raw events. Keep
platform clicks separate from consented arrivals. Reconcile discrepancies
without forcing equality. `screen()` implements the plan's operational defaults,
not significance: zero useful after 50 arrivals stops for measurement
investigation; 100 arrivals plus 14 days and at least 10% useful qualifies only
for a separately approved second batch. No automatic scaling.

## Frozen organic experiment

`seo-pairs-frozen.json` is copied from the pre-edit baseline freeze. Ten paired
existing model pages were checked HTTP 200 before editing. Seed 20261007 assigns
one treatment per pair. Matching uses model/market segment and is imperfect:
exposure and age mixes differ. All 20 candidates have been checked; only the
ten frozen treatments render `programme_model.html`. Controls retain their
main content; shared measurement/privacy shell changes are disclosed.

The explorer uses summed recorded failures/tests for an exact age/mileage group
with at least 100 tests, rejects incomplete/invalid groups and never substitutes
a broader cohort. It compares historical group rates with the whole model group,
not an individual prediction. The expandable source table works without JS.
Treatment template revisions advance their sitemap lastmod; routing/measurement
changes do not advance control main-content dates.

The private account baseline exports contain a gap between the property chart and page table. These reporting views do not reconcile; do not fill unreported page rows with zero.
Index status is not individually established by HTTP 200 or sitemap membership.
Report raw UK clicks, pair comparison estimates and indexing separately, with
missing-data bounds and uncertainty. Review the full 90-day programme window,
while recording each actual release date and exposure duration.

## Production acceptance before paid delivery

1. Pass local tests and all required PR checks, including image/Postgres staging
   evidence. Review code, privacy notice, treatment preview and campaign assets.
2. Deploy with paid client/endpoint disabled. Bind public `/api/version` to the
   exact backend/frontend SHA and successful Railway service/domain deployment.
3. Verify the existing tables and retention without exporting individual rows.
   Test the paid route in disposable Postgres first. Do not use future-clock
   retention against production or contaminate real campaign groups with fake
   paid arrivals. Keep production synthetic acceptance counts separately excluded
   by an explicitly reviewed testing method before any paid activation.
4. Check consent, GPC, duplicate retry, fixed expiry, public-to-report navigation,
   successful/failed render and mobile layout against the exact candidate.
   Confirm that raw-event deletion works in real elapsed time and verify rollback.
5. Activate the paid client in a checked follow-up release, then verify native
   owner-approved campaign-total budgets and dates in each new Google Ads campaign. Start
   only after acceptance, no earlier than Day 8. Never substitute a loose daily
   budget for a total spending cap. Retain the existing paused Performance Max.

Rollback: unset `PAID_ACQUISITION_ENABLED`, restore paid `ENABLED=false`, keep
shared retention running and preserve organic collection. No production table
is dropped. Record gaps as unknown.

## Calendar and follow-through

- 7–13 October: measurement implementation/acceptance; campaign specifications;
  frozen organic baseline and ten treatment changes ready for owner review.
- 14 October–3 November: funded Discover/Search batch and organic observation.
  Planned first delivery period is 14–27 October; shift both together if gates
  are late without extending the accounting window or spending cap.
- 4 November–5 December: choose the personalised checklist from actual owner
  query demand, then ten sourced advisory explanations; prepare reviewable
  changes before publishing. Reuse existing recorded report evidence and never
  infer a current defect from a history/advisory or aggregate group.
- 6 December–4 January: confirm qualifying findings in a second batch only if
  further spend is authorised; three demonstrations and separate YouTube test
  only after a tool passes the engagement screen.

No outcome can be declared before observations exist. Hold/inconclusive is a
valid decision. Existing cancelled pilot/automation is not restarted.
