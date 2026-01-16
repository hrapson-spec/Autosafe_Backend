"""
Regional Defaults for V55 Model
===============================

Provides MVP default values for features that require external data:
- Local corrosion index (based on postcode area)
- Station strictness bias (neutral default)

These are simplified defaults for MVP. Future enhancement will build
proper corrosion index from DVSA bulk data analysis.
"""

import re
from typing import Optional, Dict


# Postcode area extraction pattern
# UK postcodes: AB12 3CD -> area = "AB"
POSTCODE_AREA_PATTERN = re.compile(r'^([A-Z]{1,2})')


# Corrosion index by region (MVP estimates)
# Based on general knowledge of:
# - Coastal proximity (salt air)
# - Road salt usage (winter gritting)
# - Humidity levels
#
# Scale: 0.0 (low corrosion risk) to 1.0 (high corrosion risk)
# Default: 0.5 (neutral)

CORROSION_INDEX_BY_AREA: Dict[str, float] = {
    # HIGH CORROSION RISK (0.7-0.9) - Coastal areas
    'AB': 0.75,   # Aberdeen - coastal, North Sea exposure
    'BN': 0.70,   # Brighton - coastal
    'CF': 0.70,   # Cardiff - coastal Wales
    'CT': 0.70,   # Canterbury - near coast
    'DN': 0.65,   # Doncaster - industrial, winter salting
    'DY': 0.60,   # Dudley - industrial Midlands
    'EX': 0.70,   # Exeter - Devon coast
    'FY': 0.75,   # Blackpool - coastal
    'GY': 0.80,   # Guernsey - island, high salt
    'HU': 0.70,   # Hull - Humber estuary
    'IM': 0.80,   # Isle of Man - island
    'IV': 0.75,   # Inverness - Highland coastal
    'JE': 0.80,   # Jersey - island
    'KW': 0.80,   # Kirkwall (Orkney) - island
    'LL': 0.70,   # Llandudno - Welsh coast
    'NE': 0.70,   # Newcastle - North Sea proximity
    'PA': 0.75,   # Paisley/West Scotland - Atlantic exposure
    'PH': 0.70,   # Perth - Scottish coastal access
    'PL': 0.75,   # Plymouth - coastal Devon
    'PO': 0.70,   # Portsmouth - coastal
    'SA': 0.70,   # Swansea - Welsh coast
    'SO': 0.65,   # Southampton - coastal but sheltered
    'SR': 0.70,   # Sunderland - coastal
    'SS': 0.65,   # Southend - Thames estuary
    'TD': 0.65,   # Galashiels - Scottish Borders, winter salting
    'TN': 0.60,   # Tunbridge Wells - Kent, some coastal influence
    'TR': 0.75,   # Truro - Cornwall coast
    'TS': 0.70,   # Cleveland - industrial coastal
    'ZE': 0.85,   # Shetland - extreme island exposure

    # MODERATE CORROSION RISK (0.5-0.65) - Inland urban/industrial
    'B': 0.55,    # Birmingham - urban, winter salting
    'BD': 0.55,   # Bradford - Pennine, winter salting
    'BL': 0.55,   # Bolton - Greater Manchester
    'CB': 0.50,   # Cambridge - inland, moderate
    'CH': 0.60,   # Chester - Mersey influence
    'CV': 0.50,   # Coventry - central England
    'DE': 0.50,   # Derby - inland
    'DH': 0.60,   # Durham - North, winter salting
    'DL': 0.55,   # Darlington - North, winter salting
    'E': 0.55,    # East London - urban
    'EC': 0.55,   # Central London - urban
    'EN': 0.50,   # Enfield - North London
    'GL': 0.50,   # Gloucester - inland
    'HD': 0.55,   # Huddersfield - Pennine
    'HG': 0.55,   # Harrogate - Yorkshire
    'HR': 0.50,   # Hereford - inland
    'HX': 0.55,   # Halifax - Pennine
    'L': 0.60,    # Liverpool - Mersey
    'LA': 0.65,   # Lancaster - near coast
    'LE': 0.50,   # Leicester - central
    'LS': 0.55,   # Leeds - Yorkshire, winter salting
    'LN': 0.55,   # Lincoln - flat, winter salting
    'LU': 0.50,   # Luton - inland
    'M': 0.55,    # Manchester - urban
    'ME': 0.55,   # Medway - Thames influence
    'MK': 0.50,   # Milton Keynes - inland
    'N': 0.55,    # North London - urban
    'NG': 0.50,   # Nottingham - central
    'NN': 0.50,   # Northampton - central
    'NR': 0.55,   # Norwich - East Anglia, some coast
    'NW': 0.55,   # Northwest London - urban
    'OL': 0.55,   # Oldham - Greater Manchester
    'OX': 0.50,   # Oxford - inland
    'PE': 0.55,   # Peterborough - fens, winter flooding
    'PR': 0.60,   # Preston - near coast
    'RG': 0.50,   # Reading - inland
    'S': 0.55,    # Sheffield - Pennine
    'SE': 0.55,   # Southeast London - urban
    'SK': 0.55,   # Stockport - Greater Manchester
    'SL': 0.50,   # Slough - inland
    'SM': 0.50,   # Sutton - South London
    'SN': 0.50,   # Swindon - inland
    'SP': 0.50,   # Salisbury - inland
    'ST': 0.55,   # Stoke - Potteries
    'SW': 0.55,   # Southwest London - urban
    'SY': 0.55,   # Shrewsbury - Welsh border
    'TF': 0.55,   # Telford - Midlands
    'W': 0.55,    # West London - urban
    'WA': 0.55,   # Warrington - Mersey
    'WC': 0.55,   # Central London - urban
    'WF': 0.55,   # Wakefield - Yorkshire
    'WN': 0.55,   # Wigan - Greater Manchester
    'WR': 0.50,   # Worcester - inland
    'WS': 0.50,   # Walsall - Midlands
    'WV': 0.55,   # Wolverhampton - Midlands
    'YO': 0.55,   # York - Yorkshire

    # LOW CORROSION RISK (0.4-0.5) - Sheltered inland
    'AL': 0.45,   # St Albans - sheltered
    'CM': 0.50,   # Chelmsford - inland Essex
    'CO': 0.55,   # Colchester - near coast but sheltered
    'CR': 0.50,   # Croydon - South London
    'DA': 0.50,   # Dartford - Thames
    'GU': 0.45,   # Guildford - Surrey hills
    'HA': 0.50,   # Harrow - Northwest London
    'HP': 0.45,   # Hemel Hempstead - Chilterns
    'IG': 0.50,   # Ilford - East London
    'IP': 0.55,   # Ipswich - near coast
    'KT': 0.50,   # Kingston - Southwest London
    'RM': 0.50,   # Romford - East London
    'TW': 0.50,   # Twickenham - Southwest London
    'UB': 0.50,   # Southall - West London
    'WD': 0.45,   # Watford - Hertfordshire

    # SCOTLAND (higher due to winter conditions)
    'DD': 0.65,   # Dundee - East coast
    'DG': 0.65,   # Dumfries - Southwest Scotland
    'EH': 0.60,   # Edinburgh - East coast influence
    'FK': 0.60,   # Falkirk - Central Scotland
    'G': 0.60,    # Glasgow - West Scotland
    'KA': 0.70,   # Kilmarnock - Ayrshire coast
    'KY': 0.65,   # Kirkcaldy - Fife coast
    'ML': 0.55,   # Motherwell - Central Scotland

    # WALES (varied - coastal vs valleys)
    'NP': 0.60,   # Newport - South Wales
    'SY': 0.55,   # Shrewsbury/Welsh border

    # NORTHERN IRELAND
    'BT': 0.65,   # Belfast - all NI postcodes
}

# Default corrosion index for unknown areas
DEFAULT_CORROSION_INDEX = 0.50


def extract_postcode_area(postcode: str) -> Optional[str]:
    """
    Extract the area code from a UK postcode.

    Args:
        postcode: Full or partial UK postcode

    Returns:
        Area code (1-2 letters) or None if invalid
    """
    # Normalize: uppercase, remove spaces
    postcode = postcode.strip().upper().replace(" ", "")

    if not postcode:
        return None

    match = POSTCODE_AREA_PATTERN.match(postcode)
    if match:
        return match.group(1)
    return None


def get_corrosion_index(postcode: str) -> float:
    """
    Get corrosion index for a UK postcode.

    Args:
        postcode: UK postcode (full or partial)

    Returns:
        Corrosion index (0.0-1.0), default 0.5 if unknown
    """
    area = extract_postcode_area(postcode)
    if area:
        return CORROSION_INDEX_BY_AREA.get(area, DEFAULT_CORROSION_INDEX)
    return DEFAULT_CORROSION_INDEX


def get_station_strictness_bias() -> float:
    """
    Get default station strictness bias.

    For MVP, we use neutral (0.0) since we don't have station data.

    Returns:
        Station strictness bias (0.0 = neutral)
    """
    return 0.0


def validate_postcode(postcode: str) -> Dict[str, any]:
    """
    Validate a UK postcode format.

    Args:
        postcode: Postcode to validate

    Returns:
        Dict with 'valid', 'normalized', and optional 'error'
    """
    # UK postcode regex (simplified)
    # Full: AB12 3CD or AB1 2CD
    # Partial: AB12 or AB1
    uk_postcode_pattern = re.compile(
        r'^[A-Z]{1,2}[0-9][A-Z0-9]?\s*[0-9][A-Z]{2}$|'  # Full postcode
        r'^[A-Z]{1,2}[0-9][A-Z0-9]?$'  # Outward code only
    )

    normalized = postcode.strip().upper()

    if not normalized:
        return {'valid': False, 'error': 'Postcode is required'}

    if uk_postcode_pattern.match(normalized):
        return {'valid': True, 'normalized': normalized}
    else:
        return {
            'valid': False,
            'error': 'Invalid UK postcode format',
            'normalized': normalized
        }
