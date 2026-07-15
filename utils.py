"""
Utility functions for age/mileage band calculations and privacy-safe
logging helpers (VRM hashing, postcode masking).
"""
import hashlib
import hmac
import os
import re
import secrets
from typing import Optional, Union
from urllib.parse import urlsplit, urlunsplit
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


_LOG_HASH_KEY = (
    os.environ.get("VRM_LOG_HMAC_KEY")
    or os.environ.get("VRM_HMAC_KEY")
    or secrets.token_hex(32)
).encode("utf-8")


def hash_vrm(vrm: str) -> str:
    """Return a compact keyed digest for within-service log correlation.

    VRNs occupy a small, enumerable keyspace, so an unkeyed truncated hash is
    not an adequate log pseudonym. Production can provide a dedicated
    ``VRM_LOG_HMAC_KEY`` (preferred) or reuse ``VRM_HMAC_KEY``; local/demo
    processes get an ephemeral random key, which intentionally prevents
    correlation across restarts.
    """
    normalized = "".join(str(vrm).upper().split())
    return hmac.new(_LOG_HASH_KEY, normalized.encode("utf-8"), hashlib.sha256).hexdigest()[:8]


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


_SHARE_PATH_PATTERNS = (
    re.compile(r"^/api/v2/reports/[^/]+$"),
    re.compile(r"^/app/report/[^/]+$"),
)


def safe_log_path(path: str) -> str:
    """Redact opaque report bearer tokens from application log paths.

    The route shape is still retained for monitoring, while the credential
    embedded in the final path segment never reaches a log formatter.
    """
    if not isinstance(path, str):
        return "[invalid-path]"
    for pattern in _SHARE_PATH_PATTERNS:
        if pattern.fullmatch(path):
            prefix = path.rsplit("/", 1)[0]
            return prefix + "/{token}"
    return path


def safe_referrer(value: Optional[str]) -> Optional[str]:
    """Return only a referrer's HTTP(S) origin.

    Paths, query strings, and fragments are intentionally discarded because
    legacy links and third-party campaigns can put registrations, postcodes,
    email addresses, bearer tokens, or other identifiers in any of them.
    Embedded credentials and non-web schemes are rejected rather than
    persisted.
    """
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        hostname = parsed.hostname.lower()
        if ":" in hostname:  # Preserve valid IPv6 URL syntax.
            hostname = f"[{hostname}]"
        port = parsed.port
        netloc = f"{hostname}:{port}" if port is not None else hostname
        return urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))
    except (TypeError, ValueError):
        return None
