"""
AutoSafe Utility Functions
==========================

Common utility functions for age and mileage band calculations.
"""
from typing import Union

import pandas as pd


# =============================================================================
# AGE BAND THRESHOLDS (years)
# =============================================================================
AGE_BAND_THRESHOLDS = {
    'young': 3,      # 0-3 years
    'medium': 6,     # 3-5 years
    'mature': 11,    # 6-10 years
    'old': 16,       # 10-15 years
    # 16+ years = '15+'
}


# =============================================================================
# MILEAGE BAND THRESHOLDS (miles)
# =============================================================================
MILEAGE_BAND_THRESHOLDS = {
    'low': 30000,     # 0-30k
    'medium': 60000,  # 30k-60k
    'high': 100000,   # 60k-100k
    # 100k+ = '100k+'
}


def get_age_band(age: Union[int, float, None]) -> str:
    """
    Convert vehicle age in years to a categorical band.

    Args:
        age: Vehicle age in years (can be int, float, or None/NaN)

    Returns:
        Age band string: '0-3', '3-5', '6-10', '10-15', '15+', or 'Unknown'
    """
    if age is None or (isinstance(age, float) and pd.isna(age)):
        return 'Unknown'

    try:
        age = float(age)
    except (ValueError, TypeError):
        return 'Unknown'

    if age < AGE_BAND_THRESHOLDS['young']:
        return '0-3'
    elif age < AGE_BAND_THRESHOLDS['medium']:
        return '3-5'
    elif age < AGE_BAND_THRESHOLDS['mature']:
        return '6-10'
    elif age < AGE_BAND_THRESHOLDS['old']:
        return '10-15'
    else:
        return '15+'


def get_mileage_band(miles: Union[int, float, None]) -> str:
    """
    Convert vehicle mileage to a categorical band.

    Args:
        miles: Vehicle mileage (can be int, float, or None/NaN)

    Returns:
        Mileage band string: '0-30k', '30k-60k', '60k-100k', '100k+', or 'Unknown'
    """
    if miles is None or (isinstance(miles, float) and pd.isna(miles)):
        return 'Unknown'

    try:
        miles = float(miles)
    except (ValueError, TypeError):
        return 'Unknown'

    if miles < 0:
        return 'Unknown'

    if miles < MILEAGE_BAND_THRESHOLDS['low']:
        return '0-30k'
    elif miles < MILEAGE_BAND_THRESHOLDS['medium']:
        return '30k-60k'
    elif miles < MILEAGE_BAND_THRESHOLDS['high']:
        return '60k-100k'
    else:
        return '100k+'
