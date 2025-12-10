"""
Database Interpolation Functions

Additional database functions that support:
- Fix A: Bucket Cliff elimination via linear interpolation
- Fix C: Trim Level support via variant-specific queries
"""

from typing import Dict, List, Optional


async def get_risk_with_interpolation(
    model_id: str, 
    age_band: str, 
    actual_mileage: int,
    variant: str = None
) -> Optional[Dict]:
    """
    Get risk data with linear interpolation across mileage bands.
    
    This addresses the "Bucket Cliff" problem by fetching data for all
    mileage bands and interpolating based on actual mileage.
    
    Args:
        model_id: Base model ID (e.g., "FORD FIESTA")
        age_band: Age band string
        actual_mileage: The actual mileage value (not bucketed)
        variant: Optional full variant name for precise lookup (Trim Level fix)
    
    Returns:
        Dict with interpolated risk values
    """
    from database import get_pool
    from interpolation import get_mileage_bucket, interpolate_risk
    
    pool = await get_pool()
    if not pool:
        return None
    
    # Determine search pattern based on variant
    if variant:
        search_pattern = variant
        use_like = False
    else:
        search_pattern = f"{model_id}%"
        use_like = True
    
    async with pool.acquire() as conn:
        # Fetch data for all mileage bands
        if use_like:
            query = """
                SELECT 
                    mileage_band,
                    SUM(total_tests) as total_tests,
                    SUM(total_failures) as total_failures,
                    SUM(failure_risk * total_tests) / NULLIF(SUM(total_tests), 0) as failure_risk,
                    SUM(risk_brakes * total_tests) / NULLIF(SUM(total_tests), 0) as risk_brakes,
                    SUM(risk_suspension * total_tests) / NULLIF(SUM(total_tests), 0) as risk_suspension,
                    SUM(risk_tyres * total_tests) / NULLIF(SUM(total_tests), 0) as risk_tyres,
                    SUM(risk_steering * total_tests) / NULLIF(SUM(total_tests), 0) as risk_steering,
                    SUM(risk_visibility * total_tests) / NULLIF(SUM(total_tests), 0) as risk_visibility,
                    SUM(risk_body_chassis_structure * total_tests) / NULLIF(SUM(total_tests), 0) as risk_body
                FROM mot_risk 
                WHERE model_id LIKE $1 AND age_band = $2
                GROUP BY mileage_band
            """
            rows = await conn.fetch(query, search_pattern, age_band)
        else:
            query = """
                SELECT 
                    mileage_band, total_tests, total_failures, failure_risk,
                    risk_brakes, risk_suspension, risk_tyres, risk_steering,
                    risk_visibility, risk_body_chassis_structure as risk_body
                FROM mot_risk 
                WHERE model_id = $1 AND age_band = $2
            """
            rows = await conn.fetch(query, search_pattern, age_band)
        
        if not rows:
            return {"error": "not_found", "suggestion": None}
        
        # Build bucket data for interpolation
        bucket_data = {}
        total_tests_all = 0
        total_failures_all = 0
        
        for row in rows:
            band = row['mileage_band']
            if row['total_tests']:
                total_tests_all += int(row['total_tests'])
                total_failures_all += int(row['total_failures']) if row['total_failures'] else 0
                bucket_data[band] = {
                    "Failure_Risk": float(row['failure_risk']) if row['failure_risk'] else 0.0,
                    "Risk_Brakes": float(row['risk_brakes']) if row['risk_brakes'] else 0.0,
                    "Risk_Suspension": float(row['risk_suspension']) if row['risk_suspension'] else 0.0,
                    "Risk_Tyres": float(row['risk_tyres']) if row['risk_tyres'] else 0.0,
                    "Risk_Steering": float(row['risk_steering']) if row['risk_steering'] else 0.0,
                    "Risk_Visibility": float(row['risk_visibility']) if row['risk_visibility'] else 0.0,
                    "Risk_Body": float(row['risk_body']) if row['risk_body'] else 0.0,
                }
        
        current_bucket = get_mileage_bucket(actual_mileage)
        
        # Interpolate each risk field
        risk_fields = ["Failure_Risk", "Risk_Brakes", "Risk_Suspension", "Risk_Tyres",
                       "Risk_Steering", "Risk_Visibility", "Risk_Body"]
        
        result = {
            "Model_Id": variant if variant else model_id,
            "Age_Band": age_band,
            "Mileage_Band": current_bucket,
            "Actual_Mileage": actual_mileage,
            "Total_Tests": total_tests_all,
            "Total_Failures": total_failures_all,
            "Interpolated": len(bucket_data) > 1,
        }
        
        for field in risk_fields:
            bucket_risks = {band: data.get(field, 0.0) for band, data in bucket_data.items()}
            if bucket_risks:
                result[field] = round(interpolate_risk(actual_mileage, "mileage", bucket_risks), 6)
            else:
                result[field] = 0.0
        
        # Rename to match expected format
        if "Risk_Body" in result:
            result["Risk_Body_Chassis_Structure"] = result.pop("Risk_Body")
        
        return result


async def get_variant_list(make: str, model: str) -> List[Dict]:
    """
    Get list of available variants for a specific make/model.
    Supports Trim Level feature by showing all variants with test counts.
    """
    from database import get_pool
    
    pool = await get_pool()
    if not pool:
        return []
    
    base_model_id = f"{make.upper()} {model.upper()}"
    
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT model_id, SUM(total_tests) as tests
            FROM mot_risk 
            WHERE model_id LIKE $1
            GROUP BY model_id
            ORDER BY tests DESC
            LIMIT 20
            """,
            f"{base_model_id}%"
        )
        
        return [{"variant": row['model_id'], "total_tests": int(row['tests'])} for row in rows]
