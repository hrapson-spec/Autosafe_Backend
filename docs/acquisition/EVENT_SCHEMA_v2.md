# Acquisition acknowledgement schema v2

`event_schema_v2.json` defines event-specific fields and mutually consistent result
states. `acquisition_routes.py` additionally validates the mandatory wire envelope:
random UUID `session_id`, `landing_id`, integer `window_start_minute`, allowlisted
`pilot_group`, `page_family`, `source_group`, and optional release SHA. Unexpected
fields are rejected. Schema version is exactly 2; wire metric version is exactly
`oa-journey-30m-v2`. The old v1 files are historical, not accepted bodies.

The acknowledgement semantics from OA-004 remain: mounted result delivery is a
client report of rendering, not a probability validation, diagnosis or model
qualification. The source's `scope_visible` assertion is not an independent gaze,
viewport or human-readability measurement. Demo and dataset-reference results
are rendered but not supported vehicle results; unavailable/error states are
separate. A render failure in the fixed window wins for that operation.

See `COLLECTOR.md` for the bounded receipt window, full metric definition,
coverage, attribution, retention and enable conditions. JSON-schema parity tests
cover the result combinations; backend tests cover the wire envelope and store
window. Every published result must carry the metric version and coverage limits.
