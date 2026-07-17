# Prediction-First Vehicle Report

## Context

The approved AutoSafe result design leads with one answer, uses the existing slate/white/green visual system, removes the standalone evidence card, and puts the next action beside the result. The current Release 1 API does not yet produce a vehicle-level prediction: `exact_band`, `age_band_only`, and `model_average` are all comparison cohorts, while `prediction_source` identifies a backing store rather than a predictive model.

The production UI must therefore support the approved vehicle-prediction design without presenting today’s comparison data as that prediction.

## Decision

Add an explicit result-semantic discriminator to the v2 report contract:

- `comparison`: the safe default for every current and previously persisted v2 report.
- `vehicle_prediction`: reserved for a report produced by an explicitly identified vehicle model path.

Add `model_v55` as the only prediction source that may accompany `vehicle_prediction`. The contract must reject a vehicle-prediction result paired with `postgres`, `sqlite`, `dataset_reference`, or `unavailable`. No existing browser path will emit `vehicle_prediction` in this release.

## User Experience

### Vehicle prediction

When `result_kind` is `vehicle_prediction`, the report shows:

- `AutoSafe prediction for <registration>`.
- The dominant percentage and `Your car’s predicted chance of failing its next MOT`.
- A plain-language frequency such as `about 1 in 4`.
- Up to three model-provided component priorities under `Check these first`.
- A dark next-step panel leading to the existing ten-minute MOT checklist.
- MOT due-date and reminder actions.

### Comparison fallback

When `result_kind` is `comparison`, the report shows:

- `This result isn’t a prediction for <registration>`.
- `<Make> <Model> comparison: about 1 in 4 failed their MOT` when a vehicle-matched cohort exists.
- Dataset-wide reference wording for `population_default` and `unavailable`; those states must never imply make/model evidence.
- The same prominent percentage and progress treatment, clearly labelled as a comparison.
- A universal pre-MOT checklist rather than cohort component rates relabelled as this car’s priorities.
- The same checklist, MOT reminder, email and garage actions.

Detailed scope and sample information may remain available behind `How this result was calculated`; it must not compete with the primary result.

The frequency helper must remain finite at zero risk, and malformed or impossible MOT dates must fall back to generic preparation copy rather than rendering or normalising a false date.

## Visual System

Use AutoSafe’s existing production tokens and components:

- Canvas: `slate-50`.
- Cards: white, `rounded-2xl`, `border-slate-100`, `shadow-sm`.
- Primary text and action surface: `slate-900`.
- Supporting text: `slate-600`.
- Low percentage treatment: `green-600`; medium: `amber-600`; high: `red-600`.
- Body type: the existing Inter stack.
- Primary result card plus a narrower checks card on desktop; one column on mobile.
- One dark, full-width action panel below the result.

The risk percentage is the signature element. Everything else stays quiet and functional.

## Existing Functionality

Keep working:

- Check another vehicle.
- WhatsApp share and copy-link actions when a share URL is available.
- MOT reminder capture.
- Email report delivery.
- Garage finder modal and lead submission.
- Mobile sticky action.
- Report-view and conversion analytics.

Retire the `results_page_v1` control/treatment layout split. The approved result layout becomes the only report design, and stale local experiment assignments must no longer be attached to new submissions.

## Backwards Compatibility

- `ReportResponse.result_kind` defaults to `comparison`, so old stored 2.0 payloads continue to validate and replay truthfully.
- The frontend requires the field from the current backend response; the backend’s default ensures it is present after model validation.
- The API remains contract version `2.0` because the field is additive and existing clients already tolerate unknown response keys.
- No current report changes its risk calculation, evidence ladder, persistence, share URL, or component data.
- Prediction-specific share, email and garage-lead semantics remain an activation gate for the future model path; no current browser/API path emits `vehicle_prediction` in this release.

## Testing

Test-first coverage must prove:

1. Old payloads default to `comparison`.
2. Current report creation, legacy stored GET, and legacy idempotent replay emit `comparison`.
3. `vehicle_prediction` is rejected unless `prediction_source` is `model_v55`.
4. The frontend validator accepts both valid result states and rejects invalid pairings.
5. The comparison UI contains no vehicle-prediction claim and no standalone Evidence Quality card.
6. The vehicle-prediction fixture renders the approved prediction copy and priority checks.
7. Reminder, share, email and garage actions still work.
8. Desktop and mobile layouts render without overflow.
9. Claim sweep, typecheck, build, Vitest, backend contract tests and Playwright all pass.
10. The committed OpenAPI snapshot exposes only the additive result-kind and model-source changes and matches the pinned production dependency set.

## Release

Base the work on production `main@c5f83fdf58ad81344ecfaa83a0b1e857ad17ea1f`. Publish through a pull request, require the seven existing CI jobs to pass on the exact head SHA, then merge to `main` under the user’s explicit production approval. Railway will auto-deploy both production services.

Do not call the release complete until both deployments succeed, `/health` and `/ready` return 200, the live report renders the new comparison fallback, and `/api/version` has been checked. The pre-existing backend/frontend SHA mismatch must be reported separately if it persists.

## Success Criteria

- Today’s live report is simpler and visually matches the approved mockup while remaining a comparison.
- A future `vehicle_prediction` payload activates the approved prediction state without another UI redesign.
- Evidence methodology is secondary, not the headline experience.
- All existing report actions still function.
- The production deployment and rendered public page are verified directly.
