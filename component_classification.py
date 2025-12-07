"""
Component Classification Module

Classifies MOT defect categories into consumable vs systemic types
to support the Repair Paradox risk adjustment logic.

Consumable: Components where failure typically means replacement with new parts
            (brake pads, tyres, bulbs) - recent failure = LOWER future risk

Systemic: Components where repair patches symptoms rather than curing root cause
          (rust, leaks, electrical faults) - failure = HIGHER future risk
"""

from enum import Enum
from typing import Dict, Tuple


class ComponentType(Enum):
    """Component classification for repair paradox logic."""
    CONSUMABLE = "consumable"  # Replaced when failed, resets to low risk
    SYSTEMIC = "systemic"      # Patched, chronic issues that recur
    MIXED = "mixed"            # Contains both types of sub-components


class AdjustmentDirection(Enum):
    """Direction of risk adjustment."""
    ELEVATED = "elevated"    # Multiplier > 1.0
    REDUCED = "reduced"      # Multiplier < 1.0
    NEUTRAL = "neutral"      # Multiplier = 1.0


# Classification of MOT defect categories
# Based on whether standard repair involves replacement (consumable) or patching (systemic)
CATEGORY_CLASSIFICATION: Dict[str, ComponentType] = {
    # CONSUMABLE: Standard repair = replacement with new parts
    "Brakes": ComponentType.CONSUMABLE,
    "Tyres": ComponentType.CONSUMABLE,
    "Lights": ComponentType.CONSUMABLE,  # Renamed from "Lamps, Reflectors and Electrical Equipment"
    "Lamps, reflectors and electrical equipment": ComponentType.CONSUMABLE,
    "Wheels": ComponentType.CONSUMABLE,  # Renamed from "Road Wheels"
    "Road Wheels": ComponentType.CONSUMABLE,
    "Visibility": ComponentType.CONSUMABLE,  # Wipers, washer fluid, mirrors
    
    # SYSTEMIC: Repairs typically patch rather than cure
    "Body, chassis, structure": ComponentType.SYSTEMIC,
    "Noise, emissions and leaks": ComponentType.SYSTEMIC,
    "Steering": ComponentType.SYSTEMIC,  # Often alignment/geometry issues
    
    # MIXED: Contains both consumable and systemic sub-components
    "Suspension": ComponentType.MIXED,  # Bushings = consumable, geometry = systemic
    
    # ADMINISTRATIVE/OTHER: Treat as neutral
    "Identification of the vehicle": ComponentType.SYSTEMIC,  # Registration issues tend to recur
    "Seat belts and supplementary restraint systems": ComponentType.CONSUMABLE,
    "Seat belt installation check": ComponentType.CONSUMABLE,
    "Speedometer and speed limiter": ComponentType.SYSTEMIC,
    "Buses and coaches supplementary tests": ComponentType.MIXED,
    "Items Not Tested": ComponentType.SYSTEMIC,
}


# Default adjustment factors (multipliers) - to be refined by empirical validation
# Format: (defect_type, component_type) -> base_multiplier
DEFAULT_ADJUSTMENT_FACTORS: Dict[Tuple[str, ComponentType], float] = {
    # Advisories always elevate risk (component is degrading but passed)
    ("advisory", ComponentType.CONSUMABLE): 1.5,
    ("advisory", ComponentType.SYSTEMIC): 1.5,
    ("advisory", ComponentType.MIXED): 1.4,
    
    # Failures on consumables = protective effect (component replaced)
    ("failure", ComponentType.CONSUMABLE): 0.6,
    
    # Failures on systemic = elevated risk (chronic condition)
    ("failure", ComponentType.SYSTEMIC): 1.3,
    
    # Failures on mixed = slight elevation (weighted average)
    ("failure", ComponentType.MIXED): 1.1,
}


# Time decay parameters for protective effect of consumable replacements
FULL_PROTECTION_MONTHS = 12   # Full protective effect for first 12 months
NEUTRAL_BY_MONTHS = 36        # Decays to neutral (1.0) by 36 months


def get_component_type(category: str) -> ComponentType:
    """
    Get the component type classification for a defect category.
    
    Args:
        category: MOT defect category name
        
    Returns:
        ComponentType enum value
    """
    # Normalize category name
    normalized = category.strip()
    
    # Direct lookup
    if normalized in CATEGORY_CLASSIFICATION:
        return CATEGORY_CLASSIFICATION[normalized]
    
    # Try case-insensitive match
    for key, value in CATEGORY_CLASSIFICATION.items():
        if key.lower() == normalized.lower():
            return value
    
    # Default to systemic (safer - doesn't underestimate risk)
    return ComponentType.SYSTEMIC


def get_adjustment_direction(defect_type: str, component_type: ComponentType) -> AdjustmentDirection:
    """
    Determine the direction of risk adjustment based on defect type and component.
    
    Args:
        defect_type: "advisory" or "failure"
        component_type: ComponentType classification
        
    Returns:
        AdjustmentDirection indicating whether risk should be elevated/reduced
    """
    defect_type = defect_type.lower()
    
    # Advisories always elevate risk (component degrading but passed)
    if defect_type == "advisory":
        return AdjustmentDirection.ELEVATED
    
    # Failures depend on component type
    if defect_type in ("failure", "fail", "f", "prs"):
        if component_type == ComponentType.CONSUMABLE:
            return AdjustmentDirection.REDUCED  # Replacement effect
        elif component_type == ComponentType.SYSTEMIC:
            return AdjustmentDirection.ELEVATED  # Chronic condition
        else:  # MIXED
            return AdjustmentDirection.ELEVATED  # Err on side of caution
    
    return AdjustmentDirection.NEUTRAL


def get_base_adjustment_factor(defect_type: str, component_type: ComponentType) -> float:
    """
    Get the base adjustment factor (multiplier) for a defect type and component.
    
    Args:
        defect_type: "advisory" or "failure"
        component_type: ComponentType classification
        
    Returns:
        Multiplier to apply to base risk (>1 = elevated, <1 = reduced)
    """
    defect_type = defect_type.lower()
    if defect_type in ("fail", "f", "prs"):
        defect_type = "failure"
    
    key = (defect_type, component_type)
    return DEFAULT_ADJUSTMENT_FACTORS.get(key, 1.0)


def apply_protective_time_decay(months_since_failure: int, base_protective_effect: float) -> float:
    """
    Apply time decay to the protective effect of a consumable replacement.
    
    The protective effect (e.g., 0.6 multiplier) decays linearly from full
    effect at 12 months to neutral (1.0) at 36 months.
    
    Args:
        months_since_failure: Months since the component was replaced
        base_protective_effect: The base multiplier (e.g., 0.6)
        
    Returns:
        Time-decayed multiplier
    """
    if months_since_failure < 0:
        return 1.0
    
    # Full protection within first 12 months
    if months_since_failure <= FULL_PROTECTION_MONTHS:
        return base_protective_effect
    
    # Neutral by 36 months
    if months_since_failure >= NEUTRAL_BY_MONTHS:
        return 1.0
    
    # Linear decay between 12 and 36 months
    decay_range = NEUTRAL_BY_MONTHS - FULL_PROTECTION_MONTHS  # 24 months
    months_into_decay = months_since_failure - FULL_PROTECTION_MONTHS
    decay_fraction = months_into_decay / decay_range
    
    # Interpolate from base_protective_effect to 1.0
    return base_protective_effect + (1.0 - base_protective_effect) * decay_fraction


def get_all_category_classifications() -> Dict[str, str]:
    """
    Get a dictionary of all category classifications as strings.
    Useful for reporting and validation.
    
    Returns:
        Dict mapping category name to classification type string
    """
    return {
        category: comp_type.value 
        for category, comp_type in CATEGORY_CLASSIFICATION.items()
    }


if __name__ == "__main__":
    # Print classification summary for verification
    print("=" * 60)
    print("MOT DEFECT CATEGORY CLASSIFICATION")
    print("=" * 60)
    
    consumable = [c for c, t in CATEGORY_CLASSIFICATION.items() if t == ComponentType.CONSUMABLE]
    systemic = [c for c, t in CATEGORY_CLASSIFICATION.items() if t == ComponentType.SYSTEMIC]
    mixed = [c for c, t in CATEGORY_CLASSIFICATION.items() if t == ComponentType.MIXED]
    
    print("\n📦 CONSUMABLE (failure = replacement = lower risk):")
    for c in sorted(consumable):
        print(f"  - {c}")
    
    print("\n🔧 SYSTEMIC (failure = chronic condition = higher risk):")
    for c in sorted(systemic):
        print(f"  - {c}")
    
    print("\n⚖️ MIXED (contains both types):")
    for c in sorted(mixed):
        print(f"  - {c}")
    
    print("\n" + "=" * 60)
    print("ADJUSTMENT FACTORS")
    print("=" * 60)
    for (dtype, ctype), factor in DEFAULT_ADJUSTMENT_FACTORS.items():
        direction = "↑" if factor > 1.0 else "↓" if factor < 1.0 else "→"
        print(f"  {dtype:10} + {ctype.value:12} = {factor:.2f} {direction}")
