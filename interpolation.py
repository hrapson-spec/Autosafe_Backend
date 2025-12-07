"""
Risk Interpolation Module

Implements linear interpolation between mileage/age bucket centers
to eliminate artificial "cliff" discontinuities at bucket boundaries.

Uses pre-calculated mass centers (weighted mean mileage per bucket)
rather than geometric midpoints, as recommended by the expert.
"""

from typing import Dict, List, Tuple, Optional
import numpy as np


# Mileage bucket definitions with empirically-derived mass centers
# These should be updated from actual data when pipeline runs
# Format: bucket_name -> (lower_bound, upper_bound, mass_center)
MILEAGE_BUCKETS: Dict[str, Tuple[int, int, int]] = {
    "0-30k": (0, 30000, 22000),       # Mass center ~22k (end of lease cluster)
    "30k-60k": (30000, 60000, 45000), # Mass center ~45k (geometric)
    "60k-100k": (60000, 100000, 78000),  # Mass center ~78k
    "100k+": (100000, 500000, 118000),   # Mass center ~118k (right skew)
}

# Age bucket definitions with mass centers
AGE_BUCKETS: Dict[str, Tuple[int, int, float]] = {
    "0-3": (0, 3, 2.0),     # First MOT at 3 years, so average ~2
    "3-5": (3, 6, 4.2),     # Slight skew toward 3-4 years
    "6-10": (6, 11, 8.0),   # Approximately geometric
    "10-15": (11, 16, 12.5),  # Approximately geometric
    "15+": (16, 40, 19.0),  # Right skew, most are 16-22
}

# Ordered bucket names for interpolation
MILEAGE_ORDER = ["0-30k", "30k-60k", "60k-100k", "100k+"]
AGE_ORDER = ["0-3", "3-5", "6-10", "10-15", "15+"]


def get_mileage_bucket(mileage: int) -> str:
    """Get the mileage bucket for a given mileage value."""
    if mileage < 0:
        return "0-30k"
    if mileage < 30000:
        return "0-30k"
    if mileage < 60000:
        return "30k-60k"
    if mileage < 100000:
        return "60k-100k"
    return "100k+"


def get_age_bucket(age: float) -> str:
    """Get the age bucket for a given age in years."""
    if age < 3:
        return "0-3"
    if age < 6:
        return "3-5"
    if age < 11:
        return "6-10"
    if age < 16:
        return "10-15"
    return "15+"


def interpolate_risk(
    actual_value: float,
    value_type: str,  # "mileage" or "age"
    bucket_risks: Dict[str, float]
) -> float:
    """
    Interpolate risk between bucket centers.
    
    Instead of returning the risk for the bucket the value falls into,
    this linearly interpolates between adjacent bucket centers.
    
    Args:
        actual_value: The actual mileage or age
        value_type: "mileage" or "age"
        bucket_risks: Dict mapping bucket names to risk values
        
    Returns:
        Interpolated risk value
    """
    if value_type == "mileage":
        buckets = MILEAGE_BUCKETS
        order = MILEAGE_ORDER
        get_bucket = get_mileage_bucket
    else:
        buckets = AGE_BUCKETS
        order = AGE_ORDER
        get_bucket = get_age_bucket
    
    # Find current bucket
    current_bucket = get_bucket(actual_value)
    current_idx = order.index(current_bucket) if current_bucket in order else 0
    
    # Get current bucket's mass center and risk
    _, _, current_center = buckets.get(current_bucket, (0, 0, actual_value))
    current_risk = bucket_risks.get(current_bucket, 0.0)
    
    # If we only have one bucket's data, return it directly
    if len(bucket_risks) <= 1:
        return current_risk
    
    # Determine if we interpolate with previous or next bucket
    if actual_value <= current_center:
        # Interpolate with previous bucket
        if current_idx > 0:
            prev_bucket = order[current_idx - 1]
            _, _, prev_center = buckets.get(prev_bucket, (0, 0, 0))
            prev_risk = bucket_risks.get(prev_bucket, current_risk)
            
            # Linear interpolation
            if current_center != prev_center:
                t = (actual_value - prev_center) / (current_center - prev_center)
                t = max(0.0, min(1.0, t))  # Clamp to [0, 1]
                return prev_risk + t * (current_risk - prev_risk)
    else:
        # Interpolate with next bucket
        if current_idx < len(order) - 1:
            next_bucket = order[current_idx + 1]
            _, _, next_center = buckets.get(next_bucket, (0, 0, 999999))
            next_risk = bucket_risks.get(next_bucket, current_risk)
            
            # Linear interpolation
            if next_center != current_center:
                t = (actual_value - current_center) / (next_center - current_center)
                t = max(0.0, min(1.0, t))  # Clamp to [0, 1]
                return current_risk + t * (next_risk - current_risk)
    
    # Fallback: return current bucket risk
    return current_risk


def interpolate_all_risks(
    actual_mileage: int,
    actual_age: float,
    bucket_data: Dict[str, Dict[str, float]]
) -> Dict[str, float]:
    """
    Interpolate all risk fields based on mileage.
    
    Args:
        actual_mileage: The actual mileage
        actual_age: The actual age in years
        bucket_data: Dict with bucket names as keys, containing risk fields
            e.g., {"30k-60k": {"Failure_Risk": 0.15, "Risk_Brakes": 0.04}}
        
    Returns:
        Dict with interpolated risk values
    """
    # For now, interpolate based on mileage only (primary continuous variable)
    # Age is less continuous in MOT data (tests are annual)
    
    current_bucket = get_mileage_bucket(actual_mileage)
    
    if current_bucket not in bucket_data or len(bucket_data) < 2:
        # Not enough data for interpolation
        return bucket_data.get(current_bucket, {})
    
    # Get all risk keys
    risk_keys = [k for k in bucket_data[current_bucket].keys() 
                 if k.startswith("Risk_") or k == "Failure_Risk"]
    
    result = dict(bucket_data[current_bucket])  # Copy non-risk fields
    
    # Interpolate each risk field
    for risk_key in risk_keys:
        bucket_risks = {
            bucket: data.get(risk_key, 0.0) 
            for bucket, data in bucket_data.items()
        }
        result[risk_key] = interpolate_risk(actual_mileage, "mileage", bucket_risks)
    
    return result


def update_mass_centers(
    mileage_centers: Dict[str, float],
    age_centers: Dict[str, float]
) -> None:
    """
    Update the mass centers with empirically calculated values.
    Call this after processing raw data to update the module's constants.
    
    Args:
        mileage_centers: Dict mapping bucket name to mean mileage
        age_centers: Dict mapping bucket name to mean age
    """
    global MILEAGE_BUCKETS, AGE_BUCKETS
    
    for bucket, center in mileage_centers.items():
        if bucket in MILEAGE_BUCKETS:
            low, high, _ = MILEAGE_BUCKETS[bucket]
            MILEAGE_BUCKETS[bucket] = (low, high, int(center))
    
    for bucket, center in age_centers.items():
        if bucket in AGE_BUCKETS:
            low, high, _ = AGE_BUCKETS[bucket]
            AGE_BUCKETS[bucket] = (low, high, float(center))


if __name__ == "__main__":
    # Demo: show interpolation in action
    print("=" * 60)
    print("RISK INTERPOLATION DEMO")
    print("=" * 60)
    
    # Mock bucket risks
    bucket_risks = {
        "0-30k": 0.12,
        "30k-60k": 0.18,
        "60k-100k": 0.25,
        "100k+": 0.32,
    }
    
    print("\nBucket risks (without interpolation):")
    for bucket, risk in bucket_risks.items():
        center = MILEAGE_BUCKETS[bucket][2]
        print(f"  {bucket} (center={center:,}): {risk:.1%}")
    
    print("\nInterpolated risks at various mileages:")
    test_mileages = [25000, 29000, 31000, 45000, 59000, 61000, 75000, 99000, 101000]
    
    for m in test_mileages:
        bucket = get_mileage_bucket(m)
        raw_risk = bucket_risks[bucket]
        interp_risk = interpolate_risk(m, "mileage", bucket_risks)
        diff = interp_risk - raw_risk
        
        print(f"  {m:,} miles: bucket={bucket}, raw={raw_risk:.1%}, interp={interp_risk:.1%} ({diff:+.1%})")
    
    print("\n" + "=" * 60)
    print("Cliff eliminated at 59k→61k boundary:")
    print(f"  59,000: {interpolate_risk(59000, 'mileage', bucket_risks):.2%}")
    print(f"  60,000: {interpolate_risk(60000, 'mileage', bucket_risks):.2%}")
    print(f"  61,000: {interpolate_risk(61000, 'mileage', bucket_risks):.2%}")
    print("  (Now smooth transition instead of 18%→25% cliff)")
