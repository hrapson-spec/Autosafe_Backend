import pandas as pd

def get_age_band(age):
    if pd.isna(age): return 'Unknown'
    if age < 3: return '0-3'
    elif age < 6: return '3-5'
    elif age < 11: return '6-10'
    elif age < 16: return '10-15'
    else: return '15+'

def get_mileage_band(miles):
    if pd.isna(miles) or miles < 0: return 'Unknown'
    if miles < 30000: return '0-30k'
    if miles < 60000: return '30k-60k'
    if miles < 100000: return '60k-100k'
    return '100k+'
