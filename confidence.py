"""
Confidence Interval Calculations for AutoSafe
Uses Wilson score interval for binomial proportions
"""
import math
from typing import Tuple


def wilson_interval(successes: int, total: int, confidence: float = 0.95) -> Tuple[float, float]:
    """
    Calculate the Wilson score confidence interval for a binomial proportion.
    
    This is the recommended method for small sample sizes and proportions near 0 or 1.
    
    Args:
        successes: Number of "successes" (failures in MOT context)
        total: Total number of trials (tests)
        confidence: Confidence level (default 95%)
    
    Returns:
        Tuple of (lower_bound, upper_bound) for the true proportion
    """
    if total == 0:
        return (0.0, 0.0)
    
    # Z-score for confidence level (1.96 for 95%)
    z = 1.96 if confidence == 0.95 else 1.645 if confidence == 0.90 else 2.576
    
    n = total
    p_hat = successes / n
    
    denominator = 1 + z**2 / n
    center = (p_hat + z**2 / (2*n)) / denominator
    
    spread = z * math.sqrt((p_hat * (1 - p_hat) + z**2 / (4*n)) / n) / denominator
    
    lower = max(0, center - spread)
    upper = min(1, center + spread)
    
    return (lower, upper)


def classify_confidence(total_tests: int) -> str:
    """
    Classify the confidence level based on sample size.
    
    Args:
        total_tests: Number of MOT tests in the sample
    
    Returns:
        Confidence level string: "High", "Good", or "Limited"
    """
    if total_tests >= 10000:
        return "High"
    elif total_tests >= 1000:
        return "Good"
    else:
        return "Limited"


def margin_of_error(successes: int, total: int, confidence: float = 0.95) -> float:
    """
    Calculate the margin of error for the estimate.
    
    Args:
        successes: Number of failures
        total: Total tests
        confidence: Confidence level
    
    Returns:
        Margin of error as a decimal (e.g., 0.02 = ±2%)
    """
    lower, upper = wilson_interval(successes, total, confidence)
    return (upper - lower) / 2
