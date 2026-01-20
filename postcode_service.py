"""
Postcode Geocoding Service for AutoSafe.
Uses Postcodes.io (free, no API key required) to convert UK postcodes to coordinates.
"""
import httpx
import logging
from collections import OrderedDict
from typing import Optional, Tuple, Dict
from math import radians, sin, cos, sqrt, atan2

logger = logging.getLogger(__name__)

POSTCODES_IO_BASE = "https://api.postcodes.io"

# Maximum cache size to prevent unbounded memory growth
MAX_CACHE_SIZE = 10000

# In-memory LRU cache to reduce API calls
# Using OrderedDict for LRU behavior
_postcode_cache: OrderedDict[str, Tuple[float, float]] = OrderedDict()


def _cache_set(postcode: str, coords: Tuple[float, float]) -> None:
    """Add item to cache with LRU eviction."""
    global _postcode_cache
    # If already in cache, move to end (most recently used)
    if postcode in _postcode_cache:
        _postcode_cache.move_to_end(postcode)
        _postcode_cache[postcode] = coords
        return

    # Evict oldest items if cache is full
    while len(_postcode_cache) >= MAX_CACHE_SIZE:
        _postcode_cache.popitem(last=False)

    _postcode_cache[postcode] = coords


def _cache_get(postcode: str) -> Optional[Tuple[float, float]]:
    """Get item from cache, updating LRU order."""
    global _postcode_cache
    if postcode in _postcode_cache:
        _postcode_cache.move_to_end(postcode)
        return _postcode_cache[postcode]
    return None


async def get_postcode_coordinates(postcode: str) -> Optional[Tuple[float, float]]:
    """
    Convert UK postcode to latitude/longitude using Postcodes.io.

    Args:
        postcode: UK postcode (e.g., "SW1A 1AA" or "SW1A1AA")

    Returns:
        Tuple of (latitude, longitude) or None if invalid/not found
    """
    # Normalize postcode: uppercase, remove spaces
    postcode = postcode.strip().upper().replace(" ", "")

    # Check cache first
    cached = _cache_get(postcode)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{POSTCODES_IO_BASE}/postcodes/{postcode}",
                timeout=5.0
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("status") == 200 and data.get("result"):
                    result = data["result"]
                    coords = (result["latitude"], result["longitude"])
                    _cache_set(postcode, coords)
                    logger.debug(f"Geocoded {postcode} -> {coords}")
                    return coords

            # For partial postcodes (outward code only, e.g., "SW1A"), use outcodes endpoint
            if len(postcode) <= 4:
                response = await client.get(
                    f"{POSTCODES_IO_BASE}/outcodes/{postcode}",
                    timeout=5.0
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get("result"):
                        result = data["result"]
                        coords = (result["latitude"], result["longitude"])
                        _cache_set(postcode, coords)
                        return coords

    except httpx.TimeoutException:
        logger.warning(f"Timeout looking up postcode: {postcode}")
    except Exception as e:
        logger.error(f"Postcode lookup failed for {postcode}: {e}")

    return None


async def bulk_lookup_postcodes(postcodes: list) -> Dict[str, Tuple[float, float]]:
    """
    Bulk lookup postcodes (max 100 at a time for Postcodes.io).

    Args:
        postcodes: List of UK postcodes

    Returns:
        Dict mapping postcodes to (lat, lng) tuples
    """
    results = {}

    # Normalize all postcodes
    postcodes = [p.strip().upper().replace(" ", "") for p in postcodes]

    # Add cached results
    uncached = []
    for p in postcodes:
        cached = _cache_get(p)
        if cached is not None:
            results[p] = cached
        else:
            uncached.append(p)

    if not uncached:
        return results

    # Bulk API call (max 100)
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{POSTCODES_IO_BASE}/postcodes",
                json={"postcodes": uncached[:100]},
                timeout=10.0
            )

            if response.status_code == 200:
                data = response.json()
                for item in data.get("result", []):
                    if item.get("result"):
                        postcode = item["query"].upper().replace(" ", "")
                        result = item["result"]
                        coords = (result["latitude"], result["longitude"])
                        _cache_set(postcode, coords)
                        results[postcode] = coords

    except Exception as e:
        logger.error(f"Bulk postcode lookup failed: {e}")

    return results


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate distance between two points in miles using Haversine formula.

    Args:
        lat1, lon1: First point coordinates
        lat2, lon2: Second point coordinates

    Returns:
        Distance in miles
    """
    R = 3959  # Earth's radius in miles

    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return R * c


def clear_cache():
    """Clear the postcode cache (useful for testing)."""
    global _postcode_cache
    _postcode_cache = OrderedDict()


def get_cache_size() -> int:
    """Get current cache size (useful for monitoring)."""
    return len(_postcode_cache)
