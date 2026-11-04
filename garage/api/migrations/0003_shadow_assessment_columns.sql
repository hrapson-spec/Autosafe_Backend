-- Migration 0003 (INT-W3a): server-side shadow-scoring values from an unqualified model bundle.
-- Never serialised to any API response (serialize.assessment_json names its fields explicitly).
-- Raw model output is stored only for non-available assessments whose ranking is disabled.
ALTER TABLE assessments
    ADD COLUMN shadow_margin          double precision,
    ADD COLUMN shadow_probability     double precision,
    ADD COLUMN shadow_bundle_sha256   text,
    ADD COLUMN shadow_model_version   text;
ALTER TABLE assessments ADD CONSTRAINT assessments_shadow_not_when_available
    CHECK (state NOT IN ('available', 'pending')
           OR (shadow_margin IS NULL AND shadow_probability IS NULL
               AND shadow_bundle_sha256 IS NULL AND shadow_model_version IS NULL));
ALTER TABLE assessments ADD CONSTRAINT assessments_shadow_ranking_disabled
    CHECK (shadow_margin IS NULL OR coalesce(display_policy ->> 'ranking_enabled', 'false') <> 'true');
