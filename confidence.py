"""
Confidence interval utilities for risk estimation.
Implements Wilson score interval for binomial proportions.
"""
import math


def wilson_interval(successes: int, total: int, confidence: float = 0.95) -> tuple:
    """
    Calculate Wilson score interval for binomial proportion.
    
    More accurate than simple normal approximation, especially for
    small sample sizes or extreme proportions.
    
    Args:
        successes: Number of "success" events (e.g., failures)
        total: Total number of trials (e.g., tests)
        confidence: Confidence level (default 0.95 for 95% CI)
    
    Returns:
        Tuple of (lower_bound, upper_bound) for the confidence interval
    """
    if total == 0:
        return 0.0, 1.0
    
    # Z-score for confidence level
    z = 1.96 if confidence == 0.95 else 2.576 if confidence == 0.99 else 1.645
    
    p = successes / total
    n = total
    
    denominator = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z**2 / (4 * n)) / n) / denominator
    
    return max(0.0, center - margin), min(1.0, center + margin)


def classify_confidence(total_tests: int) -> str:
    """
    Classify confidence level based on sample size.
    
    Args:
        total_tests: Number of tests in the sample
    
    Returns:
        Human-readable confidence classification
    """
    if total_tests >= 1000:
        return "High"
    elif total_tests >= 100:
        return "Medium"
    elif total_tests >= 20:
        return "Low"
    else:
        return "Very Low"
