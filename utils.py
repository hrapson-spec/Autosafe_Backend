"""
Utility functions for age and mileage band calculations,
and PII masking for safe logging.
"""
from typing import Optional, Union
import pandas as pd


def mask_email(email: str) -> str:
    """
    Mask email for logging: john@example.com -> j***@example.com

    Args:
        email: Email address to mask

    Returns:
        Masked email safe for logging
    """
    if not email or '@' not in email:
        return '***'
    local, domain = email.rsplit('@', 1)
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}***@{domain}"


def mask_phone(phone: Optional[str]) -> str:
    """
    Mask phone number for logging: 07123456789 -> 07***789

    Args:
        phone: Phone number to mask

    Returns:
        Masked phone safe for logging
    """
    if not phone:
        return '***'
    phone = phone.replace(' ', '').replace('-', '')
    if len(phone) <= 4:
        return '***'
    return f"{phone[:2]}***{phone[-3:]}"


def get_age_band(age: Optional[Union[int, float]]) -> str:
    """
    Get age band classification for a vehicle.

    Args:
        age: Vehicle age in years (can be None, NaN, or numeric)

    Returns:
        Age band string (e.g., '0-3', '3-5', '6-10', '10-15', '15+', or 'Unknown')
    """
    # Handle None and NaN
    if age is None or (isinstance(age, float) and pd.isna(age)):
        return 'Unknown'

    # Handle negative ages (data error)
    if age < 0:
        return 'Unknown'

    if age < 3:
        return '0-3'
    elif age < 6:
        return '3-5'
    elif age < 11:
        return '6-10'
    elif age < 16:
        return '10-15'
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
    # Handle None and NaN
    if miles is None or (isinstance(miles, float) and pd.isna(miles)):
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
