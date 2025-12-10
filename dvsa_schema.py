"""
DVSA Schema Validation Module

Enforces strict schema contracts for DVSA data files to prevent silent data corruption
from schema drift. All files must pass validation before processing.

Per-year/per-source contracts capture real schema evolution:
- 2022-2023: pipe delimiter, original column names
- 2024: comma delimiter, added completed_date, renamed location_id
"""

import os
import re
from typing import Optional


class SchemaValidationError(Exception):
    """Raised when a file fails schema validation."""
    pass


# =============================================================================
# SCHEMA CONTRACTS
# =============================================================================

TEST_RESULT_COLUMNS_BASE = [
    "test_id", "vehicle_id", "test_date", "test_class_id", "test_type",
    "test_result", "test_mileage", "postcode_area", "make", "model",
    "colour", "fuel_type", "cylinder_capacity", "first_use_date"
]

TEST_ITEM_COLUMNS_BASE = [
    "test_id", "rfr_id", "rfr_type_code", "location_id", "dangerous_mark"
]

# Per-year schema contracts
SCHEMAS = {
    # TEST_RESULT schemas
    ("test_result", 2022): {
        "delimiter": "|",
        "columns": TEST_RESULT_COLUMNS_BASE,
        "column_count": 14,
        "types": {
            "test_id": int,
            "vehicle_id": int,
            "test_mileage": float,
            "cylinder_capacity": int,
        }
    },
    ("test_result", 2023): {
        "delimiter": "|",
        "columns": TEST_RESULT_COLUMNS_BASE,
        "column_count": 14,
        "types": {
            "test_id": int,
            "vehicle_id": int,
            "test_mileage": float,
            "cylinder_capacity": int,
        }
    },
    ("test_result", 2024): {
        "delimiter": ",",
        "columns": TEST_RESULT_COLUMNS_BASE + ["completed_date"],
        "column_count": 15,
        "types": {
            "test_id": int,
            "vehicle_id": int,
            "test_mileage": float,
            "cylinder_capacity": int,
        }
    },
    
    # TEST_ITEM schemas
    ("test_item", 2022): {
        "delimiter": "|",
        "columns": TEST_ITEM_COLUMNS_BASE,
        "column_count": 5,
        "types": {
            "test_id": int,
            "rfr_id": int,
        }
    },
    ("test_item", 2023): {
        "delimiter": "|",
        "columns": TEST_ITEM_COLUMNS_BASE,
        "column_count": 5,
        "types": {
            "test_id": int,
            "rfr_id": int,
        }
    },
    ("test_item", 2024): {
        "delimiter": ",",
        "columns": ["test_id", "rfr_id", "rfr_type_code", 
                    "mot_test_rfr_location_type_id", "dangerous_mark", "completed_date"],
        "column_count": 6,
        "aliases": {"location_id": "mot_test_rfr_location_type_id"},
        "types": {
            "test_id": int,
            "rfr_id": int,
        }
    },
}


# =============================================================================
# VALIDATION FUNCTIONS
# =============================================================================

def infer_year_from_path(filepath: str) -> int:
    """Extract year from filepath. Handles patterns like '2024', '202401', '/2023/'."""
    basename = os.path.basename(filepath)
    
    # Try YYYYMM pattern first (e.g., test_result_202401.csv)
    match = re.search(r'_(\d{4})\d{2}\.csv$', basename)
    if match:
        return int(match.group(1))
    
    # Try YYYY pattern in filename (e.g., test_result_2022.csv)
    match = re.search(r'_(\d{4})\.csv$', basename)
    if match:
        return int(match.group(1))
    
    # Try year in directory path (e.g., /2023/test_item.csv)
    match = re.search(r'/(\d{4})/', filepath)
    if match:
        return int(match.group(1))
    
    raise SchemaValidationError(
        f"Cannot infer year from path: {filepath}. "
        "Expected pattern like 'test_result_2024.csv', 'test_item_202401.csv', or '/2023/test_item.csv'"
    )


def infer_file_type(filepath: str) -> str:
    """Infer file type (test_result or test_item) from filepath."""
    basename = os.path.basename(filepath).lower()
    if "test_result" in basename or "result" in filepath.lower():
        return "test_result"
    elif "test_item" in basename or "item" in filepath.lower() or "failure" in filepath.lower():
        return "test_item"
    raise SchemaValidationError(f"Cannot infer file type from: {filepath}")


def validate_schema(filepath: str, file_type: Optional[str] = None, 
                    year: Optional[int] = None) -> None:
    """
    Validate that a file matches its expected schema contract.
    
    Args:
        filepath: Path to the CSV file
        file_type: 'test_result' or 'test_item' (inferred if not provided)
        year: Year of the data (inferred from path if not provided)
    
    Raises:
        SchemaValidationError: If file fails any validation check
    """
    if not os.path.exists(filepath):
        raise SchemaValidationError(f"File not found: {filepath}")
    
    # Infer type and year if not provided
    if file_type is None:
        file_type = infer_file_type(filepath)
    if year is None:
        year = infer_year_from_path(filepath)
    
    # Get schema contract
    schema_key = (file_type, year)
    if schema_key not in SCHEMAS:
        raise SchemaValidationError(
            f"No schema contract for {schema_key}. "
            f"Known schemas: {list(SCHEMAS.keys())}"
        )
    
    schema = SCHEMAS[schema_key]
    expected_delimiter = schema["delimiter"]
    expected_count = schema["column_count"]
    expected_columns = set(schema["columns"])
    
    # Read first line
    with open(filepath, 'r', encoding='latin1') as f:
        header_line = f.readline().strip()
    
    # Check 1: Delimiter
    # Detect actual delimiter
    if '|' in header_line and ',' not in header_line:
        actual_delimiter = '|'
    elif ',' in header_line:
        actual_delimiter = ','
    else:
        raise SchemaValidationError(
            f"Cannot detect delimiter in {filepath}. Header: {header_line[:100]}..."
        )
    
    if actual_delimiter != expected_delimiter:
        raise SchemaValidationError(
            f"Delimiter mismatch in {filepath}: "
            f"expected '{expected_delimiter}', found '{actual_delimiter}'"
        )
    
    # Parse columns
    actual_columns = header_line.split(actual_delimiter)
    actual_count = len(actual_columns)
    
    # Check 2: Column count
    if actual_count != expected_count:
        raise SchemaValidationError(
            f"Column count mismatch in {filepath}: "
            f"expected {expected_count}, found {actual_count}. "
            f"Columns: {actual_columns}"
        )
    
    # Check 3: Column names (with alias support)
    aliases = schema.get("aliases", {})
    normalized_actual = set()
    for col in actual_columns:
        # Check if this column is a known alias
        reverse_alias = {v: k for k, v in aliases.items()}
        if col in reverse_alias:
            normalized_actual.add(reverse_alias[col])
        else:
            normalized_actual.add(col)
    
    # Normalize expected columns too (use canonical names)
    normalized_expected = set()
    for col in expected_columns:
        if col in aliases:
            normalized_expected.add(aliases[col])
        else:
            normalized_expected.add(col)
    
    # For 2024 test_item, the actual file uses mot_test_rfr_location_type_id
    # We need to compare actual columns against expected (which may include the alias)
    missing = expected_columns - set(actual_columns)
    unexpected = set(actual_columns) - expected_columns
    
    # Remove aliased columns from missing/unexpected
    for canonical, alias in aliases.items():
        if canonical in missing and alias in unexpected:
            missing.discard(canonical)
            unexpected.discard(alias)
    
    if missing:
        raise SchemaValidationError(
            f"Missing columns in {filepath}: {missing}"
        )
    if unexpected:
        raise SchemaValidationError(
            f"Unexpected columns in {filepath}: {unexpected}. "
            "This may indicate DVSA schema drift. Update dvsa_schema.py if intentional."
        )
    
    # All checks passed


def validate_all_sources(sources: list, file_type: str) -> None:
    """
    Validate all files from a source configuration.
    
    Args:
        sources: List of (folder, delimiter, pattern) tuples
        file_type: 'test_result' or 'test_item'
    
    Raises:
        SchemaValidationError: If any file fails validation
    """
    import glob
    
    validated = 0
    for folder, expected_delim, pattern in sources:
        file_pattern = os.path.join(folder, pattern)
        matched_files = glob.glob(file_pattern)
        
        for filepath in matched_files:
            validate_schema(filepath, file_type)
            validated += 1
    
    if validated == 0:
        raise SchemaValidationError(
            f"No files found to validate for {file_type}. Check source paths."
        )
    
    print(f"✓ Schema validation passed: {validated} {file_type} files validated")


# =============================================================================
# CLI for manual validation
# =============================================================================

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python dvsa_schema.py <filepath> [file_type] [year]")
        print("Example: python dvsa_schema.py 'MOT Test Results/test_result_202401.csv'")
        sys.exit(1)
    
    filepath = sys.argv[1]
    file_type = sys.argv[2] if len(sys.argv) > 2 else None
    year = int(sys.argv[3]) if len(sys.argv) > 3 else None
    
    try:
        validate_schema(filepath, file_type, year)
        print(f"✓ {filepath} passed schema validation")
    except SchemaValidationError as e:
        print(f"✗ Validation failed: {e}")
        sys.exit(1)
