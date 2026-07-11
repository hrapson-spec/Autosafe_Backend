"""
Utility functions for age/mileage band calculations and privacy-safe
logging helpers (VRM hashing, postcode masking).
"""
import hashlib
from typing import Optional, Union
import pandas as pd


def get_age_band(age: Optional[Union[int, float]]) -> str:
    """
    Get age band classification for a vehicle.

    Args:
        age: Vehicle age in years (can be None, NaN, or numeric)

    Returns:
        Age band string (e.g., '0-3', '3-5', '6-10', '11-15', '15+', or 'Unknown')
    """
    # Handle None, NaN, and pandas NA types
    if age is None or pd.isna(age):
        return 'Unknown'

    # Handle negative ages (data error)
    if age < 0:
        return 'Unknown'

    if age < 3:
        return '0-2'
    elif age < 6:
        return '3-5'
    elif age < 11:
        return '6-10'
    elif age < 16:
        return '11-15'
    else:
        return '15+'


def get_mileage_band(miles: Optional[Union[int, float]]) -> str:
    """
    Get mileage band classification for a vehicle.

    Args:
        miles: Vehicle mileage (can be None, NaN, or numeric)

    Returns:
        Mileage band string (e.g., '0-30k', '30k-60k', etc., or 'Unknown')
    """
    # Handle None, NaN, and pandas NA types
    if miles is None or pd.isna(miles):
        return 'Unknown'

    # Handle invalid values (negative or unrealistically high)
    if miles < 0 or miles > 500000:
        return 'Unknown'

    if miles < 30000:
        return '0-30k'
    if miles < 60000:
        return '30k-60k'
    if miles < 100000:
        return '60k-100k'
    return '100k+'


def hash_vrm(vrm: str) -> str:
    """Hash VRM for logging to protect privacy (P1-10 fix)."""
    return hashlib.sha256(vrm.encode()).hexdigest()[:8]


def mask_postcode(postcode) -> str:
    """
    Mask a postcode's inward code for safe logging (outward code only).

    For log redaction only -- never use this for storage, matching, or
    display. The full postcode must still be persisted/used elsewhere;
    this helper only produces a coarse value safe to place in log lines.

    Args:
        postcode: Raw postcode value (can be None, empty, non-str, or a
            UK postcode string, with or without the space, any case).

    Returns:
        The outward code (e.g. "SW1A 1AA" -> "SW1A", "sw1a1aa" -> "SW1A",
        "M1 1AE" -> "M1") upper-cased with surrounding whitespace
        trimmed, or "***" if the input is None, empty, non-str, or too
        short to safely mask (<=3 chars remaining after stripping).
    """
    if not isinstance(postcode, str) or not postcode.strip():
        return "***"
    cleaned = postcode.strip().upper()
    if len(cleaned) > 3:
        return cleaned[:-3].strip()
    return "***"
