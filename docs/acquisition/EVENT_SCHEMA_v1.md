# Acquisition event schema v1 (PROPOSED)

Status: **proposed, collection OFF.** This documents the typed, safe-field events that
`utils/acquisitionEvents.ts` can build (ticket OA-004). Nothing in this branch sends
them anywhere: the default sink is a no-op, `ACQUISITION_COLLECTOR_ENABLED` is `false`,
and there is no fetch / sendBeacon / XHR transport in the code.

Authoritative semantics: `product/commercial/organic-acquisition/MEASUREMENT.md`
(frozen source/API contract 2.0 at `7325720`). Machine-readable schema:
[`event_schema_v1.json`](event_schema_v1.json) (JSON Schema 2020-12,
`additionalProperties: false`, enums only).

## Not decided here (awaits OA-005)

The following are **not approved** and nothing in this schema or branch decides them:

- the collector endpoint (`POST /api/acquisition/events` is a proposal in MEASUREMENT.md);
- storage, processors, retention and access;
- session identifiers and session persistence (memory-only vs short-lived first-party
  storage); `session_id`, `release_sha` and `received_at` are collector-side/OA-005
  fields and are deliberately absent from the client event types in this branch;
- purpose, legal basis / consent analysis, and notice;
- the final `metric_version` value (the code carries the placeholder
  `oa-metric-v1-draft`), the landing-path allowlist, and the source-group taxonomy
  rules.

Pseudonymous operation and event identifiers may still be personal data; their handling
is subject to the OA-005 decision.

## What an event does and does not establish

An event is a **client-reported acknowledgement**. `result_rendered` says the app
believes its final result view mounted and that locally computed validity flags held.
It proves neither the accuracy of the figure shown, nor comprehension or benefit, nor
that the model is qualified. The server cannot verify rendering, rates or sample counts
from these flags. Qualification remains independent. Created reports and the existing
third-party `risk_check` conversion are not displayed completions.

## Safe-field rule

Every field value is an enum, a boolean, a constant, a schema-shaped path
(`landing_observed` only), or a random identifier. Events never carry: the headline
risk or any rate, any sample/failure count, a report token or id, a registration,
postcode, make or model, a URL or referrer, an error message, or free text.
`utils/acquisitionEvents.test.ts` serialises every event type and fails if a forbidden
key or a fixture token/registration value appears.

## Events

Common fields on every event: `schema_version` (1), `metric_version`, `event_id`
(random UUID per emitted event; the collector's idempotency key), `event`.
`operation_id` is a random UUID minted per deliberate check in `App.tsx`, never derived
from any input, carried to the report route in navigation state, and absent for
restored/shared links.

| Event | Emitted when | Event-specific fields |
|---|---|---|
| `landing_observed` | **Defined, not emitted in this branch** (needs the approved session design) | `landing_path`, `source_group`, `observation_state` |
| `check_started` | `App.tsx`: accepted submission, before the API call; once per logical operation (a retry of the same unresolved operation keeps its id and does not re-announce) | `operation_id`, `entry_mode=fresh_check` |
| `report_created` | `App.tsx`: validated `createReport` success, before navigation | `operation_id`, `entry_mode`, `result_kind`, `match_scope`, `persistence_mode` |
| `result_rendered` | `ReportScreen`, from `ReportDashboard`'s post-commit effect, only when the final non-loading view is mounted, the runtime boundary is clear, and `classifyResult` reports a delivered state other than unavailable | `entry_mode`, `persistence_mode`, `render_delivered` (always true here), `supported_result`, `outcome_group`, `rate_valid`, `sample_nonzero` (omitted for `model_prediction`), `scope_visible`, `result_kind`, `match_scope`, `operation_id` (when a deliberate check) |
| `result_unavailable` | `ReportUnavailable` committed from `ReportScreen`'s own unavailable branch, or a fully degraded report (`match_scope=unavailable`) committed in the dashboard | `entry_mode`, `reason` in `not_found, expired, unavailable, error`, `operation_id` (when a deliberate check) |
| `check_failed` | `App.tsx`: `createReport` failed | `operation_id`, `entry_mode`, `error_category` (fixed enum), `stage=create_report` |
| `render_failed` | `ResultErrorBoundary` (rejected chunk -> `lazy_load`, render throw -> `render`), or the final view mounted with a report that fails the contract/numeric checks (`contract_invalid`) | `entry_mode`, `stage`, `operation_id` (when a deliberate check) |

`entry_mode`: `fresh_check` (the report route carried an operation id from this tab) or
`restored_link` (no navigation state; no `check_started`/`report_created` exists for it
in this session). `persistence_mode`: `saved` (token route) or `inline_unsaved` (the
`/app/report/unsaved` inline route); inline unsaved reports that validly render are
supported results, and unavailable sharing is not a render failure.

An error-boundary fallback is rendered without the unavailable-view callback, so a
fallback emits `render_failed` only, never `result_unavailable` or `result_rendered`.

## Result classification (`utils/resultAcknowledgement.ts`)

| Client state | `render_delivered` | `supported_result` | `outcome_group` |
|---|---|---|---|
| Valid `vehicle_prediction` / `model_prediction` | true | true | `prediction` |
| Valid `comparison` / `exact_band` | true | true | `exact_comparison` |
| Valid `comparison` / `age_band_only` or `model_average` | true | true | `broader_supported_comparison` |
| `population_default` reference | true | false | `dataset_reference` |
| `unavailable` scope | true | false | `unavailable` |
| Contract-invalid combination, non-finite or out-of-range rate, invalid matched sample | false | false | `error` |
| Loading, rejected chunk, error boundary | not emitted | not emitted | (`render_failed` where applicable) |

Checks: `failure_risk` finite and in [0, 1]; for `comparison`, integer
`total_tests > 0` and any `total_failures` in [0, `total_tests`];
`vehicle_prediction` if and only if `model_prediction`; `model_v55` source if and only
if `vehicle_prediction`; a prediction carries no cohort counts; matched scopes carry
counts. `sample_nonzero` is omitted for `model_prediction`. There is no `broad_fallback`
result kind: broader comparison is an analytical grouping only.

`scope_visible` is derived from the UI's own copy function: `buildScopeDisclosure`
must produce text for the scope. Note that text lives in the "How this result was
calculated" disclosure, which is collapsed by default; see the evidence note.

## Deduplication

An in-memory completion marker keyed by `operation_id` (or a per-mount random id for
restored links) lets StrictMode effect replays and remounts emit each of
`result_rendered`, `result_unavailable` and `render_failed` at most once per operation.
It is memory only (session storage is unapproved) and does **not** survive a reload.
Browsers retain `history.state` across a reload, so a reloaded fresh-check report route
can emit again with the same `operation_id`; counting a session once is the collector's
job under the OA-005 design.
