"""
AutoSafe Database Module - PostgreSQL Connection
Uses DATABASE_URL environment variable from Railway.
"""
import os
from typing import List, Dict, Optional

# Check if we have a database URL
DATABASE_URL = os.environ.get("DATABASE_URL")

# PostgreSQL connection pool (lazy initialization)
_pool = None

import logging

logger = logging.getLogger(__name__)

# Minimum total tests required for a make/model to appear in UI dropdowns
# This filters out typos, garbage entries, and extremely rare vehicles
# while keeping all legitimate production cars (even rare ones have >100 tests)
MIN_TESTS_FOR_UI = 100

async def get_pool():
    """Get or create the connection pool."""
    global _pool
    if _pool is None and DATABASE_URL:
        import asyncpg
        try:
            # Railway uses postgres:// but asyncpg needs postgresql://
            db_url = DATABASE_URL.replace("postgres://", "postgresql://")
            _pool = await asyncpg.create_pool(db_url, min_size=5, max_size=20)
            logger.info("Database connection pool created")
        except Exception as e:
            logger.error(f"Failed to create database connection pool: {e}")
            return None
    return _pool

async def close_pool():
    """Close the connection pool."""
    global _pool
    if _pool:
        try:
            await _pool.close()
            _pool = None
            logger.info("Database connection pool closed")
        except Exception as e:
            logger.error(f"Error closing database pool: {e}")

def normalize_columns(row_dict: Dict) -> Dict:
    """Convert lowercase PostgreSQL column names back to expected format.
    
    Examples:
        total_tests -> Total_Tests
        failure_risk -> Failure_Risk
        risk_brakes -> Risk_Brakes
    """
    normalized = {}
    for key, value in row_dict.items():
        # Convert to Title_Case (each word capitalized, joined by underscore)
        parts = key.split('_')
        new_key = '_'.join(part.capitalize() for part in parts)
        normalized[new_key] = value
    return normalized

async def get_makes() -> List[str]:
    """Return a list of all unique canonical vehicle makes with minimum test volume."""
    from consolidate_models import normalize_make, MAJOR_MAKES
    
    pool = await get_pool()
    if not pool:
        return None  # Fallback to mock data
    
    async with pool.acquire() as conn:
        # Only return makes with sufficient test volume to filter out garbage
        rows = await conn.fetch("""
            SELECT SUBSTRING(model_id FROM '^[^ ]+') as make, SUM(total_tests) as test_count
            FROM mot_risk
            GROUP BY make
            HAVING SUM(total_tests) >= $1
        """, MIN_TESTS_FOR_UI)
        
        makes = set()
        for row in rows:
            canonical = normalize_make(row['make'])
            if canonical:  # Filter out None (garbage makes)
                makes.add(canonical)
        
        # Sort with major makes first, then alphabetically
        def sort_key(make):
            if make in MAJOR_MAKES:
                return (0, MAJOR_MAKES.index(make))
            return (1, make)
        
        return sorted(list(makes), key=sort_key)

async def get_models(make: str) -> List[str]:
    """Return a list of consolidated base models for a given make with minimum test volume."""
    from consolidate_models import extract_base_model, get_canonical_models_for_make, CANONICAL_MAKES
    
    pool = await get_pool()
    if not pool:
        return None  # Fallback to mock data
    
    # Handle canonical make lookups (e.g., "MERCEDES-BENZ" needs to search "MERCEDES")
    search_patterns = [make.upper()]
    
    # Add reverse mappings for compound makes
    for raw, canonical in CANONICAL_MAKES.items():
        if canonical == make.upper():
            search_patterns.append(raw)
    
    # Get curated list of known models for this make
    known_models = get_canonical_models_for_make(make)
    
    async with pool.acquire() as conn:
        # Get all model_ids for this make with sufficient test volume
        found_models = {}  # model -> test_count
        for pattern in search_patterns:
            rows = await conn.fetch("""
                SELECT model_id, SUM(total_tests) as test_count
                FROM mot_risk 
                WHERE model_id LIKE $1
                GROUP BY model_id
                HAVING SUM(total_tests) >= $2
            """, f"{pattern}%", MIN_TESTS_FOR_UI)
            
            for row in rows:
                base_model = extract_base_model(row['model_id'], make)
                if base_model and len(base_model) > 1:
                    # Keep track of highest test count for each base model
                    if base_model not in found_models or row['test_count'] > found_models[base_model]:
                        found_models[base_model] = row['test_count']
        
        # If we have a curated list, only return models from it that exist in data
        if known_models:
            result = [m for m in known_models if m in found_models]
            return sorted(result)  # Alphabetical order
        
        # For non-curated makes, return alphabetic models only (capped)
        clean = [m for m in found_models.keys() if len(m) >= 3 and m.isalpha()]
        return sorted(clean)[:30]  # Alphabetical order

async def get_risk(model_id: str, age_band: str, mileage_band: str) -> Optional[Dict]:
    """Get aggregated risk data for a vehicle, combining all variants."""
    pool = await get_pool()
    if not pool:
        return None  # Fallback to mock data
    
    async with pool.acquire() as conn:
        # model_id is now "MAKE MODEL" (e.g., "FORD FIESTA")
        # We need to find all variants and aggregate their risk
        
        # Search for all variants of this base model
        rows = await conn.fetch(
            """SELECT 
                SUM(total_tests) as total_tests,
                SUM(total_failures) as total_failures,
                SUM(failure_risk * total_tests) / NULLIF(SUM(total_tests), 0) as failure_risk,
                SUM(risk_brakes * total_tests) / NULLIF(SUM(total_tests), 0) as risk_brakes,
                SUM(risk_suspension * total_tests) / NULLIF(SUM(total_tests), 0) as risk_suspension,
                SUM(risk_tyres * total_tests) / NULLIF(SUM(total_tests), 0) as risk_tyres,
                SUM(risk_steering * total_tests) / NULLIF(SUM(total_tests), 0) as risk_steering,
                SUM(risk_visibility * total_tests) / NULLIF(SUM(total_tests), 0) as risk_visibility,
                SUM(risk_lamps_reflectors_and_electrical_equipment * total_tests) / NULLIF(SUM(total_tests), 0) as risk_lamps_reflectors_and_electrical_equipment,
                SUM(risk_body_chassis_structure * total_tests) / NULLIF(SUM(total_tests), 0) as risk_body_chassis_structure
               FROM mot_risk 
               WHERE model_id LIKE $1 
               AND age_band = $2 AND mileage_band = $3""",
            f"{model_id}%", age_band, mileage_band
        )
        
        if rows and rows[0]['total_tests']:
            result = {
                "Model_Id": model_id,
                "Age_Band": age_band,
                "Mileage_Band": mileage_band,
                "Total_Tests": int(rows[0]['total_tests']),
                "Total_Failures": int(rows[0]['total_failures']) if rows[0]['total_failures'] else 0,
                "Failure_Risk": float(rows[0]['failure_risk']) if rows[0]['failure_risk'] else 0.0,
                "Risk_Brakes": float(rows[0]['risk_brakes']) if rows[0]['risk_brakes'] else 0.0,
                "Risk_Suspension": float(rows[0]['risk_suspension']) if rows[0]['risk_suspension'] else 0.0,
                "Risk_Tyres": float(rows[0]['risk_tyres']) if rows[0]['risk_tyres'] else 0.0,
                "Risk_Steering": float(rows[0]['risk_steering']) if rows[0]['risk_steering'] else 0.0,
                "Risk_Visibility": float(rows[0]['risk_visibility']) if rows[0]['risk_visibility'] else 0.0,
                "Risk_Lamps_Reflectors_And_Electrical_Equipment": float(rows[0]['risk_lamps_reflectors_and_electrical_equipment']) if rows[0]['risk_lamps_reflectors_and_electrical_equipment'] else 0.0,
                "Risk_Body_Chassis_Structure": float(rows[0]['risk_body_chassis_structure']) if rows[0]['risk_body_chassis_structure'] else 0.0,
            }
            return result
        
        # No data for this specific band - try any band for this model
        exists = await conn.fetchrow(
            "SELECT 1 FROM mot_risk WHERE model_id LIKE $1 LIMIT 1",
            f"{model_id}%"
        )
        
        if not exists:
            return {"error": "not_found", "suggestion": None}
        
        # Model exists but specific band doesn't - return overall average
        avg_rows = await conn.fetch(
            """SELECT 
                SUM(total_tests) as total_tests,
                SUM(failure_risk * total_tests) / NULLIF(SUM(total_tests), 0) as avg_risk
               FROM mot_risk WHERE model_id LIKE $1""",
            f"{model_id}%"
        )
        
        return {
            "Model_Id": model_id,
            "Age_Band": age_band,
            "Mileage_Band": mileage_band,
            "note": "Exact age/mileage match not found. Returning model average.",
            "Total_Tests": int(avg_rows[0]['total_tests']) if avg_rows[0]['total_tests'] else 0,
            "Failure_Risk": float(avg_rows[0]['avg_risk']) if avg_rows[0]['avg_risk'] else 0.0
        }


# ============================================================================
# Lead Management Functions
# ============================================================================

async def save_lead(lead_data: Dict) -> Optional[str]:
    """
    Save a lead to the database.

    Args:
        lead_data: Dict containing:
            - email: str
            - postcode: str
            - lead_type: str (e.g., 'garage')
            - vehicle: dict with make, model, year, mileage
            - risk_data: dict with failure_risk, reliability_score, top_risks

    Returns:
        Lead ID (UUID string) on success, None on failure
    """
    pool = await get_pool()
    if not pool:
        logger.error("No database pool available for saving lead")
        return None

    try:
        import json

        vehicle = lead_data.get('vehicle', {})
        risk_data = lead_data.get('risk_data', {})
        top_risks = risk_data.get('top_risks', [])

        async with pool.acquire() as conn:
            result = await conn.fetchrow(
                """INSERT INTO leads (
                    email, postcode, name, phone, lead_type,
                    vehicle_make, vehicle_model, vehicle_year, vehicle_mileage,
                    failure_risk, reliability_score, top_risks
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb)
                RETURNING id""",
                lead_data.get('email'),
                lead_data.get('postcode'),
                lead_data.get('name'),
                lead_data.get('phone'),
                lead_data.get('lead_type', 'garage'),
                vehicle.get('make'),
                vehicle.get('model'),
                vehicle.get('year'),
                vehicle.get('mileage'),
                risk_data.get('failure_risk'),
                risk_data.get('reliability_score'),
                json.dumps(top_risks) if top_risks else '[]'
            )

            lead_id = str(result['id'])
            logger.info(f"Lead saved: {lead_data.get('postcode')} - {vehicle.get('make')} {vehicle.get('model')}")
            return lead_id

    except Exception as e:
        logger.error(f"Failed to save lead: {e}")
        return None


async def get_leads(
    limit: int = 50,
    offset: int = 0,
    since: Optional[str] = None
) -> Optional[List[Dict]]:
    """
    Get leads from the database (for admin access).

    Args:
        limit: Max number of leads to return
        offset: Number of leads to skip
        since: ISO date string to filter leads created after

    Returns:
        List of lead dicts, or None on failure
    """
    pool = await get_pool()
    if not pool:
        logger.error("No database pool available for getting leads")
        return None

    try:
        async with pool.acquire() as conn:
            if since:
                rows = await conn.fetch(
                    """SELECT id, email, postcode, lead_type,
                              vehicle_make, vehicle_model, vehicle_year, vehicle_mileage,
                              failure_risk, reliability_score, top_risks,
                              created_at, contacted_at, notes
                       FROM leads
                       WHERE created_at >= $1::timestamp
                       ORDER BY created_at DESC
                       LIMIT $2 OFFSET $3""",
                    since, limit, offset
                )
            else:
                rows = await conn.fetch(
                    """SELECT id, email, postcode, lead_type,
                              vehicle_make, vehicle_model, vehicle_year, vehicle_mileage,
                              failure_risk, reliability_score, top_risks,
                              created_at, contacted_at, notes
                       FROM leads
                       ORDER BY created_at DESC
                       LIMIT $1 OFFSET $2""",
                    limit, offset
                )

            leads = []
            for row in rows:
                lead = dict(row)
                # Convert UUID and datetime to strings
                lead['id'] = str(lead['id'])
                if lead['created_at']:
                    lead['created_at'] = lead['created_at'].isoformat()
                if lead['contacted_at']:
                    lead['contacted_at'] = lead['contacted_at'].isoformat()
                leads.append(lead)

            return leads

    except Exception as e:
        logger.error(f"Failed to get leads: {e}")
        return None


async def count_leads(since: Optional[str] = None) -> int:
    """Count total leads, optionally since a date."""
    pool = await get_pool()
    if not pool:
        return 0

    try:
        async with pool.acquire() as conn:
            if since:
                result = await conn.fetchrow(
                    "SELECT COUNT(*) as count FROM leads WHERE created_at >= $1::timestamp",
                    since
                )
            else:
                result = await conn.fetchrow("SELECT COUNT(*) as count FROM leads")
            return result['count']
    except Exception as e:
        logger.error(f"Failed to count leads: {e}")
        return 0

