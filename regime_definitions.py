"""
Vehicle Regime Definitions
==========================

Centralized definitions for vehicle regime classification.

Used by:
- RegimeAwareHierarchicalFeatures (hierarchical_make_adjustment.py)
- Analysis scripts (analyze_regime_hierarchy.py, ablate_make_by_regime.py)
- Training pipelines

Regimes:
- Car: Default passenger vehicles (91.5% of data, 25.1% fail rate)
- Motorcycle: Two-wheeled vehicles (6.6% of data, 19.3% fail rate)
- Commercial: Vans, trucks, buses (1.9% of data, 25.9% fail rate)

Created: 2026-01-08
"""

from typing import Set, Dict

# =============================================================================
# Motorcycle Makes
# =============================================================================
# Expanded list including both pure motorcycle brands and dual-use brands
# that are predominantly motorcycle in the UK MOT data.

MOTORCYCLE_MAKES: Set[str] = {
    # Pure motorcycle brands
    'LEXMOTO', 'KAWASAKI', 'TRIUMPH', 'KTM', 'DUCATI', 'APRILIA', 'PIAGGIO',
    'VESPA', 'ROYAL ENFIELD', 'INDIAN', 'INDIAN MOTORCYCLE', 'MOTO GUZZI',
    'BENELLI', 'SYM', 'KYMCO', 'ZONTES', 'KEEWAY', 'SINNIS', 'HYOSUNG',
    'WK', 'HERALD', 'LAMBRETTA', 'MV AGUSTA', 'GAS GAS',
    'BETA', 'SHERCO', 'TM', 'FANTIC', 'SWM', 'RIEJU', 'OSSA', 'BULTACO',
    'MONTESA', 'AJS', 'MATCHLESS', 'BSA', 'NORTON', 'VELOCETTE', 'ARIEL',
    'MUTT', 'CCM', 'ZERO', 'ENERGICA', 'LIVEWIRE',

    # Major brands with motorcycle divisions (classified as motorcycle
    # because in UK MOT data these makes appear >90% as motorcycles)
    'HARLEY-DAVIDSON', 'HARLEY DAVIDSON',
    'HONDA',    # Honda motorcycles dominate UK MOT motorcycle data
    'YAMAHA',   # Yamaha motorcycles dominate UK MOT motorcycle data
    'SUZUKI',   # Suzuki motorcycles dominate UK MOT motorcycle data
    'HUSQVARNA',  # Predominantly motorcycles in UK
}

# =============================================================================
# Commercial Vehicle Makes
# =============================================================================
# Makes that are predominantly commercial vehicles (vans, trucks, buses)
# in the UK MOT data.

COMMERCIAL_MAKES: Set[str] = {
    # Van/LCV manufacturers
    'LDV', 'MAXUS',

    # Truck/HGV manufacturers
    'IVECO', 'DAF', 'SCANIA', 'MAN', 'VOLVO', 'ISUZU', 'HINO',
    'FUSO', 'UD', 'DENNIS', 'ERF', 'SEDDON', 'LEYLAND',
}

# =============================================================================
# Commercial Model Keywords (for model name inference)
# =============================================================================
# These identify commercial vehicles within mainstream passenger car makes.
# Used by infer_body_type() to flag Transit, Sprinter, etc.

COMMERCIAL_MODEL_KEYWORDS: Set[str] = {
    # Ford
    'TRANSIT', 'RANGER', 'TOURNEO',
    # Mercedes
    'SPRINTER', 'VITO', 'CITAN',
    # VW
    'TRANSPORTER', 'CRAFTER', 'CADDY', 'AMAROK',
    # Vauxhall/Opel
    'VIVARO', 'MOVANO', 'COMBO',
    # Renault
    'TRAFIC', 'MASTER', 'KANGOO',
    # Peugeot
    'BOXER', 'EXPERT', 'PARTNER',
    # Citroen
    'RELAY', 'DISPATCH', 'BERLINGO',
    # Fiat
    'DUCATO', 'SCUDO', 'DOBLO', 'TALENTO',
    # Nissan
    'NV200', 'NV300', 'NV400', 'NAVARA',
    # Toyota
    'HIACE', 'HILUX', 'PROACE',
    # Mitsubishi
    'L200',
}

# =============================================================================
# Constants
# =============================================================================

REGIMES = ('Car', 'Motorcycle', 'Commercial')
DEFAULT_REGIME = 'Car'

POWERTRAINS = ('ICE', 'HEV', 'PHEV', 'BEV')
DEFAULT_POWERTRAIN = 'ICE'

BODY_TYPES = ('Passenger', 'Commercial')
DEFAULT_BODY_TYPE = 'Passenger'

# =============================================================================
# Powertrain Keywords (for model name inference)
# =============================================================================

# BEV (Battery Electric Vehicle) - check first as most specific
BEV_KEYWORDS = {
    'ELECTRIC', ' EV ', ' EV$', '-EV ', 'E-TRON', 'I-PACE', 'IPACE',
    'MODEL S', 'MODEL 3', 'MODEL X', 'MODEL Y',  # Tesla
    'LEAF', 'ZOE', 'E-GOLF', 'E-208', 'E-2008', 'CORSA-E',
    'MX-30', 'ID.3', 'ID.4', 'ID.5', 'ENYAQ', 'KONA ELECTRIC',
    'IONIQ 5', 'IONIQ 6', 'EV6', 'MUSTANG MACH-E', 'E-NIRO',
    'BORN', 'ATTO', 'SEAL', 'DOLPHIN',  # BYD/Cupra
}

# PHEV (Plug-in Hybrid Electric Vehicle)
PHEV_KEYWORDS = {'PHEV', 'PLUG-IN', 'PLUG IN', 'OUTLANDER P-HEV'}

# HEV (Hybrid Electric Vehicle) - check after PHEV
HEV_KEYWORDS = {'HEV', 'HYBRID', 'MHEV', 'PRIUS', 'YARIS HYBRID'}


# =============================================================================
# Functions
# =============================================================================

def infer_regime(make: str) -> str:
    """
    Infer vehicle regime from make name.

    Args:
        make: Vehicle make name (case-insensitive)

    Returns:
        One of: 'Car', 'Motorcycle', 'Commercial'

    Examples:
        >>> infer_regime('LEXMOTO')
        'Motorcycle'
        >>> infer_regime('LDV')
        'Commercial'
        >>> infer_regime('FORD')
        'Car'
    """
    if make is None:
        return DEFAULT_REGIME

    make_upper = str(make).upper().strip()

    if make_upper in MOTORCYCLE_MAKES:
        return 'Motorcycle'
    elif make_upper in COMMERCIAL_MAKES:
        return 'Commercial'
    else:
        return 'Car'


def infer_powertrain(model_id: str) -> str:
    """
    Infer powertrain type from model name.

    Args:
        model_id: Vehicle model identifier (e.g., "TOYOTA YARIS HEV CVT")

    Returns:
        One of: 'ICE', 'HEV', 'PHEV', 'BEV'

    Examples:
        >>> infer_powertrain('TOYOTA YARIS EXCEL HEV CVT')
        'HEV'
        >>> infer_powertrain('TESLA MODEL 3')
        'BEV'
        >>> infer_powertrain('FORD FOCUS')
        'ICE'
    """
    if model_id is None:
        return DEFAULT_POWERTRAIN

    model_upper = str(model_id).upper().strip()

    # BEV indicators (check first - most specific)
    for kw in BEV_KEYWORDS:
        if kw in model_upper:
            return 'BEV'

    # PHEV indicators
    for kw in PHEV_KEYWORDS:
        if kw in model_upper:
            return 'PHEV'

    # HEV indicators (check after PHEV)
    for kw in HEV_KEYWORDS:
        if kw in model_upper:
            return 'HEV'

    return 'ICE'


def infer_body_type(model_id: str) -> str:
    """
    Infer body type (Passenger vs Commercial) from model name.

    This captures commercial vehicles within mainstream makes that would
    otherwise be classified as 'Car' regime (e.g., Ford Transit, Mercedes Sprinter).

    Args:
        model_id: Vehicle model identifier (e.g., "FORD TRANSIT CUSTOM 300")

    Returns:
        One of: 'Passenger', 'Commercial'

    Examples:
        >>> infer_body_type('FORD TRANSIT CUSTOM 300LEADER EBLUE')
        'Commercial'
        >>> infer_body_type('FORD FIESTA')
        'Passenger'
        >>> infer_body_type('MERCEDES-BENZ SPRINTER 315')
        'Commercial'
        >>> infer_body_type('MERCEDES-BENZ C 220')
        'Passenger'
    """
    if model_id is None:
        return DEFAULT_BODY_TYPE

    model_upper = str(model_id).upper().strip()

    for kw in COMMERCIAL_MODEL_KEYWORDS:
        if kw in model_upper:
            return 'Commercial'

    return 'Passenger'


def get_regime_makes() -> Dict[str, Set[str]]:
    """
    Return dict mapping regime -> set of known makes.

    Note: 'Car' is the default regime and not enumerated.

    Returns:
        Dict with keys 'Motorcycle', 'Commercial', 'Car'
    """
    return {
        'Motorcycle': MOTORCYCLE_MAKES.copy(),
        'Commercial': COMMERCIAL_MAKES.copy(),
        'Car': set(),  # Default, not enumerated
    }


def get_regime_baseline_rates() -> Dict[str, float]:
    """
    Return approximate baseline failure rates by regime.

    These are from analysis on 2023 dev_set:
    - Car: 25.1% failure rate
    - Motorcycle: 19.3% failure rate
    - Commercial: 25.9% failure rate

    Returns:
        Dict mapping regime -> baseline failure rate
    """
    return {
        'Car': 0.251,
        'Motorcycle': 0.193,
        'Commercial': 0.259,
    }
