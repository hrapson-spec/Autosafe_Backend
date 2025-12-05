# AutoSafe Database Guide

## Connection Details

**Production Database:** Railway PostgreSQL

```
Host: gondola.proxy.rlwy.net
Port: 56093
Database: railway
User: postgres
```

> ⚠️ Use internal URL (`postgres.railway.internal:5432`) from Railway services.

---

## Schema

### Table: `mot_risk`

| Column | Type | Description |
|--------|------|-------------|
| `model_id` | VARCHAR(255) | Vehicle make + model (e.g., "FORD FIESTA") |
| `age_band` | VARCHAR(255) | Age range (0-3, 3-6, 6-10, 10+) |
| `mileage_band` | VARCHAR(255) | Mileage range (0-30k, 30k-60k, etc.) |
| `total_tests` | INTEGER | Number of MOT tests |
| `total_failures` | INTEGER | Number of failures |
| `failure_risk` | REAL | Failure probability (0-1) |
| `risk_brakes` | REAL | Brake failure risk |
| `risk_suspension` | REAL | Suspension failure risk |
| ... | REAL | Other component risks |

### Indexes
- `idx_model` on `model_id`
- `idx_age` on `age_band`
- `idx_mileage` on `mileage_band`

---

## Data Migration

### Full Re-upload
```bash
# Set DATABASE_URL
export DATABASE_URL="postgresql://..."

# Run upload script
python3 upload_to_postgres.py
```

This will:
1. Drop existing `mot_risk` table
2. Create new table with indexes
3. Upload all rows from `FINAL_MOT_REPORT.csv`

---

## Common Queries

```sql
-- Row count
SELECT COUNT(*) FROM mot_risk;

-- Check specific model
SELECT * FROM mot_risk WHERE model_id LIKE 'FORD FIESTA%' LIMIT 10;

-- Average risk by make
SELECT 
  SPLIT_PART(model_id, ' ', 1) as make,
  AVG(failure_risk) as avg_risk
FROM mot_risk
GROUP BY 1
ORDER BY 2 DESC
LIMIT 20;
```

---

## Backup

```bash
# Export to CSV
psql $DATABASE_URL -c "\COPY mot_risk TO 'backup.csv' CSV HEADER"

# Or use pg_dump
pg_dump $DATABASE_URL -t mot_risk > backup.sql
```
