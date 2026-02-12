"""
AutoSafe Database Module - PostgreSQL Connection
Uses DATABASE_URL environment variable from Railway.
"""
import os
import re
from typing import List, Dict, Optional


def _clamp_risk(value: float, min_val: float = 0.0, max_val: float = 1.0) -> float:
    """
    Clamp a risk value to a valid probability range.

    This provides defense-in-depth against corrupted data in the database.
    Even if the data pipeline produces invalid values (e.g., >1.0), this
    ensures the API never returns impossible probabilities.

    Args:
        value: The risk value to clamp
        min_val: Minimum valid value (default 0.0)
        max_val: Maximum valid value (default 1.0)

    Returns:
        Clamped value within [min_val, max_val]
    """
    if value is None:
        return 0.0
    return max(min_val, min(max_val, float(value)))

# Check if we have a database URL (support multiple variable name formats)
DATABASE_URL = (
    os.environ.get("DATABASE_URL") or
    os.environ.get("AutoSafe DB1") or
    os.environ.get("AutoSafe_DB1")
)

# PostgreSQL connection pool (lazy initialization)
_pool = None

import logging

logger = logging.getLogger(__name__)

# Minimum total tests required for a make/model to appear in UI dropdowns
# This filters out typos, garbage entries, and extremely rare vehicles
# while keeping all legitimate production cars (even rare ones have >100 tests)
# Configurable via environment variable for tuning without redeployment
MIN_TESTS_FOR_UI = int(os.environ.get("MIN_TESTS_FOR_UI", "100"))

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


async def is_postgres_available() -> bool:
    """
    Check if PostgreSQL is available and accepting connections.

    This is used to prevent silent fallback to SQLite for write operations
    which could cause data loss in production.

    Returns:
        True if PostgreSQL is connected and responding, False otherwise
    """
    if not DATABASE_URL:
        return False

    pool = await get_pool()
    if not pool:
        return False

    try:
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")
            return True
    except Exception as e:
        logger.error(f"PostgreSQL health check failed: {e}")
        return False

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
        return None  # PostgreSQL unavailable, caller handles fallback

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
        return None  # PostgreSQL unavailable, caller handles fallback

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

        # P0-5 fix: Use exact match OR variant match (model_id || ' %')
        # This prevents "FORD F" from matching "FORD FOCUS"
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
               WHERE (model_id = $1 OR model_id LIKE $1 || ' %')
               AND age_band = $2 AND mileage_band = $3""",
            model_id, age_band, mileage_band
        )
        
        if rows and rows[0]['total_tests'] is not None:
            # Apply sanity check clamping to all risk values to prevent invalid data
            # from corrupted database entries from propagating to the API
            result = {
                "Model_Id": model_id,
                "Age_Band": age_band,
                "Mileage_Band": mileage_band,
                "Total_Tests": int(rows[0]['total_tests']),
                "Total_Failures": int(rows[0]['total_failures']) if rows[0]['total_failures'] else 0,
                "Failure_Risk": _clamp_risk(rows[0]['failure_risk']),
                "Risk_Brakes": _clamp_risk(rows[0]['risk_brakes']),
                "Risk_Suspension": _clamp_risk(rows[0]['risk_suspension']),
                "Risk_Tyres": _clamp_risk(rows[0]['risk_tyres']),
                "Risk_Steering": _clamp_risk(rows[0]['risk_steering']),
                "Risk_Visibility": _clamp_risk(rows[0]['risk_visibility']),
                "Risk_Lamps_Reflectors_And_Electrical_Equipment": _clamp_risk(rows[0]['risk_lamps_reflectors_and_electrical_equipment']),
                "Risk_Body_Chassis_Structure": _clamp_risk(rows[0]['risk_body_chassis_structure']),
            }
            return result
        
        # No data for this specific band - try any band for this model
        # P0-5 fix: Use exact or variant match
        exists = await conn.fetchrow(
            "SELECT 1 FROM mot_risk WHERE (model_id = $1 OR model_id LIKE $1 || ' %') LIMIT 1",
            model_id
        )

        if not exists:
            return {"error": "not_found", "suggestion": None}

        # Model exists but specific band doesn't - return overall average
        avg_rows = await conn.fetch(
            """SELECT
                SUM(total_tests) as total_tests,
                SUM(failure_risk * total_tests) / NULLIF(SUM(total_tests), 0) as avg_risk
               FROM mot_risk WHERE (model_id = $1 OR model_id LIKE $1 || ' %')""",
            model_id
        )
        
        return {
            "Model_Id": model_id,
            "Age_Band": age_band,
            "Mileage_Band": mileage_band,
            "note": "Exact age/mileage match not found. Returning model average.",
            "Total_Tests": int(avg_rows[0]['total_tests']) if avg_rows[0]['total_tests'] else 0,
            "Failure_Risk": _clamp_risk(avg_rows[0]['avg_risk'])
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
        services_requested = lead_data.get('services_requested', [])
        consent_given = lead_data.get('consent_given', False)

        async with pool.acquire() as conn:
            result = await conn.fetchrow(
                """INSERT INTO leads (
                    email, postcode, name, phone, lead_type,
                    vehicle_make, vehicle_model, vehicle_year, vehicle_mileage,
                    failure_risk, reliability_score, top_risks, services_requested,
                    description, urgency, consent_given, consent_timestamp
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, $13::jsonb,
                          $14, $15, $16, CASE WHEN $16 THEN NOW() ELSE NULL END)
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
                json.dumps(top_risks) if top_risks else '[]',
                json.dumps(services_requested) if services_requested else '[]',
                lead_data.get('description'),
                lead_data.get('urgency'),
                consent_given
            )

            lead_id = str(result['id'])
            # Log lead saved with postcode (needed for ops) but no email/name/phone
            logger.info(f"Lead saved: id={lead_id} postcode={lead_data.get('postcode')} make={vehicle.get('make')} model={vehicle.get('model')}")
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
                              failure_risk, reliability_score, top_risks, services_requested,
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
                              failure_risk, reliability_score, top_risks, services_requested,
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


# ============================================================================
# Garage Management Functions
# ============================================================================

async def save_garage(garage_data: Dict) -> Optional[str]:
    """
    Save a garage to the database.

    Args:
        garage_data: Dict containing name, email, postcode, etc.

    Returns:
        Garage ID (UUID string) on success, None on failure
    """
    pool = await get_pool()
    if not pool:
        logger.error("No database pool available for saving garage")
        return None

    try:
        async with pool.acquire() as conn:
            result = await conn.fetchrow(
                """INSERT INTO garages (
                    name, contact_name, email, phone, postcode,
                    latitude, longitude, status, tier, source, notes
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                RETURNING id""",
                garage_data.get('name'),
                garage_data.get('contact_name'),
                garage_data.get('email'),
                garage_data.get('phone'),
                garage_data.get('postcode'),
                garage_data.get('latitude'),
                garage_data.get('longitude'),
                garage_data.get('status', 'active'),
                garage_data.get('tier', 'free'),
                garage_data.get('source'),
                garage_data.get('notes')
            )

            garage_id = str(result['id'])
            logger.info(f"Garage saved: {garage_data.get('name')} ({garage_data.get('postcode')})")
            return garage_id

    except Exception as e:
        logger.error(f"Failed to save garage: {e}")
        return None


async def get_garage_by_id(garage_id: str) -> Optional[Dict]:
    """Get a garage by ID."""
    pool = await get_pool()
    if not pool:
        return None

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT id, name, contact_name, email, phone, postcode,
                          latitude, longitude, status, tier,
                          leads_received, leads_converted, source, notes, created_at
                   FROM garages WHERE id = $1""",
                garage_id
            )

            if row:
                garage = dict(row)
                garage['id'] = str(garage['id'])
                if garage['created_at']:
                    garage['created_at'] = garage['created_at'].isoformat()
                return garage
            return None

    except Exception as e:
        logger.error(f"Failed to get garage: {e}")
        return None


async def get_all_garages(status: Optional[str] = None) -> List[Dict]:
    """Get all garages, optionally filtered by status."""
    pool = await get_pool()
    if not pool:
        return []

    try:
        async with pool.acquire() as conn:
            if status:
                rows = await conn.fetch(
                    """SELECT id, name, contact_name, email, phone, postcode,
                              latitude, longitude, status, tier,
                              leads_received, leads_converted, source, created_at
                       FROM garages WHERE status = $1
                       ORDER BY created_at DESC""",
                    status
                )
            else:
                rows = await conn.fetch(
                    """SELECT id, name, contact_name, email, phone, postcode,
                              latitude, longitude, status, tier,
                              leads_received, leads_converted, source, created_at
                       FROM garages ORDER BY created_at DESC"""
                )

            garages = []
            for row in rows:
                garage = dict(row)
                garage['id'] = str(garage['id'])
                if garage['created_at']:
                    garage['created_at'] = garage['created_at'].isoformat()
                garages.append(garage)

            return garages

    except Exception as e:
        logger.error(f"Failed to get garages: {e}")
        return []


async def get_garages_with_coordinates() -> List[Dict]:
    """Get all active garages that have coordinates set (for matching)."""
    pool = await get_pool()
    if not pool:
        return []

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, name, email, postcode, latitude, longitude, tier
                   FROM garages
                   WHERE status = 'active'
                     AND latitude IS NOT NULL
                     AND longitude IS NOT NULL
                   ORDER BY tier DESC"""
            )

            garages = []
            for row in rows:
                garages.append({
                    'id': str(row['id']),
                    'name': row['name'],
                    'email': row['email'],
                    'postcode': row['postcode'],
                    'latitude': float(row['latitude']),
                    'longitude': float(row['longitude']),
                    'tier': row['tier']
                })

            return garages

    except Exception as e:
        logger.error(f"Failed to get garages with coordinates: {e}")
        return []


async def update_garage(garage_id: str, updates: Dict) -> bool:
    """Update a garage's fields."""
    pool = await get_pool()
    if not pool:
        return False

    # Build dynamic update query
    allowed_fields = ['name', 'contact_name', 'email', 'phone', 'postcode',
                      'latitude', 'longitude', 'status', 'tier', 'notes']

    # Defense-in-depth: validate field names match expected pattern (lowercase + underscore only)
    # This prevents SQL injection even if allowed_fields is compromised in future changes
    safe_field_pattern = re.compile(r'^[a-z_]+$')

    set_clauses = []
    values = []
    param_num = 1

    for field in allowed_fields:
        if field in updates:
            # Extra safety check: ensure field name is safe for SQL
            if not safe_field_pattern.match(field):
                logger.error(f"Invalid field name rejected: {field}")
                continue
            set_clauses.append(f"{field} = ${param_num}")
            values.append(updates[field])
            param_num += 1

    if not set_clauses:
        return False

    values.append(garage_id)

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                f"UPDATE garages SET {', '.join(set_clauses)} WHERE id = ${param_num}",
                *values
            )
            return True

    except Exception as e:
        logger.error(f"Failed to update garage: {e}")
        return False


async def increment_garage_leads_received(garage_id: str) -> bool:
    """Increment the leads_received counter for a garage."""
    pool = await get_pool()
    if not pool:
        return False

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE garages SET leads_received = leads_received + 1 WHERE id = $1",
                garage_id
            )
            return True
    except Exception as e:
        logger.error(f"Failed to increment garage leads: {e}")
        return False


# ============================================================================
# Lead Assignment Functions
# ============================================================================

async def create_lead_assignment(
    lead_id: str,
    garage_id: str,
    distance_miles: float
) -> Optional[str]:
    """Create a lead assignment record."""
    pool = await get_pool()
    if not pool:
        return None

    try:
        async with pool.acquire() as conn:
            result = await conn.fetchrow(
                """INSERT INTO lead_assignments (lead_id, garage_id, distance_miles, email_sent_at)
                   VALUES ($1, $2, $3, NOW())
                   RETURNING id""",
                lead_id, garage_id, distance_miles
            )
            return str(result['id'])
    except Exception as e:
        logger.error(f"Failed to create lead assignment: {e}")
        return None


async def update_lead_assignment_outcome(assignment_id: str, outcome: str) -> bool:
    """Update the outcome of a lead assignment."""
    pool = await get_pool()
    if not pool:
        return False

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE lead_assignments
                   SET outcome = $1, outcome_reported_at = NOW()
                   WHERE id = $2""",
                outcome, assignment_id
            )

            # If outcome is 'won', increment garage's leads_converted
            if outcome == 'won':
                await conn.execute(
                    """UPDATE garages SET leads_converted = leads_converted + 1
                       WHERE id = (SELECT garage_id FROM lead_assignments WHERE id = $1)""",
                    assignment_id
                )

            return True
    except Exception as e:
        logger.error(f"Failed to update lead assignment outcome: {e}")
        return False


async def get_lead_by_id(lead_id: str) -> Optional[Dict]:
    """Get a lead by ID (for distribution)."""
    pool = await get_pool()
    if not pool:
        return None

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT id, email, postcode, name, phone,
                          vehicle_make, vehicle_model, vehicle_year, vehicle_mileage,
                          failure_risk, reliability_score, top_risks,
                          distribution_status, created_at
                   FROM leads WHERE id = $1""",
                lead_id
            )

            if row:
                lead = dict(row)
                lead['id'] = str(lead['id'])
                if lead['created_at']:
                    lead['created_at'] = lead['created_at'].isoformat()
                return lead
            return None

    except Exception as e:
        logger.error(f"Failed to get lead: {e}")
        return None


async def update_lead_distribution_status(lead_id: str, status: str) -> bool:
    """Update lead distribution status."""
    pool = await get_pool()
    if not pool:
        return False

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE leads
                   SET distribution_status = $1, distributed_at = NOW()
                   WHERE id = $2""",
                status, lead_id
            )
            return True
    except Exception as e:
        logger.error(f"Failed to update lead distribution status: {e}")
        return False


async def get_lead_assignment_by_id(assignment_id: str) -> Optional[Dict]:
    """Get a lead assignment by ID (for outcome reporting)."""
    pool = await get_pool()
    if not pool:
        return None

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT la.id, la.lead_id, la.garage_id, la.distance_miles,
                          la.email_sent_at, la.outcome, la.outcome_reported_at,
                          g.name as garage_name,
                          l.vehicle_make, l.vehicle_model, l.vehicle_year
                   FROM lead_assignments la
                   JOIN garages g ON la.garage_id = g.id
                   JOIN leads l ON la.lead_id = l.id
                   WHERE la.id = $1""",
                assignment_id
            )

            if row:
                assignment = dict(row)
                assignment['id'] = str(assignment['id'])
                assignment['lead_id'] = str(assignment['lead_id'])
                assignment['garage_id'] = str(assignment['garage_id'])
                if assignment['email_sent_at']:
                    assignment['email_sent_at'] = assignment['email_sent_at'].isoformat()
                if assignment['outcome_reported_at']:
                    assignment['outcome_reported_at'] = assignment['outcome_reported_at'].isoformat()
                return assignment
            return None

    except Exception as e:
        logger.error(f"Failed to get lead assignment: {e}")
        return None


# ============================================================================
# Risk Check Logging Functions
# ============================================================================

async def save_risk_check(risk_data: Dict) -> Optional[str]:
    """
    Save a risk check to the database for model training data.

    Args:
        risk_data: Dict containing:
            - registration: str (VRM)
            - postcode: str (optional)
            - vehicle_make, vehicle_model, vehicle_year, vehicle_fuel_type
            - mileage: int
            - last_mot_date, last_mot_result
            - failure_risk: float
            - confidence_level: str
            - risk_components: dict
            - repair_cost_estimate: dict
            - model_version: str
            - prediction_source: str ('dvsa', 'lookup', 'fallback')
            - is_dvsa_data: bool

    Returns:
        Risk check ID (UUID string) on success, None on failure
    """

    pool = await get_pool()
    if not pool:
        logger.error("No database pool available for saving risk check")
        return None

    try:
        import json
        async with pool.acquire() as conn:
            result = await conn.fetchrow(
                """INSERT INTO risk_checks (
                    registration, postcode,
                    vehicle_make, vehicle_model, vehicle_year, vehicle_fuel_type,
                    mileage, last_mot_date, last_mot_result,
                    failure_risk, confidence_level, risk_components, repair_cost_estimate,
                    model_version, prediction_source, is_dvsa_data
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, $13::jsonb, $14, $15, $16)
                RETURNING id""",
                risk_data.get('registration'),
                risk_data.get('postcode'),
                risk_data.get('vehicle_make'),
                risk_data.get('vehicle_model'),
                risk_data.get('vehicle_year'),
                risk_data.get('vehicle_fuel_type'),
                risk_data.get('mileage'),
                risk_data.get('last_mot_date'),
                risk_data.get('last_mot_result'),
                risk_data.get('failure_risk'),
                risk_data.get('confidence_level'),
                json.dumps(risk_data.get('risk_components', {})),
                json.dumps(risk_data.get('repair_cost_estimate', {})),
                risk_data.get('model_version'),
                risk_data.get('prediction_source'),
                risk_data.get('is_dvsa_data', False)
            )

            risk_check_id = str(result['id'])
            logger.info(f"Risk check logged: postcode={risk_data.get('postcode')} make={risk_data.get('vehicle_make')} model={risk_data.get('vehicle_model')}")
            return risk_check_id

    except Exception as e:
        logger.error(f"Failed to save risk check: {e}")
        # Backup to local file so data is never lost
        _backup_risk_check_to_file(risk_data)
        return None


def _backup_risk_check_to_file(risk_data: Dict):
    """Append risk check to a local JSONL file as backup when DB save fails."""
    try:
        import json
        from datetime import datetime, date
        backup_path = os.path.join(os.path.dirname(__file__), "risk_checks_backup.jsonl")
        entry = {**risk_data, "backup_timestamp": datetime.now().isoformat()}
        for k, v in entry.items():
            if isinstance(v, (datetime, date)):
                entry[k] = v.isoformat()
        with open(backup_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
        logger.info(f"Risk check backed up to file: {risk_data.get('registration')}")
    except Exception as e:
        logger.error(f"Failed to backup risk check to file: {e}")


# ============================================================================
# MOT Reminder + Report Email Functions
# ============================================================================

async def save_mot_reminder(data: Dict) -> Dict:
    """
    Save an MOT reminder signup. Checks for duplicates (same email + registration).

    Returns:
        Dict with 'success', 'already_subscribed', and optionally 'lead_id'
    """
    pool = await get_pool()
    if not pool:
        logger.error("No database pool available for saving MOT reminder")
        return {"success": False, "already_subscribed": False}

    try:
        import json
        email = data.get('email', '').lower().strip()
        registration = data.get('registration', '').upper().strip()

        async with pool.acquire() as conn:
            # Check for duplicate
            existing = await conn.fetchrow(
                """SELECT id FROM leads
                   WHERE email = $1 AND registration = $2 AND lead_type = 'mot_reminder'""",
                email, registration
            )

            if existing:
                return {"success": True, "already_subscribed": True, "lead_id": str(existing['id'])}

            # Parse mot_expiry_date to date object if provided
            mot_expiry = None
            if data.get('mot_expiry_date'):
                from datetime import date as date_type
                try:
                    parts = data['mot_expiry_date'][:10].split('-')
                    mot_expiry = date_type(int(parts[0]), int(parts[1]), int(parts[2]))
                except (ValueError, IndexError):
                    pass

            result = await conn.fetchrow(
                """INSERT INTO leads (
                    email, postcode, lead_type, registration, mot_expiry_date,
                    vehicle_make, vehicle_model, vehicle_year,
                    failure_risk, consent_given, consent_timestamp
                ) VALUES ($1, $2, 'mot_reminder', $3, $4, $5, $6, $7, $8, TRUE, NOW())
                RETURNING id""",
                email,
                data.get('postcode', '').upper().strip(),
                registration,
                mot_expiry,
                data.get('vehicle_make'),
                data.get('vehicle_model'),
                data.get('vehicle_year'),
                data.get('failure_risk'),
            )

            lead_id = str(result['id'])
            logger.info(f"MOT reminder saved: id={lead_id} registration={registration}")
            return {"success": True, "already_subscribed": False, "lead_id": lead_id}

    except Exception as e:
        logger.error(f"Failed to save MOT reminder: {e}")
        return {"success": False, "already_subscribed": False}


async def save_report_email_lead(data: Dict) -> Optional[str]:
    """
    Save a report email lead to the database.

    Returns:
        Lead ID on success, None on failure
    """
    pool = await get_pool()
    if not pool:
        logger.error("No database pool available for saving report email lead")
        return None

    try:
        import json
        email = data.get('email', '').lower().strip()

        async with pool.acquire() as conn:
            result = await conn.fetchrow(
                """INSERT INTO leads (
                    email, postcode, lead_type, registration,
                    vehicle_make, vehicle_model, vehicle_year,
                    failure_risk, reliability_score,
                    top_risks, consent_given, consent_timestamp
                ) VALUES ($1, $2, 'report_email', $3, $4, $5, $6, $7, $8, $9::jsonb, TRUE, NOW())
                RETURNING id""",
                email,
                data.get('postcode', '').upper().strip(),
                data.get('registration', '').upper().strip(),
                data.get('vehicle_make'),
                data.get('vehicle_model'),
                data.get('vehicle_year'),
                data.get('failure_risk'),
                data.get('reliability_score'),
                json.dumps(data.get('common_faults', [])),
            )

            lead_id = str(result['id'])
            logger.info(f"Report email lead saved: id={lead_id}")
            return lead_id

    except Exception as e:
        logger.error(f"Failed to save report email lead: {e}")
        return None


async def get_risk_check_stats() -> Dict:
    """
    Get total and monthly risk check counts for the public stats endpoint.

    Returns:
        Dict with 'total_checks' and 'checks_this_month'
    """
    pool = await get_pool()
    if not pool:
        return {"total_checks": 0, "checks_this_month": 0}

    try:
        async with pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM risk_checks")
            monthly = await conn.fetchval(
                """SELECT COUNT(*) FROM risk_checks
                   WHERE created_at >= date_trunc('month', CURRENT_DATE)"""
            )
            return {
                "total_checks": total or 0,
                "checks_this_month": monthly or 0,
            }
    except Exception as e:
        logger.error(f"Failed to get risk check stats: {e}")
        return {"total_checks": 0, "checks_this_month": 0}
