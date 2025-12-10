"""
Adjustment Factor Calculation Module

Calculates risk adjustment factors based on:
- Defect type (advisory vs failure)
- Component classification (consumable vs systemic)
- Time since last failure (for protective effect decay)

These factors modify the base population-level risk estimates to account
for the Repair Paradox: recent failures on consumable components indicate
lower risk (replacement), while failures on systemic components indicate
higher risk (chronic condition).
"""

from typing import Dict, Optional

import pandas as pd

from component_classification import (
    CATEGORY_CLASSIFICATION,
    ComponentType,
    apply_protective_time_decay,
    get_base_adjustment_factor,
    get_component_type,
)


def calculate_advisory_adjustment(
    advisory_count: int,
    category: str,
    base_multiplier: Optional[float] = None
) -> float:
    """
    Calculate the risk adjustment for a component with advisories.
    
    Each advisory indicates degradation. Multiple advisories compound the effect.
    
    Args:
        advisory_count: Number of advisories for this category
        category: MOT defect category
        base_multiplier: Override base multiplier (for testing)
        
    Returns:
        Risk multiplier (>1.0 for elevated risk)
    """
    if advisory_count <= 0:
        return 1.0
    
    comp_type = get_component_type(category)
    base = base_multiplier or get_base_adjustment_factor("advisory", comp_type)
    
    # Compound effect for multiple advisories (diminishing returns)
    # First advisory: full effect, subsequent: sqrt scaling
    if advisory_count == 1:
        return base
    else:
        # e.g., 2 advisories: 1.5 * sqrt(1.5) ≈ 1.84
        return base * (base ** (0.5 * (advisory_count - 1) / advisory_count))


def calculate_failure_adjustment(
    had_failure: bool,
    category: str,
    months_since_failure: int = 0,
    recurrence_count: int = 0
) -> float:
    """
    Calculate the risk adjustment for a component with prior failure.
    
    Args:
        had_failure: Whether this category had a failure
        category: MOT defect category
        months_since_failure: Months since the failure occurred
        recurrence_count: Number of consecutive failures (for recurrence detection)
        
    Returns:
        Risk multiplier (<1.0 for consumables, >1.0 for systemic)
    """
    if not had_failure:
        return 1.0
    
    comp_type = get_component_type(category)
    base = get_base_adjustment_factor("failure", comp_type)
    
    # For consumables, apply time decay to protective effect
    if comp_type == ComponentType.CONSUMABLE:
        # Recurrence overrides protective effect
        if recurrence_count >= 2:
            # Multiple consecutive failures suggest minimum-quality repairs
            # Reduce protective effect significantly
            return 0.8 + (1.0 - base) * 0.5  # e.g., 0.6 -> 0.8 instead of full protection
        
        return apply_protective_time_decay(months_since_failure, base)
    
    # For systemic, effect worsens over time (optional: could add escalation)
    return base


def calculate_combined_adjustment(
    category: str,
    had_failure: bool = False,
    advisory_count: int = 0,
    months_since_failure: int = 0,
    recurrence_count: int = 0
) -> float:
    """
    Calculate combined adjustment factor considering both failures and advisories.
    
    Args:
        category: MOT defect category
        had_failure: Whether had a failure on this component
        advisory_count: Number of advisories
        months_since_failure: Months since failure
        recurrence_count: Consecutive failure count
        
    Returns:
        Combined risk multiplier
    """
    failure_adj = calculate_failure_adjustment(
        had_failure, category, months_since_failure, recurrence_count
    )
    advisory_adj = calculate_advisory_adjustment(advisory_count, category)
    
    # Combine multiplicatively
    return failure_adj * advisory_adj


def apply_adjustment_factors_to_risks(
    risk_data: Dict[str, float],
    failure_history: Optional[Dict[str, bool]] = None,
    advisory_counts: Optional[Dict[str, int]] = None
) -> Dict[str, float]:
    """
    Apply adjustment factors to a set of component risks.
    
    This is the main entry point for adjusting population-level risks
    based on component classification.
    
    Args:
        risk_data: Dict of Risk_<category>: probability from API
        failure_history: Dict of category: had_failure (optional)
        advisory_counts: Dict of category: advisory_count (optional)
        
    Returns:
        Dict with adjusted risk values
    """
    adjusted = {}
    
    for key, base_risk in risk_data.items():
        if not key.startswith("Risk_"):
            adjusted[key] = base_risk
            continue
        
        # Extract category name
        category = key.replace("Risk_", "").replace("_", " ")
        
        # Get adjustment inputs
        had_failure = failure_history.get(category, False) if failure_history else False
        adv_count = advisory_counts.get(category, 0) if advisory_counts else 0
        
        # Calculate adjustment
        multiplier = calculate_combined_adjustment(
            category,
            had_failure=had_failure,
            advisory_count=adv_count
        )
        
        # Apply adjustment (cap at 1.0 for probabilities)
        adjusted_risk = min(base_risk * multiplier, 1.0)
        adjusted[key] = adjusted_risk
        
        # Store the adjustment factor for transparency
        adjusted[f"{key}_Adjustment"] = multiplier
    
    return adjusted


def generate_adjustment_factor_table() -> pd.DataFrame:
    """
    Generate a table of all adjustment factors for documentation/validation.
    
    Returns:
        DataFrame with category, type, advisory factor, failure factor
    """
    rows = []
    
    for category, comp_type in CATEGORY_CLASSIFICATION.items():
        advisory_factor = get_base_adjustment_factor("advisory", comp_type)
        failure_factor = get_base_adjustment_factor("failure", comp_type)
        
        rows.append({
            "Category": category,
            "Type": comp_type.value,
            "Advisory_Multiplier": advisory_factor,
            "Failure_Multiplier": failure_factor,
            "Advisory_Direction": "↑ elevated" if advisory_factor > 1.0 else "→ neutral",
            "Failure_Direction": (
                "↓ reduced" if failure_factor < 1.0 
                else "↑ elevated" if failure_factor > 1.0 
                else "→ neutral"
            )
        })
    
    return pd.DataFrame(rows)


if __name__ == "__main__":
    print("=" * 80)
    print("ADJUSTMENT FACTOR TABLE")
    print("=" * 80)
    
    df = generate_adjustment_factor_table()
    print(df.to_string(index=False))
    
    print("\n" + "=" * 80)
    print("EXAMPLE CALCULATIONS")
    print("=" * 80)
    
    # Example: Brakes (consumable)
    print("\n🚗 Brakes (consumable):")
    print(f"  - No history: {calculate_combined_adjustment('Brakes'):.2f}")
    print(f"  - Had failure (0 months ago): {calculate_combined_adjustment('Brakes', had_failure=True, months_since_failure=0):.2f}")
    print(f"  - Had failure (24 months ago): {calculate_combined_adjustment('Brakes', had_failure=True, months_since_failure=24):.2f}")
    print(f"  - 1 advisory: {calculate_combined_adjustment('Brakes', advisory_count=1):.2f}")
    print(f"  - Had failure + 1 advisory: {calculate_combined_adjustment('Brakes', had_failure=True, advisory_count=1):.2f}")
    
    # Example: Body structure (systemic)
    print("\n🏗️ Body, chassis, structure (systemic):")
    print(f"  - No history: {calculate_combined_adjustment('Body, chassis, structure'):.2f}")
    print(f"  - Had failure: {calculate_combined_adjustment('Body, chassis, structure', had_failure=True):.2f}")
    print(f"  - 1 advisory: {calculate_combined_adjustment('Body, chassis, structure', advisory_count=1):.2f}")
    print(f"  - Had failure + 1 advisory: {calculate_combined_adjustment('Body, chassis, structure', had_failure=True, advisory_count=1):.2f}")
