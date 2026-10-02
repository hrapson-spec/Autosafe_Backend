# OA-002 public-claims decision

Decision date: 2026-10-02. This is a product-copy correction within the
authorised organic-acquisition goal. It does not approve a new model, change
production inference settings, or establish predictive qualification.

## Decisions

| Claim | Decision and evidence boundary |
|---|---|
| CL-001 product modes | Use “MOT Records & Evidence” consistently in initial HTML and React metadata. Promise recorded information and labelled evidence, with availability stated. The existing `vehicle_prediction` path is described as a **model estimate**; comparisons remain historical group rates. Remove the unsupported “1 in 4 cars” headline statistic. Retain the previously owner-approved brand slogan. |
| CL-002 age rates | Withdraw conflicting age percentages and explanations about owner behaviour/survivorship from both public guide implementations. Do not select the alternative 39.78% figure merely because another page contains it. Publication of age rates needs compatible populations, periods, numerator, denominator and rounding evidence. |
| CL-003 windscreen damage | Replace automatic-failure claims in static and React checklists. Explain that location, size and effect on the driver's view matter, with a direct link to the current DVSA manual. |
| CL-004 source coverage | Retain only the reproducible dataset total and overall ratio as dataset facts: 148,509,908 tests; 39,969,903 failures; 26.9% rounded to one decimal. Source-record coverage dates are not established here. These are tests, not unique cars, and are not current annual or UK-wide rates. Withdraw unreviewed component/category percentages from the reviewed failure guides and checklist. |

The failing-test ratio is `sum(Total_Failures) / sum(Total_Tests)` in
`prod_data_clean.csv.gz`, checked by `scripts/claim_sweep.py`. This is not an
unweighted average of row percentages. Component categories can overlap in
one test; percentages of tests and percentages of failures are different
denominators. Withheld age/category rates have not been recomputed or promoted
by this correction. An artifact revision date is not a record coverage date.

## Current result matrix

| Runtime state | What it means | Acquisition acknowledgement |
|---|---|---|
| `vehicle_prediction` / `model_v55` / `model_prediction` | Existing model estimate based on recorded history; no cohort counts are implied | `prediction` can be supported when finite, valid and actually rendered; this does not establish model accuracy or calibration |
| `comparison` / `exact_band` | Recorded outcomes for the matching model/age/mileage group | `exact_comparison` requires usable counts, valid rate and displayed scope |
| `comparison` / `age_band_only` or `model_average` | A broader recorded comparison | `broader_supported_comparison` has the same evidence checks |
| `population_default` | Dataset-wide reference; no vehicle-matched result | `dataset_reference`, never a supported result |
| `unavailable` | Required comparison evidence is unavailable; any reference number is labelled as a reference | `unavailable`, never a supported result |
| Demo data | Demonstration state, not an observed vehicle result | Never supported; an unavailable scope retains unavailable precedence |
| Missing/expired share or render failure | No completed supported result | Fixed unavailable reason or `render_failed`; no completion claim |

The current `report_routes.py` attempts `prediction_service.build_v55_assessment`
and falls back on its typed `PredictionUnavailable` conditions. The prediction
setting is read at call time and defaults to enabled. A read-only production
configuration check on 2026-10-02 found `PREDICTIONS_ENABLED` unset, so that source
default applies. This establishes configuration and code paths, not successful
inference for every vehicle. Existing July integration documents explicitly
excluded revalidation of V55 statistical performance. This copy review makes
no new accuracy, calibration, uplift or prospective-validation claim and does
not access protected research evidence.

Result text now says “model estimate”, “historical group comparison” or
“dataset reference”. An unavailable match has an explicit unavailable-evidence
sentence. The frequency helper also had an edge bug: estimates too high to
round to a small fraction fell through to “fewer than 1 in 100”. It now uses
percentage-sized fractions or an appropriate high-end bound. Exactly 50% no
longer says failure is more likely. Boundary tests cover these distinctions.

## Source review

- [DVSA inspection manual](https://www.gov.uk/guidance/mot-inspection-manual-for-private-passenger-and-light-commercial-vehicles), last updated 1 June 2026; checked 2 October 2026. Its scope is Great Britain, with separate Northern Ireland guidance.
- [DVSA visibility rules](https://www.gov.uk/guidance/mot-inspection-manual-for-private-passenger-and-light-commercial-vehicles/3-visibility), section 3.2. Size is part of assessment; the effect on the view of the road is required for the damage failure judgement.
- [GOV.UK retest rules](https://www.gov.uk/getting-an-mot/retests), checked 2 October 2026. Corrected the checklist's blanket claim that returning after external repairs within ten working days guarantees a free retest; the fee depends on the applicable case.

Static FAQs and their visible answers were changed together. The React guide
no longer publishes different percentages. The homepage title, description
and social metadata agree before and after hydration. Significant content
changes are recorded in `page_revisions.json` with 2026-10-02 dates.

## What release evidence does and does not establish

- A post-commit render acknowledgement means the app mounted its final result
  and passed the registered checks. It does not prove that a person read it,
  that the model is accurate, or that an enquiry was qualified.
- Report routes carry `noindex, nofollow`, `no-store` and `no-referrer` controls.
  They remain bearer-token URLs. These headers are not authentication and do
  not guarantee removal from every crawler or the absence of infrastructure
  logs. Search-engine exclusion depends on crawler access to the directive.
- Seven named GitHub Actions checks are required on main, including for
  administrators, and Railway waits for check suites. A failed candidate was
  rejected by branch policy and a subsequent failed main run was skipped by
  Railway. A previous green PR run is not a green main run or deployed state.
- Local benchmarks, repeated production HTTP requests, browser rendering and
  controlled cold-start measurements are different observations. Report their
  environment, sample size and units; do not infer production LCP from local
  timings or claim a controlled performance uplift from a few HTTP requests.
- The acquisition collector remains disabled until its separate privacy,
  aggregation and production acceptance requirements have been met. A merged
  acknowledgement does not mean acquisition measurement is collecting.

This file records source and wording decisions. Passing CI and deployed-version
verification are recorded separately in the release receipt, not inferred
from the existence of this document.
