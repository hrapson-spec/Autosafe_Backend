# AutoSafe Database Guide

Never place connection coordinates or credentials in this file. Supply
`DATABASE_URL` through the environment; Railway `postgres://` URLs are
normalised by the application and operational scripts.

## Stores

### PostgreSQL `mot_risk`

Pre-aggregated comparison evidence at make/model, age-band, and mileage-band
grain. Core fields are:

- `model_id`, `age_band`, `mileage_band`
- `total_tests`, `total_failures`, `failure_risk`
- component-category rate columns for brakes, suspension, tyres, steering,
  visibility, lamps/electrics, and body/chassis

Current age bands are `0-2`, `3-5`, `6-10`, `11-15`, and `15+`. v2 reads use
sample-size-weighted aggregation, never an unweighted average of row rates.

### PostgreSQL `risk_checks`

The existing check table is expanded by
`migrations/add_report_contract_columns.py` with the v2 contract version,
opaque token, saved JSON payload, mileage source/value, match scope, evidence
counts, idempotency key, expiry, pseudonymisation timestamp, and keyed VRN
digest. The migration is additive and idempotent. It deliberately relaxes
`registration NOT NULL` because retention removes old plaintext registrations.

### PostgreSQL `leads`

Stores user-requested garage enquiries, MOT reminders, and emailed reports,
including consent timestamps and the comparison/mileage provenance needed to
avoid fabricating context. Postcode is nullable. `scripts/lead_retention_sweep.py`
deletes leads and dependent assignments after the published 12-month window.

### SQLite `risks`

`build_db.py` builds the local read fallback from `prod_data_clean.csv.gz`.
Its evidence ladder and weighted calculations must remain arithmetically
equivalent to PostgreSQL; the banding tests enforce that parity.

The database selected during the ladder is not necessarily the provenance of
the displayed number. When no matched cohort is returned, v2 displays the
checked-in dataset-wide aggregate with `prediction_source=dataset_reference`;
`match_scope` records whether the store was reached but empty or unavailable.

## Safe schema workflow

```bash
python migrations/add_report_contract_columns.py --dry-run
DATABASE_URL=<candidate database> python migrations/add_report_contract_columns.py
python migrations/add_report_contract_columns.py --rollback --dry-run
```

Run the forward migration before candidate application traffic. In an
incident, redeploy the previous image; do not use the destructive rollback as
the first response. The nullability relaxations are intentional one-way
compatibility changes and are not restored by rollback.

## Verification queries

Use counts and schema metadata, not row dumps containing personal data.

```sql
SELECT COUNT(*) FROM mot_risk;
SELECT COUNT(*) FROM risk_checks WHERE contract_version = '2.0';
SELECT COUNT(*) FROM risk_checks WHERE report_token IS NOT NULL;
SELECT COUNT(*) FROM leads WHERE created_at < NOW() - INTERVAL '12 months';
```

Retention and backlog scripts provide their own no-PII dry-run and post-run
verification output. Use those scripts rather than hand-written bulk updates.
