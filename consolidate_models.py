"""
Make/Model Consolidation Script
Creates canonical mappings for makes and models to simplify user selection.
"""
import os
import re

# Canonical Make Mappings - normalize variations to standard names
CANONICAL_MAKES = {
    # Common typos and variations
    "0PEL": "OPEL",
    "VOLKSWAGON": "VOLKSWAGEN",
    "MERCEDES": "MERCEDES-BENZ",
    "HARLEY": "HARLEY-DAVIDSON",
    "LAND": "LAND ROVER",
    "ALFA": "ALFA ROMEO",
    "ROLLS": "ROLLS-ROYCE",
    "MOTO": "MOTO GUZZI",
    
    # Excluded garbage makes (return None to filter out)
    ".": None,
    "": None,
    "A": None,
    "THE": None,
    "AND": None,
    "FOR": None,
    "0": None,
    "1": None,
    "2": None,
    "3": None,
}

# Top 50 Major Makes (curated list)
MAJOR_MAKES = [
    "AUDI", "BMW", "CHEVROLET", "CITROEN", "DACIA", "DODGE", "FERRARI",
    "FIAT", "FORD", "HONDA", "HYUNDAI", "ISUZU", "IVECO", "JAGUAR", "JEEP",
    "KIA", "LAMBORGHINI", "LAND ROVER", "LEXUS", "MASERATI", "MAZDA",
    "MERCEDES-BENZ", "MG", "MINI", "MITSUBISHI", "NISSAN", "OPEL", "PEUGEOT",
    "PORSCHE", "RENAULT", "ROLLS-ROYCE", "SAAB", "SEAT", "SKODA", "SMART",
    "SSANGYONG", "SUBARU", "SUZUKI", "TESLA", "TOYOTA", "VAUXHALL", "VOLKSWAGEN",
    "VOLVO", "ALFA ROMEO", "ASTON MARTIN", "BENTLEY", "CHRYSLER", "INFINITI",
    # Motorcycles
    "HARLEY-DAVIDSON", "YAMAHA", "KAWASAKI", "DUCATI", "TRIUMPH", "KTM",
    "APRILIA", "HUSQVARNA", "PIAGGIO", "VESPA"
]

# Model extraction patterns - extract base model from detailed variant
MODEL_PATTERNS = [
    # Remove engine specs: "1.4L", "2.0 TDCI", "125CC"
    (r'\s+\d+\.?\d*\s*(L|CC|TDCI|TSI|TDI|HDI|CDTI|CRDI|VVT|VTEC)\b', ''),
    # Remove door counts: "3 DOOR", "5DR"
    (r'\s+\d+\s*(DOOR|DR)\b', ''),
    # Remove transmission: "MANUAL", "AUTOMATIC", "AUTO", "CVT"
    (r'\s+(MANUAL|AUTOMATIC|AUTO|CVT|DSG|PDK)\b', ''),
    # Remove body types at end: "HATCHBACK", "SALOON", "ESTATE"
    (r'\s+(HATCHBACK|SALOON|ESTATE|COUPE|CONVERTIBLE|SUV|MPV|VAN)\s*$', ''),
]

def normalize_make(raw_make: str) -> str:
    """Convert raw make to canonical form. Returns None for non-major makes."""
    if not raw_make:
        return None
    
    make = raw_make.upper().strip()
    
    # Check explicit mappings first
    if make in CANONICAL_MAKES:
        return CANONICAL_MAKES[make]  # May return None for garbage
    
    # Check if it's a known major make
    if make in MAJOR_MAKES:
        return make
    
    # Try partial matches for compound makes (e.g., "LAND" -> "LAND ROVER")
    for major in MAJOR_MAKES:
        if make.startswith(major.split()[0]) and major.split()[0] == make:
            return major
    
    # Return None for unknown makes - we only want curated makes
    return None

def extract_base_model(model_id: str, make: str) -> str:
    """Extract base model name from full model_id."""
    if not model_id or not make:
        return None
    
    # Remove make from start of model_id
    model = model_id.upper()
    if model.startswith(make.upper()):
        model = model[len(make):].strip()
    
    # Skip entries that start with special chars, numbers, or are too short
    if not model or len(model) < 2:
        return None
    if model[0] in './-()':
        return None
    
    # Apply cleaning patterns
    for pattern, replacement in MODEL_PATTERNS:
        model = re.sub(pattern, replacement, model, flags=re.IGNORECASE)
    
    # Get the first word as base model name
    words = model.split()
    if not words:
        return None
    
    first_word = words[0]
    
    # Skip if first word is a trim level, not a model
    trim_words = ['ZETEC', 'ST', 'GTI', 'RS', 'AMG', 'SPORT', 'LINE', 'ACTIVE', 
                  'TITANIUM', 'VIGNALE', 'GHIA', 'LX', 'SE', 'SRI', 'EDITION',
                  'STYLE', 'TREND', 'BASE', 'HYBRID', 'TURBO', 'DIESEL', 'PETROL',
                  'MOTORHOME', 'CAMPER', 'VAN', 'BUS', 'MINIBUS', 'PICKUP', 'UNCLASSIFIED']
    if first_word in trim_words:
        return None
    
    # Handle BMW/AUDI/MERCEDES - allow alphanumeric models
    if make.upper() in ['BMW', 'AUDI', 'MERCEDES-BENZ', 'PEUGEOT', 'CITROEN']:
        if re.match(r'^[A-Z]?\d', first_word):
            # Normalize BMW: 320D, 330I, 520D -> 3 SERIES, 5 SERIES
            if make.upper() == 'BMW' and re.match(r'^\d', first_word):
                series_num = first_word[0]
                return f"{series_num} SERIES"
            return first_word
    
    # For other makes, skip if model is purely numeric or starts with number
    if re.match(r'^\d', first_word):
        return None
    
    # Skip very short or suspicious names
    if len(first_word) < 2:
        return None
    
    # Only allow models that are mostly alphabetic
    alpha_count = sum(1 for c in first_word if c.isalpha())
    if alpha_count < len(first_word) * 0.6:  # At least 60% alphabetic
        return None
    
    return first_word

def get_canonical_models_for_make(make: str) -> dict:
    """Get mapping of raw models to canonical models for a given make."""
    # This would be populated from database analysis
    # Key popular models by make
    KNOWN_MODELS = {
        "FORD": ["FIESTA", "FOCUS", "MONDEO", "PUMA", "KUGA", "MUSTANG", "RANGER", "TRANSIT", "ECOSPORT", "S-MAX", "GALAXY", "KA", "C-MAX", "B-MAX"],
        "VAUXHALL": ["CORSA", "ASTRA", "INSIGNIA", "MOKKA", "CROSSLAND", "GRANDLAND", "ADAM", "ZAFIRA", "MERIVA", "COMBO"],
        "VOLKSWAGEN": ["GOLF", "POLO", "PASSAT", "TIGUAN", "T-ROC", "TOURAN", "TOUAREG", "ARTEON", "ID.3", "ID.4", "UP", "BEETLE", "SCIROCCO"],
        "BMW": ["1 SERIES", "2 SERIES", "3 SERIES", "4 SERIES", "5 SERIES", "6 SERIES", "7 SERIES", "8 SERIES", "X1", "X2", "X3", "X4", "X5", "X6", "X7", "Z4", "I3", "I4", "IX"],
        "AUDI": ["A1", "A3", "A4", "A5", "A6", "A7", "A8", "Q2", "Q3", "Q5", "Q7", "Q8", "TT", "R8", "E-TRON"],
        "MERCEDES-BENZ": ["A CLASS", "B CLASS", "C CLASS", "E CLASS", "S CLASS", "CLA", "CLS", "GLA", "GLB", "GLC", "GLE", "GLS", "AMG GT", "SL", "SLC"],
        "TOYOTA": ["YARIS", "COROLLA", "CAMRY", "RAV4", "C-HR", "AYGO", "PRIUS", "LAND CRUISER", "HILUX", "SUPRA", "GR86"],
        "HONDA": ["CIVIC", "JAZZ", "HR-V", "CR-V", "ACCORD", "NSX", "E"],
        "NISSAN": ["MICRA", "JUKE", "QASHQAI", "X-TRAIL", "LEAF", "NAVARA", "GT-R", "370Z"],
        "PEUGEOT": ["108", "208", "308", "508", "2008", "3008", "5008", "PARTNER", "RIFTER"],
        "RENAULT": ["CLIO", "MEGANE", "CAPTUR", "KADJAR", "SCENIC", "TWINGO", "ZOE", "TRAFIC"],
        "KIA": ["PICANTO", "RIO", "CEED", "SPORTAGE", "SORENTO", "NIRO", "STONIC", "EV6"],
        "HYUNDAI": ["I10", "I20", "I30", "TUCSON", "KONA", "SANTA FE", "IONIQ"],
        "FIAT": ["500", "PANDA", "TIPO", "500X", "500L", "DUCATO", "DOBLO"],
    }
    return KNOWN_MODELS.get(make.upper(), [])

if __name__ == "__main__":
    # Test the functions
    print("Testing make normalization:")
    test_makes = ["FORD", "MERCEDES", "LAND", "0PEL", "VOLKSWAGON", "."]
    for m in test_makes:
        print(f"  {m} -> {normalize_make(m)}")
    
    print("\nTesting model extraction:")
    test_models = [
        ("FORD FIESTA ZETEC 1.4L 5 DOOR MANUAL HATCHBACK", "FORD"),
        ("FORD FOCUS ST-LINE X 1.0 ECOBOOST 125", "FORD"),
        ("BMW 320D M SPORT AUTO", "BMW"),
        ("VOLKSWAGEN GOLF GTI 2.0 TSI", "VOLKSWAGEN"),
    ]
    for model_id, make in test_models:
        print(f"  {model_id} -> {extract_base_model(model_id, make)}")
