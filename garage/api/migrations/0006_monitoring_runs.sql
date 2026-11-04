-- Migration 0006 (APP_COMPLETION_V1 / T1b, task 3): an append-only record of monitoring runs (G6 replay, G7).
--
-- Holds aggregates only: counts, PSI values, refusal-code counts, status. No booking id, registration, vehicle id, feature value
-- or outcome row (monitoring/runs.py refuses a result that carries such a key). Not workshop-scoped by design: monitoring pools
-- bookings across workshops (PLAN_rev3_20261026.md:96), so the only workshop identifiers inside `result` are the ones that
-- already exist in workshops/assessments. It is written only by the operator commands (`--record`), never by an API handler.
-- Retention is NOT decided (open question for the DPIA); rows are never updated, and deleted only by an explicit sweep that
-- sets garage.retention_sweep = 'on' (same convention as assessments_freeze).
CREATE TABLE monitoring_runs (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind                   text NOT NULL CHECK (kind IN ('g6_replay', 'g7')),
    window_start           timestamptz,
    window_end             timestamptz,
    policy_sha256          text NOT NULL CHECK (policy_sha256 ~ '^[0-9a-f]{64}$'),
    reference_sha256       text CHECK (reference_sha256 IS NULL OR reference_sha256 ~ '^[0-9a-f]{64}$'),
    bundle_manifest_sha256 text CHECK (bundle_manifest_sha256 IS NULL OR bundle_manifest_sha256 ~ '^[0-9a-f]{64}$'),
    result                 jsonb NOT NULL CHECK (jsonb_typeof(result) = 'object'),
    created_at             timestamptz NOT NULL DEFAULT now(),
    CHECK ((window_start IS NULL) = (window_end IS NULL)),
    CHECK (window_start IS NULL OR window_start < window_end),
    CHECK (kind <> 'g6_replay' OR (reference_sha256 IS NOT NULL AND bundle_manifest_sha256 IS NOT NULL
                                   AND window_start IS NOT NULL))
);
CREATE INDEX monitoring_runs_kind_created ON monitoring_runs (kind, created_at DESC);
CREATE FUNCTION monitoring_runs_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'monitoring run % is append-only', OLD.id;
    END IF;
    IF coalesce(current_setting('garage.retention_sweep', true), '') <> 'on' THEN
        RAISE EXCEPTION 'monitoring run % cannot be deleted', OLD.id;
    END IF;
    RETURN OLD;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER monitoring_runs_append_only BEFORE UPDATE OR DELETE ON monitoring_runs
    FOR EACH ROW EXECUTE FUNCTION monitoring_runs_append_only();
