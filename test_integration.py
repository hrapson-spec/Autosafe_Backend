"""
Integration test for interpolation with real database.

This test:
1. Builds the SQLite database from prod_data_clean.csv.gz
2. Queries real vehicle data
3. Verifies interpolation produces different scores for different mileages
4. Compares against non-interpolated (raw bucket) scores
"""
import sqlite3
import os
import sys

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from interpolation import (
    interpolate_risk,
    MILEAGE_ORDER,
    MILEAGE_BUCKETS
)

DB_FILE = 'autosafe.db'
DATA_FILE = 'prod_data_clean.csv.gz'


def build_database_if_needed():
    """Build the database if it doesn't exist."""
    if os.path.exists(DB_FILE):
        print(f"Database {DB_FILE} already exists")
        return True

    if not os.path.exists(DATA_FILE):
        print(f"ERROR: Data file {DATA_FILE} not found!")
        return False

    print(f"Building {DB_FILE} from {DATA_FILE}...")
    import gzip
    import csv

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS risks")

    with gzip.open(DATA_FILE, 'rt', encoding='utf-8') as f:
        reader = csv.reader(f)
        headers = next(reader)

        clean_headers = [h.strip().replace(' ', '_').replace('-', '_') for h in headers]

        col_defs = []
        for i, col in enumerate(clean_headers):
            if i < 3:
                col_defs.append(f'"{col}" TEXT')
            elif i < 5:
                col_defs.append(f'"{col}" INTEGER')
            else:
                col_defs.append(f'"{col}" REAL')

        cursor.execute(f"CREATE TABLE risks ({', '.join(col_defs)})")

        placeholders = ','.join(['?' for _ in clean_headers])
        insert_sql = f"INSERT INTO risks VALUES ({placeholders})"

        batch = []
        for row in reader:
            processed = []
            for i, val in enumerate(row):
                val = val.strip()
                if i < 3:
                    processed.append(val)
                elif i < 5:
                    processed.append(int(float(val)) if val else 0)
                else:
                    processed.append(float(val) if val else 0.0)
            batch.append(processed)

            if len(batch) >= 5000:
                cursor.executemany(insert_sql, batch)
                batch = []

        if batch:
            cursor.executemany(insert_sql, batch)

    # Create indexes
    cursor.execute('CREATE INDEX idx_model_id ON risks (model_id)')
    cursor.execute('CREATE INDEX idx_model_age_mileage ON risks (model_id, age_band, mileage_band)')

    conn.commit()
    conn.close()
    print(f"Database built successfully!")
    return True


def get_adjacent_mileage_bands(mileage_band: str):
    """Get adjacent bands for interpolation."""
    try:
        idx = MILEAGE_ORDER.index(mileage_band)
        bands = [mileage_band]
        if idx > 0:
            bands.append(MILEAGE_ORDER[idx - 1])
        if idx < len(MILEAGE_ORDER) - 1:
            bands.append(MILEAGE_ORDER[idx + 1])
        return bands
    except ValueError:
        return [mileage_band]


def fetch_bucket_data(conn, model_id: str, age_band: str, mileage_bands: list):
    """Fetch data for multiple mileage bands."""
    placeholders = ",".join("?" * len(mileage_bands))
    query = f"""
        SELECT * FROM risks
        WHERE model_id = ? AND age_band = ? AND mileage_band IN ({placeholders})
    """
    conn.row_factory = sqlite3.Row
    rows = conn.execute(query, [model_id, age_band] + mileage_bands).fetchall()

    result = {}
    for row in rows:
        row_dict = dict(row)
        key = (row_dict.get('age_band'), row_dict.get('mileage_band'))
        result[key] = row_dict
    return result


def interpolate_result(base_result, bucket_data, actual_mileage, age_band):
    """Apply interpolation to risk values."""
    result = dict(base_result)

    risk_fields = [k for k in base_result.keys()
                   if k.startswith("Risk_") or k == "Failure_Risk"]

    mileage_risks = {}
    for mb in MILEAGE_ORDER:
        key = (age_band, mb)
        if key in bucket_data:
            mileage_risks[mb] = bucket_data[key]

    for field in risk_fields:
        field_by_mileage = {
            mb: data.get(field, 0.0)
            for mb, data in mileage_risks.items()
            if field in data
        }

        if len(field_by_mileage) >= 2:
            interpolated = interpolate_risk(actual_mileage, "mileage", field_by_mileage)
            result[field] = round(interpolated, 6)

    return result


def run_integration_test():
    """Run integration test with real data."""
    print("=" * 70)
    print("INTEGRATION TEST: Interpolation with Real Database")
    print("=" * 70)
    print()

    # Build database if needed
    if not build_database_if_needed():
        print("FAILED: Could not build database")
        return False

    # Connect to database
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row

    # Find a popular model with good data coverage
    query = """
        SELECT model_id, age_band, COUNT(*) as band_count, SUM(Total_Tests) as total
        FROM risks
        WHERE age_band = '3-5'
        GROUP BY model_id
        HAVING band_count >= 3
        ORDER BY total DESC
        LIMIT 5
    """
    popular_models = conn.execute(query).fetchall()

    if not popular_models:
        print("FAILED: No models with sufficient data coverage")
        conn.close()
        return False

    print("Top 5 models with best data coverage (age_band='3-5'):")
    for row in popular_models:
        print(f"  {row['model_id']}: {row['band_count']} mileage bands, {row['total']:,} total tests")
    print()

    # Test with the most popular model
    test_model = popular_models[0]['model_id']
    test_age_band = '3-5'

    print(f"Testing with: {test_model}, age_band={test_age_band}")
    print("-" * 70)

    # Fetch all mileage bands for this model/age
    all_bands_query = """
        SELECT mileage_band, Failure_Risk, Total_Tests
        FROM risks
        WHERE model_id = ? AND age_band = ?
        ORDER BY mileage_band
    """
    all_bands = conn.execute(all_bands_query, (test_model, test_age_band)).fetchall()

    print("\nRaw bucket data:")
    for row in all_bands:
        print(f"  {row['mileage_band']}: Failure_Risk={row['Failure_Risk']:.4%} (n={row['Total_Tests']:,})")

    # Test interpolation at various mileages within 30k-60k band
    mileage_band = '30k-60k'
    mileage_bands = get_adjacent_mileage_bands(mileage_band)
    bucket_data = fetch_bucket_data(conn, test_model, test_age_band, mileage_bands)

    if (test_age_band, mileage_band) not in bucket_data:
        print(f"\nWARNING: No data for {mileage_band} band, trying 60k-100k")
        mileage_band = '60k-100k'
        mileage_bands = get_adjacent_mileage_bands(mileage_band)
        bucket_data = fetch_bucket_data(conn, test_model, test_age_band, mileage_bands)

    if (test_age_band, mileage_band) not in bucket_data:
        print("FAILED: No suitable mileage band found")
        conn.close()
        return False

    base_result = bucket_data[(test_age_band, mileage_band)]
    raw_risk = base_result.get('Failure_Risk', 0)

    print(f"\nInterpolation test for {mileage_band} band:")
    print(f"Raw bucket risk: {raw_risk:.4%}")
    print()

    # Test at different mileages
    if mileage_band == '30k-60k':
        test_mileages = [30001, 35000, 40000, 45000, 50000, 55000, 59999]
    else:  # 60k-100k
        test_mileages = [60001, 70000, 78000, 85000, 95000, 99999]

    print(f"{'Mileage':>10} | {'Raw Bucket':>12} | {'Interpolated':>12} | {'Difference':>12}")
    print("-" * 55)

    interpolated_risks = []
    for mileage in test_mileages:
        interp_result = interpolate_result(base_result, bucket_data, mileage, test_age_band)
        interp_risk = interp_result.get('Failure_Risk', raw_risk)
        diff = interp_risk - raw_risk
        interpolated_risks.append(interp_risk)
        print(f"{mileage:>10,} | {raw_risk:>11.4%} | {interp_risk:>11.4%} | {diff:>+11.4%}")

    conn.close()

    # Validate results
    print()
    print("=" * 70)
    print("VALIDATION:")
    print("=" * 70)

    # Check 1: All interpolated risks should be unique (no ties)
    unique_risks = len(set(round(r, 6) for r in interpolated_risks))
    all_unique = unique_risks == len(interpolated_risks)
    print(f"  [{'PASS' if all_unique else 'FAIL'}] All {len(test_mileages)} mileages produce unique scores: {unique_risks}/{len(test_mileages)}")

    # Check 2: Risks should be monotonically increasing with mileage
    monotonic = all(interpolated_risks[i] <= interpolated_risks[i+1]
                    for i in range(len(interpolated_risks)-1))
    print(f"  [{'PASS' if monotonic else 'FAIL'}] Risks increase monotonically with mileage")

    # Check 3: Interpolated range should span the raw buckets
    risk_range = max(interpolated_risks) - min(interpolated_risks)
    has_range = risk_range > 0.01  # At least 1% spread
    print(f"  [{'PASS' if has_range else 'FAIL'}] Interpolated risk range: {risk_range:.2%} (should be > 1%)")

    # Check 4: Center of bucket should be close to raw risk
    center_idx = len(test_mileages) // 2
    center_risk = interpolated_risks[center_idx]
    center_close = abs(center_risk - raw_risk) < 0.02
    print(f"  [{'PASS' if center_close else 'FAIL'}] Center mileage risk ({center_risk:.4%}) close to raw ({raw_risk:.4%})")

    all_passed = all_unique and monotonic and has_range and center_close
    print()
    print(f"Overall: {'ALL TESTS PASSED' if all_passed else 'SOME TESTS FAILED'}")

    return all_passed


if __name__ == "__main__":
    success = run_integration_test()
    sys.exit(0 if success else 1)
