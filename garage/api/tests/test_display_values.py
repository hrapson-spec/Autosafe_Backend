"""Percent rounding and rank_score (route-to-live step 9). The exact 'ties to even' reading is OPEN owner decision O9, so all three
candidate readings are implemented and tested; none is a default. Oracles: the vendored qual_v2 reference (readings A and B) and an
independent Decimal-based exact-binary computation (reading C)."""
import math
from decimal import Decimal, ROUND_HALF_EVEN

import pytest

from garage.api.display_values import READINGS, rank_score, round_percent
from garage.api.qual_v2.display_ref import percent_rounded, percent_rounded_decimal

A, B, C = READINGS
E = (20, 35)
TIES = ["%.3f" % (k / 1000) for k in range(25, 1000, 50)]      # 0.025 ... 0.975: 100*p/5 is a half-integer when printed


def band(q):
    return "low" if q < E[0] else "medium" if q < E[1] else "high"


def exact_binary_oracle(p):
    return int(5 * (Decimal(p) * 100 / 5).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def test_three_readings_are_the_documented_ones():
    assert READINGS == ("float_expression", "decimal_repr", "binary_exact")


@pytest.mark.parametrize("p,want", [(0.175, (20, 20, 15)), (0.325, (30, 30, 35)), (0.575, (55, 60, 55))])
def test_flagged_ties(p, want):
    assert tuple(round_percent(p, r) for r in READINGS) == want


def test_flagged_ties_change_the_band_at_the_provisional_edges():
    assert [band(round_percent(0.175, r)) for r in READINGS] == ["medium", "medium", "low"]
    assert [band(round_percent(0.325, r)) for r in READINGS] == ["medium", "medium", "high"]
    assert [band(round_percent(0.575, r)) for r in READINGS] == ["high"] * 3


def test_every_printed_tie_matches_its_oracle():
    for t in TIES:
        p = float(t)
        assert round_percent(p, A) == percent_rounded(p), t
        assert round_percent(p, B) == percent_rounded_decimal(t), t
        assert round_percent(p, C) == exact_binary_oracle(p), t


def test_disagreements_between_readings_are_exactly_those_qual_v2_measured():
    a = {t: round_percent(float(t), A) for t in TIES}
    b = {t: round_percent(float(t), B) for t in TIES}
    c = {t: round_percent(float(t), C) for t in TIES}
    assert [t for t in TIES if a[t] != b[t]] == ["0.575"]
    assert len([t for t in TIES if a[t] != c[t]]) == 9


@pytest.mark.parametrize("reading", READINGS)
def test_non_tie_boundaries_and_ends(reading):
    for p, q in [(0.1749, 15), (0.1751, 20), (0.18, 20), (0.3249, 30), (0.3251, 35), (0.0, 0), (1.0, 100), (0, 0), (1, 100)]:
        assert round_percent(p, reading) == q, p


@pytest.mark.parametrize("reading", READINGS)
def test_properties_multiple_of_pp_monotone_and_within_half_a_step(reading):
    last = -1
    for i in range(0, 10001):
        p = i / 10000
        q = round_percent(p, reading)
        assert q % 5 == 0 and 0 <= q <= 100 and q >= last
        assert abs(100 * p - q) <= 2.5 + 1e-9
        last = q


def test_other_rounding_steps_are_supported_but_pp_is_validated():
    assert round_percent(0.37, A, 1) == 37 and round_percent(0.125, B, 10) == 10
    for bad in (0, -5, 2.5, True, None):
        with pytest.raises(ValueError):
            round_percent(0.3, A, bad)


def test_reading_is_required_there_is_no_default():
    for bad in (None, "", "float", "FLOAT_EXPRESSION", "half_even", 1):
        with pytest.raises(ValueError):
            round_percent(0.3, bad)
    with pytest.raises(TypeError):
        round_percent(0.3)                       # no default argument


@pytest.mark.parametrize("bad", [-0.1, 1.0000001, float("nan"), float("inf"), True, "0.5", None, [0.5]])
@pytest.mark.parametrize("reading", READINGS)
def test_bad_probabilities_are_refused(bad, reading):
    with pytest.raises(ValueError):
        round_percent(bad, reading)
    with pytest.raises(ValueError):
        rank_score(bad)


def test_rank_score_is_the_unrounded_probability():
    for p in (0.0, 0.175, 0.2537123, 0.575, 1.0, 1):
        s = rank_score(p)
        assert isinstance(s, float) and s == float(p) and math.isfinite(s)
    # rank_score separates probabilities that round to the same percentage
    assert round_percent(0.2501, A) == round_percent(0.2549, A) == 25 and rank_score(0.2501) < rank_score(0.2549)
