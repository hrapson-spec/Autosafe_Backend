"""
report_service._sqlite_ladder (and its Postgres sibling,
database.get_risk_v2_banded / get_risk) match a requested model_id
against stored rows with `model_id = ? OR model_id LIKE ? || ' %'`, so
that e.g. a lookup for "FORD FIESTA" also aggregates variant rows like
"FORD FIESTA VARIANT". Before the fix, the *same* unescaped model_id
value was bound to both the `=` and the `LIKE` sides. SQL LIKE treats a
literal `%` or `_` inside that value as a wildcard regardless of how it
arrived (parameter binding only prevents code injection, not wildcard
interpretation) -- so a make/model string that happens to contain one of
those characters (typed by a user, or produced by any upstream text that
isn't guaranteed clean) silently broadens the match to include unrelated
models, aggregating their risk data into what looks like a specific
model's answer.

This test seeds a decoy row for a completely unrelated "model" and shows
that a nonsense request containing a literal '%' incorrectly pulls that
decoy row into its aggregate before the fix, and correctly finds no data
after it (report_service.escape_like paired with an ESCAPE '\\' clause).
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_service import _sqlite_ladder  # noqa: E402
from report_test_helpers import seeded_sqlite  # noqa: E402


def _seed_decoy(conn):
    """Two rows that have nothing to do with a real '%'- or '_'-named
    model, but shaped to be exactly what the UNESCAPED bug's LIKE pattern
    matches for those two requested values."""
    conn.executemany(
        "INSERT INTO risks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            # 'TESTMAKE % %': starts with 'TESTMAKE ', then anything,
            # then a space, then anything.
            ('TESTMAKE UNRELATED DECOY MODEL', '3-5', '30k-60k',
             999, 999, 1.0, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9),
            # 'TESTMAKE _ %': starts with 'TESTMAKE ', then exactly one
            # character, then a space, then anything.
            ('TESTMAKE X DECOY2', '3-5', '30k-60k',
             777, 777, 1.0, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9),
        ],
    )
    conn.commit()


def test_wildcard_in_requested_model_id_does_not_match_unrelated_decoy():
    conn = seeded_sqlite()
    _seed_decoy(conn)

    # No vehicle is ever literally named "TESTMAKE %"; a lookup for it
    # must find nothing, not silently borrow an unrelated model's data.
    result = _sqlite_ladder(conn, 'TESTMAKE %', '3-5', '30k-60k')

    assert result is None, (
        f"expected no match for a nonsense '%'-containing model_id, got {result!r} -- "
        "the LIKE clause's wildcard escaping regressed"
    )


def test_underscore_in_requested_model_id_does_not_match_unrelated_decoy():
    conn = seeded_sqlite()
    _seed_decoy(conn)

    # '_' matches exactly one character; still must not turn into a
    # wildcard purely because it's present in the requested text.
    result = _sqlite_ladder(conn, 'TESTMAKE _', '3-5', '30k-60k')

    assert result is None

    result_step3 = _sqlite_ladder(conn, 'TESTMAKE _', '99+', None)
    assert result_step3 is None


def test_legitimate_variant_matching_is_unaffected():
    """The escaping fix must not break the exact behavior it's built on
    top of: a real model_id with no wildcard characters still aggregates
    its own ' %'-suffixed variant rows (TESTMAKE TESTMODEL + TESTMAKE
    TESTMODEL VARIANT, from report_test_helpers.SEEDED_RISKS_ROWS)."""
    conn = seeded_sqlite()
    _seed_decoy(conn)

    result = _sqlite_ladder(conn, 'TESTMAKE TESTMODEL', '3-5', '30k-60k')

    assert result is not None
    # 500 (base) + 50 (VARIANT) from SEEDED_RISKS_ROWS; the decoy (999)
    # must not be included.
    assert result['total_tests'] == 550
