"""
Repair Paradox Validation Script

Validates that adjustment factors align with expected patterns:
- Advisories → elevated future failure risk (multiplier > 1.0)
- Consumable failures → reduced future failure risk (multiplier < 1.0) 
- Systemic failures → elevated future failure risk (multiplier > 1.0)

Generates a JSON report with calculated multipliers and validation status.
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Any

from component_classification import (
    get_component_type,
    get_all_category_classifications,
    ComponentType,
    DEFAULT_ADJUSTMENT_FACTORS,
    FULL_PROTECTION_MONTHS,
    NEUTRAL_BY_MONTHS
)
from adjustment_factors import generate_adjustment_factor_table


def validate_adjustment_directions() -> Dict[str, Any]:
    """
    Validate that adjustment factors follow expected patterns.
    
    Returns:
        Validation results with pass/fail status for each rule
    """
    results = {
        "timestamp": datetime.now().isoformat(),
        "validation_rules": [],
        "all_passed": True
    }
    
    # Rule 1: All advisory factors should be > 1.0
    advisory_check = {
        "rule": "Advisory factors elevate risk",
        "expected": "All advisory multipliers > 1.0",
        "details": []
    }
    for (defect_type, comp_type), factor in DEFAULT_ADJUSTMENT_FACTORS.items():
        if defect_type == "advisory":
            passed = factor > 1.0
            advisory_check["details"].append({
                "component_type": comp_type.value,
                "factor": factor,
                "passed": passed
            })
            if not passed:
                advisory_check["passed"] = False
                results["all_passed"] = False
    advisory_check["passed"] = all(d["passed"] for d in advisory_check["details"])
    results["validation_rules"].append(advisory_check)
    
    # Rule 2: Consumable failure factors should be < 1.0
    consumable_check = {
        "rule": "Consumable failure factors reduce risk",
        "expected": "Failure multiplier < 1.0 for consumable components",
        "details": []
    }
    for (defect_type, comp_type), factor in DEFAULT_ADJUSTMENT_FACTORS.items():
        if defect_type == "failure" and comp_type == ComponentType.CONSUMABLE:
            passed = factor < 1.0
            consumable_check["details"].append({
                "component_type": comp_type.value,
                "factor": factor,
                "passed": passed
            })
            if not passed:
                results["all_passed"] = False
    consumable_check["passed"] = all(d["passed"] for d in consumable_check["details"])
    results["validation_rules"].append(consumable_check)
    
    # Rule 3: Systemic failure factors should be > 1.0
    systemic_check = {
        "rule": "Systemic failure factors elevate risk",
        "expected": "Failure multiplier > 1.0 for systemic components",
        "details": []
    }
    for (defect_type, comp_type), factor in DEFAULT_ADJUSTMENT_FACTORS.items():
        if defect_type == "failure" and comp_type == ComponentType.SYSTEMIC:
            passed = factor > 1.0
            systemic_check["details"].append({
                "component_type": comp_type.value,
                "factor": factor,
                "passed": passed
            })
            if not passed:
                results["all_passed"] = False
    systemic_check["passed"] = all(d["passed"] for d in systemic_check["details"])
    results["validation_rules"].append(systemic_check)
    
    return results


def generate_classification_report() -> Dict[str, Any]:
    """
    Generate a report of all component classifications.
    """
    classifications = get_all_category_classifications()
    
    consumable = [c for c, t in classifications.items() if t == "consumable"]
    systemic = [c for c, t in classifications.items() if t == "systemic"]
    mixed = [c for c, t in classifications.items() if t == "mixed"]
    
    return {
        "consumable": sorted(consumable),
        "systemic": sorted(systemic),
        "mixed": sorted(mixed),
        "total_categories": len(classifications)
    }


def generate_factor_summary() -> List[Dict[str, Any]]:
    """
    Generate summary of all adjustment factors as a list of dicts.
    """
    df = generate_adjustment_factor_table()
    return df.to_dict(orient="records")


def generate_time_decay_summary() -> Dict[str, Any]:
    """
    Document the time decay configuration for consumable replacements.
    """
    return {
        "full_protection_months": FULL_PROTECTION_MONTHS,
        "neutral_by_months": NEUTRAL_BY_MONTHS,
        "description": (
            f"Protective effect of consumable replacement is full for {FULL_PROTECTION_MONTHS} months, "
            f"then decays linearly to neutral by {NEUTRAL_BY_MONTHS} months."
        )
    }


def main():
    """
    Run full validation and generate report.
    """
    print("=" * 70)
    print("REPAIR PARADOX VALIDATION REPORT")
    print("=" * 70)
    
    # Run validations
    validation_results = validate_adjustment_directions()
    classification_report = generate_classification_report()
    factor_summary = generate_factor_summary()
    time_decay = generate_time_decay_summary()
    
    # Compile full report
    full_report = {
        "generated_at": datetime.now().isoformat(),
        "validation": validation_results,
        "component_classification": classification_report,
        "adjustment_factors": factor_summary,
        "time_decay": time_decay
    }
    
    # Save report
    output_file = "adjustment_factors_report.json"
    with open(output_file, "w") as f:
        json.dump(full_report, f, indent=2)
    
    # Print summary
    print(f"\n📊 Component Classifications:")
    print(f"   - Consumable: {len(classification_report['consumable'])} categories")
    print(f"   - Systemic: {len(classification_report['systemic'])} categories")
    print(f"   - Mixed: {len(classification_report['mixed'])} categories")
    
    print(f"\n🧪 Validation Results:")
    all_passed = True
    for rule in validation_results["validation_rules"]:
        status = "✅ PASS" if rule["passed"] else "❌ FAIL"
        print(f"   {status}: {rule['rule']}")
        if not rule["passed"]:
            all_passed = False
    
    print(f"\n⏱️ Time Decay Configuration:")
    print(f"   - Full protection: {time_decay['full_protection_months']} months")
    print(f"   - Neutral by: {time_decay['neutral_by_months']} months")
    
    print(f"\n📁 Report saved to: {output_file}")
    
    if all_passed:
        print("\n✅ ALL VALIDATIONS PASSED")
    else:
        print("\n❌ SOME VALIDATIONS FAILED - Review report for details")
    
    return full_report


if __name__ == "__main__":
    main()
