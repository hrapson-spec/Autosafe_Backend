"""
Lead Matching Algorithm for AutoSafe.
Matches leads to nearby garages based on postcode proximity.
"""
import logging
from typing import List, Optional
from dataclasses import dataclass

import database as db
from postcode_service import get_postcode_coordinates, haversine_distance

logger = logging.getLogger(__name__)

# Distance tiers (in miles) - expand search if no results
RADIUS_TIERS = [5, 10, 15, 25]

# Maximum garages to send each lead to
MAX_GARAGES_PER_LEAD = 3

# Email source priority (lower = higher priority)
# Scraped emails are more reliable than inferred
EMAIL_SOURCE_PRIORITY = {
    "scraped": 1,      # Highest - found on website
    "directory": 2,    # From garage directories
    "inferred": 3,     # Generated from domain
}


@dataclass
class MatchedGarage:
    """A garage matched to a lead."""
    garage_id: str
    name: str
    email: str
    postcode: str
    distance_miles: float
    tier: str
    email_source: str = "directory"  # scraped, directory, or inferred


async def find_matching_garages(
    lead_postcode: str,
    max_results: int = MAX_GARAGES_PER_LEAD
) -> List[MatchedGarage]:
    """
    Find garages that match a lead based on geographic proximity.

    Args:
        lead_postcode: The lead's postcode
        max_results: Maximum number of garages to return

    Returns:
        List of MatchedGarage objects, sorted by distance
    """
    # Get lead coordinates
    lead_coords = await get_postcode_coordinates(lead_postcode)
    if not lead_coords:
        logger.warning(f"Could not geocode lead postcode: {lead_postcode}")
        return []

    lead_lat, lead_lng = lead_coords

    # Get all active garages with coordinates
    garages = await db.get_garages_with_coordinates()
    if not garages:
        logger.warning("No garages with coordinates found")
        return []

    # Calculate distance for each garage
    matches = []
    for garage in garages:
        distance = haversine_distance(
            lead_lat, lead_lng,
            garage['latitude'], garage['longitude']
        )

        # Check if within maximum radius
        if distance <= RADIUS_TIERS[-1]:
            matches.append({
                'garage_id': garage['id'],
                'name': garage['name'],
                'email': garage['email'],
                'postcode': garage['postcode'],
                'distance_miles': round(distance, 1),
                'tier': garage['tier'],
                'email_source': garage.get('email_source', 'directory')
            })

    if not matches:
        logger.info(f"No garages found within {RADIUS_TIERS[-1]} miles of {lead_postcode}")
        return []

    # Sort by: paid tier first, then email quality, then distance
    # Scraped emails are more reliable than inferred
    tier_priority = {'unlimited': 0, 'pro': 1, 'starter': 2, 'free': 3}
    matches.sort(key=lambda m: (
        tier_priority.get(m['tier'], 4),
        EMAIL_SOURCE_PRIORITY.get(m['email_source'], 3),
        m['distance_miles']
    ))

    # Return top matches
    result = [
        MatchedGarage(
            garage_id=m['garage_id'],
            name=m['name'],
            email=m['email'],
            postcode=m['postcode'],
            distance_miles=m['distance_miles'],
            tier=m['tier'],
            email_source=m['email_source']
        )
        for m in matches[:max_results]
    ]

    logger.info(f"Found {len(result)} garage(s) for lead in {lead_postcode}")
    return result


async def find_garages_in_radius(
    postcode: str,
    radius_miles: float
) -> List[MatchedGarage]:
    """
    Find all garages within a specific radius of a postcode.

    Args:
        postcode: Center postcode
        radius_miles: Search radius in miles

    Returns:
        List of MatchedGarage objects within radius
    """
    coords = await get_postcode_coordinates(postcode)
    if not coords:
        return []

    lat, lng = coords
    garages = await db.get_garages_with_coordinates()

    matches = []
    for garage in garages:
        distance = haversine_distance(lat, lng, garage['latitude'], garage['longitude'])
        if distance <= radius_miles:
            matches.append(MatchedGarage(
                garage_id=garage['id'],
                name=garage['name'],
                email=garage['email'],
                postcode=garage['postcode'],
                distance_miles=round(distance, 1),
                tier=garage['tier'],
                email_source=garage.get('email_source', 'directory')
            ))

    # Sort by email quality then distance
    matches.sort(key=lambda m: (
        EMAIL_SOURCE_PRIORITY.get(m.email_source, 3),
        m.distance_miles
    ))
    return matches
