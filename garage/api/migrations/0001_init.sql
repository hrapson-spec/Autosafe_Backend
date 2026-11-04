-- Garage MVP schema, migration 0001.
-- No customer name / email / phone columns exist anywhere. users.email is the
-- workshop staff sign-in address only.

CREATE TABLE workshops (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email       text NOT NULL UNIQUE CHECK (email = lower(email)),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE memberships (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workshop_id  uuid NOT NULL REFERENCES workshops(id) ON DELETE CASCADE,
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role         text NOT NULL CHECK (role IN ('owner', 'staff')),
    status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'removed')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    removed_at   timestamptz,
    UNIQUE (workshop_id, user_id),
    UNIQUE (workshop_id, id)
);
CREATE UNIQUE INDEX one_active_owner_per_workshop
    ON memberships (workshop_id) WHERE role = 'owner' AND status = 'active';

-- Invite codes gate signup (R12). Only a hash of the code is stored.
CREATE TABLE invites (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code_hash                text NOT NULL UNIQUE,
    note                     text,
    created_at               timestamptz NOT NULL DEFAULT now(),
    expires_at               timestamptz NOT NULL,
    consumed_at              timestamptz,
    consumed_by_workshop_id  uuid REFERENCES workshops(id) ON DELETE SET NULL
);

-- Sign-in links. Only a hash of the token is stored. Single use, 15 minutes.
CREATE TABLE login_tokens (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash   text NOT NULL UNIQUE,
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workshop_id  uuid NOT NULL REFERENCES workshops(id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    consumed_at  timestamptz
);
CREATE INDEX login_tokens_user_created ON login_tokens (user_id, created_at);

CREATE TABLE sessions (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash     text NOT NULL UNIQUE,
    user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workshop_id    uuid NOT NULL REFERENCES workshops(id) ON DELETE CASCADE,
    membership_id  uuid NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    expires_at     timestamptz NOT NULL,
    last_seen_at   timestamptz,
    revoked_at     timestamptz,
    revoked_reason text,
    FOREIGN KEY (workshop_id, membership_id) REFERENCES memberships (workshop_id, id) ON DELETE CASCADE
);
CREATE INDEX sessions_workshop ON sessions (workshop_id);

CREATE TABLE imports (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workshop_id        uuid NOT NULL REFERENCES workshops(id) ON DELETE CASCADE,
    membership_id      uuid NOT NULL,
    format             text NOT NULL CHECK (format IN ('paste', 'csv', 'manual')),
    status             text NOT NULL DEFAULT 'previewed' CHECK (status IN ('previewed', 'committed')),
    rows               jsonb NOT NULL,
    counts             jsonb NOT NULL,
    result             jsonb,
    created_at         timestamptz NOT NULL DEFAULT now(),
    expires_at         timestamptz NOT NULL,
    committed_at       timestamptz,
    FOREIGN KEY (workshop_id, membership_id) REFERENCES memberships (workshop_id, id),
    UNIQUE (workshop_id, id)
);

CREATE TABLE bookings (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workshop_id                uuid NOT NULL REFERENCES workshops(id) ON DELETE CASCADE,
    registration               text NOT NULL CHECK (registration ~ '^[A-Z0-9]{2,8}$'),
    -- appointment_date is the current London calendar date; import_key_date is
    -- the date it was first entered and never changes (idempotency key).
    appointment_date           date NOT NULL,
    import_key_date            date NOT NULL,
    booking_type               text NOT NULL CHECK (booking_type IN ('mot', 'service', 'retest', 'service_mot')),
    status                     text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
    source                     text NOT NULL CHECK (source IN ('import', 'manual')),
    import_id                  uuid,
    created_by_membership_id   uuid NOT NULL,
    created_at                 timestamptz NOT NULL DEFAULT now(),
    rescheduled_at             timestamptz,
    cancelled_at               timestamptz,
    FOREIGN KEY (workshop_id, created_by_membership_id) REFERENCES memberships (workshop_id, id),
    FOREIGN KEY (workshop_id, import_id) REFERENCES imports (workshop_id, id),
    UNIQUE (workshop_id, id),
    UNIQUE (workshop_id, registration, import_key_date, booking_type)
);
CREATE INDEX bookings_workshop_date ON bookings (workshop_id, appointment_date, id);

CREATE TABLE booking_events (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workshop_id   uuid NOT NULL,
    booking_id    uuid NOT NULL,
    event         text NOT NULL CHECK (event IN ('created', 'rescheduled', 'cancelled')),
    detail        jsonb NOT NULL DEFAULT '{}'::jsonb,
    membership_id uuid,
    created_at    timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (workshop_id, booking_id) REFERENCES bookings (workshop_id, id) ON DELETE CASCADE
);

-- Raw-ish upstream evidence (normalised DVSA history). Vehicle data only.
CREATE TABLE source_snapshots (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workshop_id     uuid NOT NULL,
    booking_id      uuid NOT NULL,
    kind            text NOT NULL CHECK (kind IN ('assessment_input', 'outcome_check')),
    upstream_status text NOT NULL CHECK (upstream_status IN ('ok', 'not_found')),
    history         jsonb,
    content_sha256  text NOT NULL,
    fetched_at      timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (workshop_id, booking_id) REFERENCES bookings (workshop_id, id) ON DELETE CASCADE,
    UNIQUE (workshop_id, id)
);

CREATE TABLE assessments (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workshop_id          uuid NOT NULL,
    booking_id           uuid NOT NULL,
    version              integer NOT NULL CHECK (version >= 1),
    trigger              text NOT NULL CHECK (trigger IN ('import', 'manual_entry', 'refresh')),
    state                text NOT NULL DEFAULT 'pending'
                         CHECK (state IN ('pending', 'available', 'unavailable', 'unsupported', 'failed')),
    reason               text,
    reason_text          text,
    result               jsonb,
    evidence             jsonb,
    input_cutoff_date    date NOT NULL,
    input_cutoff_at      timestamptz NOT NULL,
    snapshot_id          uuid,
    provider_name        text,
    provider_version     text,
    quota_counted        boolean NOT NULL DEFAULT false,
    retry_at             timestamptz,
    requested_by_membership_id uuid,
    created_at           timestamptz NOT NULL DEFAULT now(),
    completed_at         timestamptz,
    FOREIGN KEY (workshop_id, booking_id) REFERENCES bookings (workshop_id, id) ON DELETE CASCADE,
    FOREIGN KEY (workshop_id, snapshot_id) REFERENCES source_snapshots (workshop_id, id),
    UNIQUE (workshop_id, id),
    UNIQUE (booking_id, version),
    CHECK ((state = 'pending') = (completed_at IS NULL))
);
-- Versions are frozen once they leave 'pending'.
CREATE FUNCTION assessments_freeze() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.state <> 'pending' THEN
        RAISE EXCEPTION 'assessment version % is frozen', OLD.id;
    END IF;
    IF TG_OP = 'DELETE' AND OLD.state <> 'pending'
       AND coalesce(current_setting('garage.retention_sweep', true), '') <> 'on' THEN
        RAISE EXCEPTION 'assessment version % cannot be deleted', OLD.id;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER assessments_freeze_t BEFORE UPDATE OR DELETE ON assessments
    FOR EACH ROW EXECUTE FUNCTION assessments_freeze();

CREATE TABLE decisions (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workshop_id    uuid NOT NULL,
    booking_id     uuid NOT NULL,
    decision_type  text NOT NULL CHECK (decision_type IN
                   ('check_repair_history', 'offer_inspection', 'adjust_booking_plan', 'proceed_as_booked')),
    note           text CHECK (note IS NULL OR length(note) <= 500),
    membership_id  uuid NOT NULL,
    assessment_id  uuid,
    supersedes_id  uuid,
    created_at     timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (workshop_id, booking_id) REFERENCES bookings (workshop_id, id) ON DELETE CASCADE,
    FOREIGN KEY (workshop_id, membership_id) REFERENCES memberships (workshop_id, id),
    FOREIGN KEY (workshop_id, assessment_id) REFERENCES assessments (workshop_id, id)
);
-- Decision history is append-only (retention sweep is the one exception).
CREATE FUNCTION append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' AND coalesce(current_setting('garage.retention_sweep', true), '') = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER decisions_append_only BEFORE UPDATE OR DELETE ON decisions
    FOR EACH ROW EXECUTE FUNCTION append_only();

CREATE TABLE outcomes (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workshop_id   uuid NOT NULL,
    booking_id    uuid NOT NULL,
    check_day     integer NOT NULL CHECK (check_day IN (1, 3, 7, 14)),
    schedule_date date NOT NULL,   -- appointment date the check was scheduled against
    status        text NOT NULL DEFAULT 'scheduled'
                  CHECK (status IN ('scheduled', 'matched', 'no_subsequent_test', 'ambiguous',
                                    'not_applicable', 'cancelled', 'failed')),
    due_at        timestamptz NOT NULL,
    checked_at    timestamptz,
    matched       jsonb,
    candidates    jsonb,
    detail        text,
    snapshot_id   uuid,
    created_at    timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (workshop_id, booking_id) REFERENCES bookings (workshop_id, id) ON DELETE CASCADE,
    FOREIGN KEY (workshop_id, snapshot_id) REFERENCES source_snapshots (workshop_id, id),
    UNIQUE (booking_id, check_day, schedule_date)
);
CREATE INDEX outcomes_booking ON outcomes (workshop_id, booking_id);

-- Postgres job queue (SELECT ... FOR UPDATE SKIP LOCKED).
CREATE TABLE jobs (
    id            bigserial PRIMARY KEY,
    workshop_id   uuid REFERENCES workshops(id) ON DELETE CASCADE,   -- NULL only for system jobs
    kind          text NOT NULL CHECK (kind IN ('assess_booking', 'outcome_check', 'retention_sweep')),
    payload       jsonb NOT NULL DEFAULT '{}'::jsonb,
    dedupe_key    text,
    status        text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    run_at        timestamptz NOT NULL DEFAULT now(),
    attempts      integer NOT NULL DEFAULT 0,
    max_attempts  integer NOT NULL DEFAULT 3,
    locked_at     timestamptz,
    locked_by     text,
    last_error    text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    CHECK ((workshop_id IS NULL) = (kind = 'retention_sweep'))
);
CREATE INDEX jobs_claim ON jobs (run_at, id) WHERE status = 'queued';
CREATE UNIQUE INDEX jobs_dedupe ON jobs (dedupe_key) WHERE dedupe_key IS NOT NULL AND status IN ('queued', 'running');

-- Fair use: 50 assessments per workshop per London day.
CREATE TABLE usage_counters (
    workshop_id  uuid NOT NULL REFERENCES workshops(id) ON DELETE CASCADE,
    usage_date   date NOT NULL,
    kind         text NOT NULL,
    count        integer NOT NULL DEFAULT 0 CHECK (count >= 0),
    PRIMARY KEY (workshop_id, usage_date, kind)
);

-- Global DVSA throttle: one row, successive callers get slots >= 200 ms apart.
CREATE TABLE dvsa_throttle (
    id         integer PRIMARY KEY CHECK (id = 1),
    next_slot  timestamptz NOT NULL
);
INSERT INTO dvsa_throttle (id, next_slot) VALUES (1, '-infinity');
