"""
AutoSafe Repair Cost Estimation Module

Provides indicative repair cost estimates based on MOT failure categories.
Costs are based on typical UK repair prices and should be treated as estimates only.
"""

from typing import Dict, Optional

# Static repair cost lookup table
# Prices in GBP, based on typical UK garage rates (2024)
# Source: Industry averages from WhoCanFixMyCar, ClickMechanic, and AA data
REPAIR_COSTS = {
    "Brakes": {
        "min": 80,
        "max": 350,
        "typical": 180,
        "description": "Brake pads, discs, or caliper repairs",
        "common_repairs": ["Brake pad replacement", "Brake disc replacement", "Brake fluid change"]
    },
    "Suspension": {
        "min": 150,
        "max": 600,
        "typical": 350,
        "description": "Shock absorbers, springs, or bushings",
        "common_repairs": ["Shock absorber replacement", "Coil spring replacement", "Drop link replacement"]
    },
    "Tyres": {
        "min": 60,
        "max": 200,
        "typical": 100,
        "description": "Per tyre replacement",
        "common_repairs": ["Tyre replacement", "Wheel alignment", "Puncture repair"]
    },
    "Steering": {
        "min": 100,
        "max": 500,
        "typical": 250,
        "description": "Steering rack, track rods, or power steering",
        "common_repairs": ["Track rod end replacement", "Steering rack repair", "Power steering pump"]
    },
    "Visibility": {
        "min": 20,
        "max": 150,
        "typical": 60,
        "description": "Wipers, mirrors, or windscreen",
        "common_repairs": ["Wiper blade replacement", "Wing mirror replacement", "Windscreen chip repair"]
    },
    "Lamps_Reflectors_And_Electrical_Equipment": {
        "min": 30,
        "max": 200,
        "typical": 80,
        "description": "Bulbs, headlights, or electrical faults",
        "common_repairs": ["Bulb replacement", "Headlight unit replacement", "Battery replacement"]
    },
    "Body_Chassis_Structure": {
        "min": 200,
        "max": 1000,
        "typical": 450,
        "description": "Structural repairs, rust treatment, or exhaust",
        "common_repairs": ["Exhaust repair", "Rust treatment", "Subframe repair"]
    }
}

# Age adjustment factor: older vehicles typically cost more to repair
# Due to part availability, corrosion, and complexity
AGE_ADJUSTMENT_THRESHOLD = 10  # years
AGE_ADJUSTMENT_FACTOR = 0.15   # 15% increase for vehicles over threshold


def normalise_component_name(component: str) -> str:
    """
    Normalise component name to match REPAIR_COSTS keys.
    Handles variations from API responses.
    """
    # Remove 'Risk_' prefix if present
    if component.startswith("Risk_"):
        component = component[5:]
    
    # Replace spaces with underscores
    component = component.replace(" ", "_")
    
    return component


def get_repair_estimate(component: str, vehicle_age: int = 0) -> Optional[Dict]:
    """
    Get indicative repair cost estimate for a component.
    
    Args:
        component: Component name (e.g., 'Brakes', 'Suspension')
        vehicle_age: Age of vehicle in years (for cost adjustment)
    
    Returns:
        Dict with min, max, typical costs and description, or None if unknown
    """
    normalised = normalise_component_name(component)
    
    if normalised not in REPAIR_COSTS:
        return None
    
    base_costs = REPAIR_COSTS[normalised].copy()
    
    # Apply age adjustment for older vehicles
    if vehicle_age >= AGE_ADJUSTMENT_THRESHOLD:
        adjustment = 1 + AGE_ADJUSTMENT_FACTOR
        base_costs["min"] = round(base_costs["min"] * adjustment)
        base_costs["max"] = round(base_costs["max"] * adjustment)
        base_costs["typical"] = round(base_costs["typical"] * adjustment)
        base_costs["age_adjusted"] = True
    else:
        base_costs["age_adjusted"] = False
    
    base_costs["component"] = normalised
    return base_costs


def get_all_repair_estimates(risk_data: Dict, vehicle_age: int = 0, threshold: float = 0.05) -> list:
    """
    Get repair estimates for all at-risk components from a risk API response.
    
    Args:
        risk_data: Response from /api/risk endpoint
        vehicle_age: Age of vehicle in years
        threshold: Minimum risk value to include (default 5%)
    
    Returns:
        List of repair estimates for components above threshold, sorted by risk
    """
    estimates = []
    
    for key, value in risk_data.items():
        if key.startswith("Risk_") and not key.endswith("_CI_Lower") and not key.endswith("_CI_Upper"):
            if value and value >= threshold:
                component = key.replace("Risk_", "")
                estimate = get_repair_estimate(component, vehicle_age)
                if estimate:
                    estimate["risk_value"] = round(value, 4)
                    estimate["risk_percentage"] = f"{value * 100:.1f}%"
                    estimates.append(estimate)
    
    # Sort by risk value descending
    estimates.sort(key=lambda x: x["risk_value"], reverse=True)
    return estimates


def format_cost_range(estimate: Dict) -> str:
    """Format cost estimate as human-readable string."""
    if not estimate:
        return "Cost unknown"
    
    return f"£{estimate['min']} - £{estimate['max']} (typical: £{estimate['typical']})"


def calculate_expected_repair_cost(risk_data: Dict) -> Optional[Dict]:
    """
    Calculate expected repair cost if the vehicle fails its MOT.
    
    Uses the formula: E[cost|fail] = Σ(risk_i × cost_mid_i) / p_fail
    
    Range calculation:
    - fail_mid = sum of (risk_i × typical_cost_i) / overall_fail_probability  
    - fail_min = max(0.6 × fail_mid, £150)  # Floor to avoid absurdly low values
    - fail_max = 1.7 × fail_mid  # Fat range to cover multi-part failures
    
    Args:
        risk_data: Response from /api/risk endpoint containing Risk_* fields
        
    Returns:
        Dict with cost_min, cost_mid, cost_max, and display string, or None if insufficient data
    """
    # Get overall failure probability
    p_fail = risk_data.get("Failure_Risk", 0)
    if p_fail <= 0:
        return None
    
    # Component name mapping from API/database to cost table
    # Note: CSV column names use "Risk_Lamps_Reflectors_And_Electrical_Equipment" and "Risk_Body_Chassis_Structure"
    COMPONENT_MAP = {
        "Risk_Brakes": "Brakes",
        "Risk_Suspension": "Suspension",
        "Risk_Tyres": "Tyres",
        "Risk_Steering": "Steering",
        "Risk_Visibility": "Visibility",
        "Risk_Lamps_Reflectors_And_Electrical_Equipment": "Lamps_Reflectors_And_Electrical_Equipment",
        "Risk_Body_Chassis_Structure": "Body_Chassis_Structure",
        # Also handle lowercase variants from PostgreSQL (via normalize_columns)
        "Risk_Lamps_Reflectors_Electrical_Equipment": "Lamps_Reflectors_And_Electrical_Equipment",
        "Risk_Body_Chassis_Structure_Exhaust": "Body_Chassis_Structure",
    }
    
    # Calculate E[cost per MOT] = Σ risk_i × cost_mid_i
    expected_cost_per_mot = 0.0
    components_used = 0
    
    for api_key, cost_key in COMPONENT_MAP.items():
        risk_i = risk_data.get(api_key, 0) or 0
        if risk_i > 0 and cost_key in REPAIR_COSTS:
            cost_mid = REPAIR_COSTS[cost_key]["typical"]
            expected_cost_per_mot += risk_i * cost_mid
            components_used += 1
    
    # Need at least some component data to calculate
    if components_used == 0 or expected_cost_per_mot <= 0:
        return None
    
    # Calculate conditional cost: E[cost | fail] = E[cost per MOT] / p_fail
    fail_mid = expected_cost_per_mot / p_fail
    
    # Apply range calculation with floor and ceiling
    FLOOR = 150  # Minimum cost floor
    MIN_MULTIPLIER = 0.6
    MAX_MULTIPLIER = 1.7
    
    fail_min = max(MIN_MULTIPLIER * fail_mid, FLOOR)
    fail_max = MAX_MULTIPLIER * fail_mid
    
    # Round to sensible values
    fail_min = round(fail_min / 10) * 10  # Round to nearest £10
    fail_mid = round(fail_mid / 10) * 10
    fail_max = round(fail_max / 10) * 10
    
    return {
        "cost_min": int(fail_min),
        "cost_mid": int(fail_mid),
        "cost_max": int(fail_max),
        "display": f"usually cost around £{int(fail_mid)}, often between £{int(fail_min)} and £{int(fail_max)}",
        "disclaimer": "Guide prices only—actual costs vary by garage, region and vehicle condition."
    }
