-- Migration 0002 (T6b): briefing feedback, display policy + order-only ranking, appointment time,
-- asynchronous import commit.

-- ---- appointment time (optional, Europe/London wall-clock; display/ordering only, never a model input) ----
ALTER TABLE bookings ADD COLUMN appointment_time time;
CREATE INDEX bookings_workshop_date_time ON bookings (workshop_id, appointment_date, appointment_time, id);

-- ---- display policy and ranking, recorded with the frozen assessment version ----
ALTER TABLE assessments
    ADD COLUMN display_policy    jsonb,
    ADD COLUMN rank_key          bigint,
    ADD COLUMN score_unrounded   double precision;   -- server-side only: never serialised to a client
ALTER TABLE assessments ADD CONSTRAINT assessments_score_only_when_available
    CHECK (state = 'available' OR (rank_key IS NULL AND score_unrounded IS NULL));

-- ---- briefing feedback: one row per (user's membership, booking, assessment version) ----
CREATE TABLE briefing_feedback (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workshop_id       uuid NOT NULL,
    booking_id        uuid NOT NULL,
    assessment_id     uuid NOT NULL,
    membership_id     uuid NOT NULL,
    value             text NOT NULL CHECK (value IN ('helpful', 'not_helpful')),
    reason_code       text CHECK (reason_code IN ('inaccurate_records', 'not_relevant', 'too_long',
                                                   'missing_information', 'other')),
    briefing_version  text NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (reason_code IS NULL OR value = 'not_helpful'),
    FOREIGN KEY (workshop_id, booking_id) REFERENCES bookings (workshop_id, id) ON DELETE CASCADE,
    FOREIGN KEY (workshop_id, assessment_id) REFERENCES assessments (workshop_id, id) ON DELETE CASCADE,
    FOREIGN KEY (workshop_id, membership_id) REFERENCES memberships (workshop_id, id),
    UNIQUE (workshop_id, booking_id, assessment_id, membership_id)
);

-- ---- asynchronous import commit ----
ALTER TABLE imports DROP CONSTRAINT imports_status_check;
ALTER TABLE imports ADD CONSTRAINT imports_status_check
    CHECK (status IN ('previewed', 'committing', 'committed', 'failed'));
ALTER TABLE imports
    ADD COLUMN progress_index        integer NOT NULL DEFAULT 0,   -- position in rows[] of the next row to handle
    ADD COLUMN progress_processed    integer NOT NULL DEFAULT 0,   -- 'new' rows handled so far (N of M)
    ADD COLUMN progress_total        integer,                      -- M: rows that were 'new' at commit request
    ADD COLUMN commit_requested_at   timestamptz,
    ADD COLUMN commit_membership_id  uuid,
    ADD COLUMN last_error            text,
    ADD FOREIGN KEY (workshop_id, commit_membership_id) REFERENCES memberships (workshop_id, id);

ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check
    CHECK (kind IN ('assess_booking', 'outcome_check', 'retention_sweep', 'import_commit'));
