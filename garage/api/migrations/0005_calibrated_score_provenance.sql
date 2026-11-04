-- Migration 0005 (APP_COMPLETION_V1 / T1b, task 1): the calibrated probability, stored under its own name.
--
-- Provenance (see execution/tickets/APP_COMPLETION_V1/REPORT_T1b.md): the scorer's `probability` is the Platt-calibrated
-- probability (T2c/rev3/spine/serve.py: apply_platt(margin)); G6 (score PSI) and G7 (bias, AUC, bands) refer to that value
-- (protocol/AMENDMENT_DRAFT.md:49 "calibrated score", :57). Until now it was kept only as `shadow_probability`, and only for an
-- UNQUALIFIED bundle (0003 forbids it on available rows), so once a bundle is qualified no calibrated probability was stored
-- unless ranking was enabled (`score_unrounded`). These two columns hold it for every scored assessment, qualified or not,
-- together with the manifest hash of the bundle that produced it (needed to pair a stored score with a replayed input row).
--
-- Server-side only: serialize.assessment_json names its fields explicitly and none of these is among them.
-- Existing rows are not back-filled (assessments are frozen once completed): rows written before this migration keep their
-- value in shadow_probability, and the monitoring commands take the score column by name.
ALTER TABLE assessments
    ADD COLUMN calibrated_probability   double precision,
    ADD COLUMN calibrated_bundle_sha256 text;
ALTER TABLE assessments ADD CONSTRAINT assessments_calibrated_in_range
    CHECK (calibrated_probability IS NULL OR (calibrated_probability >= 0 AND calibrated_probability <= 1));
ALTER TABLE assessments ADD CONSTRAINT assessments_calibrated_pair
    CHECK ((calibrated_probability IS NULL) = (calibrated_bundle_sha256 IS NULL));
ALTER TABLE assessments ADD CONSTRAINT assessments_calibrated_bundle_hash
    CHECK (calibrated_bundle_sha256 IS NULL OR calibrated_bundle_sha256 ~ '^[0-9a-f]{64}$');
ALTER TABLE assessments ADD CONSTRAINT assessments_calibrated_only_when_scored
    CHECK (calibrated_probability IS NULL OR state IN ('available', 'unavailable'));
