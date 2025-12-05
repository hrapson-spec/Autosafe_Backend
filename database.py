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

async def get_pool():
    """Get or create the connection pool."""
    global _pool
    if _pool is None and DATABASE_URL:
        import asyncpg
        # Railway uses postgres:// but asyncpg needs postgresql://
        db_url = DATABASE_URL.replace("postgres://", "postgresql://")
        _pool = await asyncpg.create_pool(db_url, min_size=1, max_size=10)
    return _pool

async def close_pool():
    """Close the connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None

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
    """Return a list of all unique vehicle makes."""
    pool = await get_pool()
    if not pool:
        return None  # Fallback to mock data
    
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT DISTINCT model_id FROM mot_risk")
        makes = set()
        for row in rows:
            parts = row['model_id'].split(' ', 1)
            if len(parts) > 0:
                makes.add(parts[0])
        return sorted(list(makes))

async def get_models(make: str) -> List[str]:
    """Return a list of models for a given make."""
    pool = await get_pool()
    if not pool:
        return None  # Fallback to mock data
    
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT model_id FROM mot_risk WHERE model_id LIKE $1",
            f"{make.upper()}%"
        )
        return sorted([row['model_id'] for row in rows])

async def get_risk(model_id: str, age_band: str, mileage_band: str) -> Optional[Dict]:
    """Get risk data for a specific vehicle configuration."""
    pool = await get_pool()
    if not pool:
        return None  # Fallback to mock data
    
    async with pool.acquire() as conn:
        # Try exact match first
        row = await conn.fetchrow(
            """SELECT * FROM mot_risk 
               WHERE model_id = $1 AND age_band = $2 AND mileage_band = $3""",
            model_id, age_band, mileage_band
        )
        
        if row:
            return normalize_columns(dict(row))
        
        # Check if model exists at all
        exists = await conn.fetchrow(
            "SELECT 1 FROM mot_risk WHERE model_id = $1 LIMIT 1",
            model_id
        )
        
        if not exists:
            # Try to find a suggestion
            suggestion = await conn.fetchrow(
                "SELECT DISTINCT model_id FROM mot_risk WHERE model_id LIKE $1 LIMIT 1",
                f"%{model_id.split()[-1]}%"  # Search by last word (model name)
            )
            return {"error": "not_found", "suggestion": suggestion['model_id'] if suggestion else None}
        
        # Model exists but specific band doesn't - return average
        avg_row = await conn.fetchrow(
            "SELECT AVG(failure_risk) as avg_risk FROM mot_risk WHERE model_id = $1",
            model_id
        )
        
        return {
            "model_id": model_id,
            "age_band": age_band,
            "mileage_band": mileage_band,
            "note": "Exact age/mileage match not found. Returning model average.",
            "Failure_Risk": float(avg_row['avg_risk']) if avg_row['avg_risk'] else 0.0
        }
