-- Migration 0004 (INT-W3a / ruling D-D): the exact DVSA response bytes behind a snapshot, hashed.
-- Server-side only: no API response selects these columns. The row is covered by the existing 12-month
-- retention (source_snapshots rows are deleted with their booking by ON DELETE CASCADE, retention.purge).
-- NULL for snapshots taken before this migration and for not_found snapshots.
ALTER TABLE source_snapshots
    ADD COLUMN raw_response bytea,
    ADD COLUMN raw_sha256   text;
ALTER TABLE source_snapshots ADD CONSTRAINT source_snapshots_raw_hash_matches
    CHECK ((raw_response IS NULL AND raw_sha256 IS NULL)
           OR (raw_response IS NOT NULL AND raw_sha256 = encode(sha256(raw_response), 'hex')));
