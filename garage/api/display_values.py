"""Display arithmetic for a qualified model package, implemented once: the rounded percentage and the server-side rank score.

q = pp * round(100 * p / pp), ties to even (amendment section 6; pp = DisplayPolicy.rounding_pp, which DisplayPolicy.problem() pins to 5).
"Ties to even" does not say WHICH value is rounded, and the three candidate readings disagree on printed ties such as p = .175, .325 and
.575 (T2c/qual_v2/REPORT.md section 4 finding 4, open owner decision O9):

  float_expression  round the float expression 100*p/pp          (what the amendment text and DisplayPolicy.band_for's comment describe)
  decimal_repr      round the exact decimal of the printed probability, Decimal(repr(p)); the scorer prints p as its shortest round-trip
                    repr, so this is "the number as written"
  binary_exact      round the exact binary value of the float p, using exact rational arithmetic

There is deliberately NO default reading. `round_percent` requires one; the provider requires it to be configured before it will serve a
qualified bundle (providers/__init__.py: GARAGE_PERCENT_ROUNDING_READING). Pure functions, no I/O.
"""
from __future__ import annotations

import math
from decimal import Decimal
from fractions import Fraction

READINGS = ("float_expression", "decimal_repr", "binary_exact")


def _prob(p) -> float:
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0.0 <= p <= 1.0:
        raise ValueError("p must be a finite probability in [0, 1]")
    return float(p)


def round_percent(p, reading: str, rounding_pp: int = 5) -> int:
    """The displayed percentage for probability p under the named reading. `reading` is required and must be one of READINGS."""
    if reading not in READINGS:
        raise ValueError("rounding reading must be one of %s" % (READINGS,))
    if isinstance(rounding_pp, bool) or not isinstance(rounding_pp, int) or rounding_pp <= 0:
        raise ValueError("rounding_pp must be a positive integer")
    p = _prob(p)
    if reading == "float_expression":
        n = round(100 * p / rounding_pp)                                        # float arithmetic, ties to even on the float value
    elif reading == "decimal_repr":
        n = round(Fraction(Decimal(repr(p))) * 100 / rounding_pp)               # exact; Fraction.__round__ is ties to even
    else:
        n = round(Fraction(p) * 100 / rounding_pp)                              # exact binary value of the double
    return int(rounding_pp * n)


def rank_score(p) -> float:
    """The unrounded probability, used only to order bookings on the server (it never reaches a client: ranking.py exposes ordinals only)."""
    return _prob(p)
