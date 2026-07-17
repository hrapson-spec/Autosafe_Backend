# V55 Customer Report Integration

## Context

AutoSafe currently deploys and loads the V55 CatBoost model, but the public browser journey does not call it. The website submits `POST /api/v2/reports`; that route resolves DVSA vehicle history and then obtains the displayed percentage from the PostgreSQL/SQLite comparison ladder. V55 is isolated behind the legacy `GET /api/risk/v55` endpoint.

The prediction-first report UI already has an explicit semantic contract:

- `vehicle_prediction` is valid only with `prediction_source: model_v55`.
- Every existing or degraded report remains `comparison`.

This change connects V55 to new v2 report creation without relabelling comparison data or mutating previously saved reports.

## Goals

1. New public checks use V55 when model inference succeeds.
2. Successful inference produces a persisted, shareable `vehicle_prediction` report.
3. If V55 cannot run, the same request returns the existing clearly labelled comparison fallback.
4. Every customer-facing downstream surface describes the result consistently: report, WhatsApp sharing, emailed report and garage enquiry.
5. Existing saved comparison reports continue to replay unchanged.

## Non-goals

- Retraining, replacing or revalidating V55's statistical performance.
- Recalculating or rewriting previously saved reports.
- Removing the comparison evidence ladder.
- Making a diagnosis, pass guarantee or claim that a component fault exists.
- Changing the existing report visual design.

## Considered Approaches

### 1. Integrate inference inside `POST /api/v2/reports` — selected

The report route reuses the DVSA history it already fetched, attempts V55 inference, maps a successful output into `ReportResponse`, and persists the exact response. Expected prediction failures fall back to the comparison builder.

This creates one request, one persistence operation and one source of report truth.

### 2. Make the browser call `/api/risk/v55` before creating a report — rejected

This would require two browser requests and would duplicate DVSA access. The prediction and saved report could disagree, and failure/idempotency handling would be split between clients.

### 3. Make the report route issue an internal HTTP request to `/api/risk/v55` — rejected

This would reuse a legacy wire shape but introduce a self-request, duplicate rate limiting and logging, and preserve an endpoint contract that does not match `ReportResponse`.

## Architecture

### Prediction boundary

Add a small `prediction_service.py` boundary responsible for turning resolved vehicle identity and DVSA history into a typed V55 assessment. It will:

1. Confirm the prediction kill-switch is enabled and the model is loaded.
2. Build V55 features from the already-resolved `VehicleHistory` and optional postcode.
3. Call `model_v55.predict_risk` exactly once.
4. Validate that the calibrated probability is finite and within `[0, 1]`.
5. Map a complete, finite seven-component vector into report priorities; otherwise suppress components and the repair estimate without discarding an otherwise valid prediction.
6. Map the result into the existing report-domain types.

Shared vehicle, MOT and mileage mapping will be extracted from `report_service.py` into a small reusable vehicle-context helper. The comparison assessment and prediction assessment will use the same mapping, avoiding duplicated date and odometer semantics.

`prediction_service.py` will expose typed operational failures such as prediction disabled, model unavailable, feature-engineering failure and inference failure. The route will catch only those expected failures. Contract/programming errors will not be silently hidden by a comparison fallback.

### Report orchestration

`POST /api/v2/reports` keeps its existing normalization, idempotency, DVSA lookup, token minting and persistence ordering.

After resolving vehicle identity and history:

1. Attempt `prediction_service.build_v55_assessment(...)`.
2. On success, build a `vehicle_prediction` report.
3. On a typed prediction failure, log a privacy-safe reason and call the existing `report_service.build_assessment(...)` comparison path.
4. Persist the exact response returned to the browser.

No second DVSA request and no internal HTTP request are permitted.

## Contract Semantics

### Successful V55 prediction

- `result_kind: vehicle_prediction`
- `prediction_source: model_v55`
- `evidence.match_scope: model_prediction`
- `evidence.age_band: null`
- `evidence.mileage_band: null`
- `evidence.total_tests: null`
- `evidence.total_failures: null`
- `risk.failure_risk`: V55's calibrated probability
- `risk.confidence`: V55's existing confidence classification
- `components`: the seven V55 component priorities when complete and valid; otherwise unavailable
- `repair_estimate`: derived from the same predicted probability/components when supported
- `note: null`

`model_prediction` is added to the Python and TypeScript `MatchScope` unions. It exists only for `vehicle_prediction`; comparison reports may not use it. Conversely, a prediction may not claim an exact, age-band or model-average cohort.

### Comparison fallback

The current report assessment remains unchanged:

- `result_kind: comparison`
- `prediction_source: postgres`, `sqlite` or `dataset_reference`
- the current evidence-ladder `match_scope`
- existing comparison wording and universal checks

The fallback is returned when prediction is disabled, the model is unavailable, feature engineering fails or inference fails. A fallback is not presented as a degraded prediction; it is explicitly a comparison.

### Persistence

The stored payload remains the canonical replay object. Prediction rows use:

- `model_version: v55`
- `prediction_source: model_v55`
- `match_scope: model_prediction`
- the same report token, expiry and idempotency rules as comparisons

Comparison rows continue to use `model_version: lookup_v2`.

Previously stored payloads are not recalculated. The current production release already understands both result kinds, so rolling back the integration preserves readability of prediction reports created by this release.

## Customer-facing Behaviour

### Main report

The existing prediction UI activates automatically:

- `AutoSafe prediction for <registration>`
- `Your car's predicted chance of failing its next MOT`
- predicted percentage and plain-language frequency
- up to three model component priorities under `Check these first`

The methodology disclosure for a prediction says that AutoSafe used the vehicle's recorded MOT history and details with its prediction model. It does not show cohort sample sizes or describe the result as a recorded failure rate.

### Sharing and email

WhatsApp and emailed-report copy branch on `result_kind`:

- predictions say `AutoSafe predicts ... for <registration>` and retain a clear non-guarantee;
- comparisons retain the existing comparable-vehicle wording.

### Garage enquiry

Prediction reports send up to three model priorities labelled as inspection priorities, not diagnosed faults. Comparison reports retain comparison-pattern wording. Component percentages remain absent from the primary report and garage request summary.

### Public and privacy copy

Homepage metadata, explanatory copy, privacy notice and terms must describe both states accurately:

- AutoSafe can estimate next-MOT failure risk from a vehicle's recorded history when V55 runs.
- It falls back to a labelled historical comparison when prediction is unavailable.
- Neither state is a diagnosis, guarantee or substitute for an MOT inspection.

## Error Handling and Observability

- Do not expose model exceptions or feature values to customers.
- Log prediction attempt outcome, model version and a privacy-safe hashed VRM/correlation ID.
- Never log raw registration, postcode, report token, feature vector or prediction payload.
- Keep the existing model kill-switch.
- A prediction failure must not prevent the comparison fallback from being saved and shared.
- Existing health checks continue to report model load state; no new unauthenticated diagnostics are added.

## Testing

Implementation follows test-driven development.

### Backend

1. A model-success route test proves feature engineering and `predict_risk` are each called once.
2. The success response and persisted payload are byte-identical and contain `vehicle_prediction`, `model_v55`, `model_prediction` and `model_version: v55`.
3. Kill-switch, model-unavailable, feature failure and inference failure tests each prove the comparison fallback.
4. Prediction contract tests reject invalid source/scope pairings and non-finite overall probabilities; incomplete/invalid component data is suppressed without relabelling or discarding a valid overall prediction.
5. Existing comparison, idempotency, persistence-degraded and saved-report replay tests remain green.
6. OpenAPI drift contains only the intended additive scope and prediction-path changes.

### Frontend and downstream surfaces

1. Prediction and fallback fixtures pass runtime validation.
2. Prediction report, methodology, WhatsApp, email and garage wording contain no comparison claims.
3. Comparison surfaces contain no vehicle-prediction claims.
4. Existing reminder, sharing, email and garage interactions remain functional.
5. Desktop and mobile E2E journeys cover both result states without overflow.
6. Claim sweep, privacy-copy and legal-copy tests enforce the new conditional claim boundary.

## Release and Live Verification

1. Run the complete backend, frontend, typecheck, lint, build, OpenAPI, claim-sweep and E2E suites.
2. Require all seven GitHub checks on the exact feature SHA.
3. Merge only under the user's existing production authorization and wait for both Railway deployments to succeed.
4. Confirm `/health`, `/ready` and `/api/version` on the merged SHA.
5. Create one new report for the user's test vehicle through the real website.
6. Confirm its API payload contains `vehicle_prediction`, `model_v55` and `model_prediction`.
7. Verify the live desktop/mobile report and downstream prediction wording.
8. Confirm an existing saved comparison report still replays as `comparison`.

## Success Criteria

- The public website uses V55 for new reports when inference succeeds.
- A successful V55 result is visibly and contractually a prediction about that vehicle.
- Prediction failure produces the agreed honest comparison fallback.
- Saved reports, shares, emails and garage enquiries preserve the same semantics.
- No existing comparison is relabelled or rewritten.
- Production identity, two Railway deployments and a newly generated live prediction report are verified directly.
