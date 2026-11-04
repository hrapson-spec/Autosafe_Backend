"""Reference percent/band arithmetic for the display policy (amendment section 6): q = 5*round(100*p/5), ties-to-even; low q<e0, medium e0<=q<e1, high q>=e1.

Reference only; the app owns the real implementation (step 9). It exists so the tie behaviour is tested rather than assumed.
`percent_rounded` rounds the FLOAT expression 100*p/5 (what the amendment text and DisplayPolicy.band_for's comment describe). `percent_rounded_decimal` rounds the
exact decimal value of the printed probability. They differ on some printed ties because 0.175 etc. are not exactly representable; see tests/test_display_ref.py.
"""
from __future__ import annotations
from decimal import Decimal, ROUND_HALF_EVEN


def percent_rounded(p):
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0.0 <= p <= 1.0:
        raise ValueError("p must be a probability in [0,1]")
    return int(5 * round(100 * p / 5))


def percent_rounded_decimal(p_text):
    """Exact decimal reading of a probability given as text (e.g. '0.175')."""
    d = Decimal(p_text)
    if not Decimal(0) <= d <= Decimal(1):
        raise ValueError("p must be in [0,1]")
    return int(5 * (d * 100 / 5).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def band_for(q, edges):
    return "low" if q < edges[0] else "medium" if q < edges[1] else "high"
