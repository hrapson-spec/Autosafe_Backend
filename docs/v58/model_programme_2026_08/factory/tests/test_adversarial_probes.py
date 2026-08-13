"""Adversarial-review probes, ADOPTED into the shipped suite (2026-08-12).

Written by the red-team reviewer (out/FACTORY_REVIEW.md) as failing probes
against the pre-fix package; adopted verbatim in substance so the fixes stay
pinned. Probe -> defect map:

    P1 / P1b  B-1  inclusion_weight branched on the realised u, not the design
                   cell: enriched-stratum weighted totals inflated up to 2x.
    P2        m-5  defect_rows physical-order invariance (held empirically;
                   now guaranteed by list_sort in atoms.packet_struct_sql).
    P3        --   second-stage salt independence from the %100 panel family
                   (measurement probe; held).
    P4        B-2  prepare() left stale staging files -> ghost vehicles.
    P5        M-4  the AMBIGUOUS column also counted mixed non-definitive days;
                   resolved by rename + a strict variant.

Fixtures only -- the real lake is never touched.
"""
from datetime import date

import duckdb
import pytest

from conftest import make_config, run_factory
from factory import emit, packets, sampling
from factory.fixtures import FixtureLake, ItemRow, TestRow

RECIPE = emit.WindowRecipe("all", date(2005, 1, 1), date(2024, 1, 1))


# --- P1: Horvitz-Thompson weight must be a function of the design cell ------

def test_p1_weighted_estimator_is_unbiased_for_enriched_strata():
    """HT contract: weight = base / P(row selected). P(selected) for a
    stratum-eligible row is `enriched` REGARDLESS of its realised u.
    The weighted total of an enriched stratum must therefore recover
    base * N_stratum. If eligible rows with u < base carry weight 1.0,
    the stratum total is overestimated by factor (2 - base/enriched)."""
    rung = sampling.Rung("r", base=0.10, enriched=0.40)
    n = 200_000
    # exact uniform grid: u_i = (i + 0.5)/n  -> expectations are exact sums
    stratum = ["deep_history" if i % 4 == 0 else "none" for i in range(n)]
    n_eligible = sum(1 for s in stratum if s != "none")

    weighted_total = 0.0
    for i, s in enumerate(stratum):
        u = (i + 0.5) / n
        if s == "none":
            continue
        if rung.selects(u, s):
            weighted_total += rung.inclusion_weight(u, s)

    expected = rung.base * n_eligible          # what an unbiased HT sum recovers
    rel_err = weighted_total / expected - 1.0
    assert abs(rel_err) < 0.01, (
        f"enriched-stratum weighted total {weighted_total:.0f} vs unbiased "
        f"{expected:.0f}: relative error {rel_err:+.1%} "
        f"(predicted bias factor 2 - base/enriched = {2 - rung.base/rung.enriched:.2f})")


def test_p1b_weight_is_a_function_of_the_design_not_the_realised_u():
    """Two eligible rows differ only in u (both selected): same inclusion
    probability -> same HT weight."""
    rung = sampling.Rung("r", base=0.10, enriched=0.40)
    w_low = rung.inclusion_weight(0.05, "deep_history")
    w_high = rung.inclusion_weight(0.15, "deep_history")
    assert w_low == w_high, (
        f"same design cell, different weights: u=0.05 -> {w_low}, u=0.15 -> {w_high}")


def test_p1c_non_stratum_and_degenerate_cells_weigh_one():
    rung = sampling.Rung("r", base=0.10, enriched=0.40)
    assert rung.inclusion_weight(0.05, sampling.NO_STRATUM) == 1.0
    assert rung.inclusion_weight(0.05, "deep_history") == pytest.approx(0.25)
    flat = sampling.Rung("f", base=0.30, enriched=0.30)
    assert flat.inclusion_weight(0.10, "deep_history") == 1.0


# --- P2: defect_rows has no canonical order (D13 / determinism) -------------

def _two_item_prior(root: str, reverse_items: bool) -> FixtureLake:
    lake = FixtureLake(root)
    lake.add_test(TestRow(test_id=1, vehicle_id=9, test_date=date(2019, 3, 1),
                          outcome="FAIL", test_mileage=42_000))
    items = [ItemRow(test_id=1, rfr_id="20001", rfr_type_code="F"),
             ItemRow(test_id=1, rfr_id="20003", rfr_type_code="A")]
    if reverse_items:
        items = list(reversed(items))          # physical row order only
    for it in items:
        lake.add_item(it)
    lake.add_test(TestRow(test_id=2, vehicle_id=9, test_date=date(2020, 3, 1),
                          outcome="PASS", test_mileage=52_000))
    return lake


def test_p2_defects_json_is_invariant_to_items_physical_row_order(tmp_path):
    """Contract falsifier 2 (v2 amendment: applies to packet contents): shuffling
    same-day row order leaves every emitted value bit-identical. The items
    relation's physical order is exactly such an ordering choice."""
    _, _, pk_a = run_factory(_two_item_prior(str(tmp_path / "a"), False),
                             tmp_path / "ra", [RECIPE])
    _, _, pk_b = run_factory(_two_item_prior(str(tmp_path / "b"), True),
                             tmp_path / "rb", [RECIPE])
    proj_a = sorted(packets.d13_invariant_projection(p) for p in pk_a)
    proj_b = sorted(packets.d13_invariant_projection(p) for p in pk_b)
    assert proj_a == proj_b, (
        "packet contents (defects_json) changed under a pure physical re-ordering "
        "of the items relation:\nA=%r\nB=%r" % (proj_a, proj_b))


# --- P3: second-stage salt independence from the %100 panel family ----------

def test_p3_salted_u_is_uniform_within_a_panel_residue():
    """The frames are built ON panel inputs (hash(vehicle_id)%100 family).
    Sampling then thresholds u = hash(CAST(id AS VARCHAR) || salt)/2^64.
    If u is not uniform WITHIN a panel residue class, rung sizes and every
    HT weight are silently wrong on the panel substrate. Measure it."""
    con = duckdb.connect()
    n = 2_000_000
    u_expr = sampling.unit_hash_sql("v", sampling.SAMPLE_SALT)
    rows = con.execute(f"""
        WITH ids AS (SELECT range AS v FROM range(1, {n + 1}))
        SELECT
          count(*) FILTER (WHERE hash(v) % 100 = 0)                          AS n_panel_int,
          count(*) FILTER (WHERE hash(v) % 100 = 0 AND {u_expr} < 0.01)      AS hit_int,
          count(*) FILTER (WHERE hash(CAST(v AS VARCHAR)) % 100 = 0)         AS n_panel_str,
          count(*) FILTER (WHERE hash(CAST(v AS VARCHAR)) % 100 = 0
                             AND {u_expr} < 0.01)                            AS hit_str,
          count(*) FILTER (WHERE {u_expr} < 0.01)                            AS hit_all
        FROM ids
    """).fetchone()
    n_panel_int, hit_int, n_panel_str, hit_str, hit_all = rows
    overall = hit_all / n
    for label, n_panel, hit in (("int-hash panel", n_panel_int, hit_int),
                                ("varchar-hash panel", n_panel_str, hit_str)):
        rate = hit / n_panel
        # binomial 4-sigma band around the overall rate
        sigma = (overall * (1 - overall) / n_panel) ** 0.5
        assert abs(rate - overall) < 4 * sigma + 1e-12, (
            f"{label}: selection rate {rate:.5f} vs overall {overall:.5f} "
            f"(n={n_panel}) -- second-stage salt correlates with the panel family")
        print(f"{label}: n={n_panel}, rate={rate:.5f}, overall={overall:.5f}")


# --- P4: stale staging files must not survive prepare() ---------------------

def test_p4_prepare_does_not_leave_stale_staging(tmp_path):
    """prepare() must wipe its staging scope. A re-run with different inputs
    into the same --staging-dir must not scan ghost vehicles from the previous
    run."""
    stage = tmp_path / "shared_stage"

    lake1 = FixtureLake(str(tmp_path / "lake1"))
    lake1.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2019, 5, 1)))
    inputs1 = lake1.write()
    cfg1 = make_config(tmp_path / "c1", staging_dir=str(stage))
    f1 = emit.Factory(inputs1, cfg1)
    f1.connect()
    f1.preflight([2019], [RECIPE])
    prep1 = f1.prepare([2019], [RECIPE])

    # Second run, SAME staging dir, disjoint vehicle: the first run's rows
    # must not leak into this build.
    lake2 = FixtureLake(str(tmp_path / "lake2"))
    lake2.add_test(TestRow(test_id=2, vehicle_id=2, test_date=date(2019, 6, 1)))
    inputs2 = lake2.write()
    cfg2 = make_config(tmp_path / "c2", staging_dir=str(stage))
    f2 = emit.Factory(inputs2, cfg2)
    f2.connect()
    f2.preflight([2019], [RECIPE])
    prep2 = f2.prepare([2019], [RECIPE])

    vehicles = {row["vehicle_id"] for row, _pk in f2.scan(RECIPE)}
    assert vehicles == {2}, (
        f"scan of the second build returned vehicles {sorted(vehicles)}: "
        f"stale staging from the previous run leaked into the frame")
    # the manifest must carry the staged file counts (evidence, not trust)
    for prep in (prep1, prep2):
        assert prep["staged_files"]["vehicle_day"] >= 1
        assert prep["staged_files"]["events/all"] >= 1


def test_p4b_identical_rerun_is_shrink_safe(tmp_path):
    """An identical re-run into the same staging dir must not duplicate rows:
    FILENAME_PATTERN's {i} count is thread/vector dependent, so a rerun that
    writes fewer part-files would otherwise leave the surplus behind."""
    stage = tmp_path / "stage"
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2019, 5, 1)))
    lake.add_test(TestRow(test_id=2, vehicle_id=1, test_date=date(2020, 5, 1)))
    inputs = lake.write()
    counts = []
    for run in ("a", "b"):
        cfg = make_config(tmp_path / run, staging_dir=str(stage))
        factory = emit.Factory(inputs, cfg)
        factory.connect()
        factory.preflight([2019, 2020], [RECIPE])
        factory.prepare([2019, 2020], [RECIPE])
        counts.append(len(list(factory.scan(RECIPE))))
    assert counts[0] == counts[1] == 2, f"row count changed across reruns: {counts}"


# --- P5: what the AMBIGUOUS column actually counts --------------------------

def test_p5_ambiguous_day_definition_matches_the_dictionary(tmp_path):
    """cycles._cluster_outcome returns AMBIGUOUS for a day of mixed
    non-definitive outcomes (ABANDONED + ABORTED) as well as for the
    same-stratum FAIL + definitive-pass case. Owner ruling 2026-08-12: the
    cycles-faithful superset is `b5_n_prior_nondefinitive_days`; the strict
    same-stratum case keeps the name `b5_n_prior_ambiguous_days`."""
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=5, test_date=date(2019, 4, 1),
                          outcome="ABANDONED"))
    lake.add_test(TestRow(test_id=2, vehicle_id=5, test_date=date(2019, 4, 1),
                          test_type="RT", outcome="ABORTED"))
    lake.add_test(TestRow(test_id=3, vehicle_id=5, test_date=date(2020, 4, 1),
                          outcome="PASS"))
    _, rows, _ = run_factory(lake, tmp_path / "run", [RECIPE])
    target = [r for r in rows if r["tgt_id"] == 3][0]
    assert target["b5_n_prior_ambiguous_days"] == 0, (
        f"a day with NO definitive outcome and mixed non-results counts as "
        f"strictly AMBIGUOUS ({target['b5_n_prior_ambiguous_days']})")
    assert target["b5_n_prior_nondefinitive_days"] == 1, (
        "the cycles-faithful superset must still count the mixed non-result day")
    assert target["b1_n_prior_nonresult_days"] == 1


# --- P5: full-pop staging pushdown (restrict_max_u) -------------------------

def test_p5_restrict_max_u_stages_only_sampled_vehicles(tmp_path):
    """restrict_max_u must drop non-sampled vehicles from BOTH atom staging and
    events, keep whole histories for sampled ones, and refuse multi-recipe."""
    import duckdb as _duck
    import pytest as _pytest
    from factory.sampling import unit_hash_sql

    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2019, 5, 1)))
    lake.add_test(TestRow(test_id=2, vehicle_id=2, test_date=date(2019, 6, 1)))
    inputs = lake.write()

    con = _duck.connect()
    u = {v: con.execute(
        f"SELECT {unit_hash_sql(str(v), RECIPE.salt)}").fetchone()[0]
        for v in (1, 2)}
    lo = min(u, key=u.get)
    cut = (u[1] + u[2]) / 2  # strictly between the two vehicles' u values

    cfg = make_config(tmp_path / "c", staging_dir=str(tmp_path / "stage"))
    f = emit.Factory(inputs, cfg)
    f.connect()
    f.preflight([2019], [RECIPE])
    f.prepare([2019], [RECIPE], restrict_max_u=cut)

    vehicles = {row["vehicle_id"] for row, _pk in f.scan(RECIPE)}
    assert vehicles == {lo}, (
        f"expected only vehicle {lo} (u={u[lo]:.4f} < cut={cut:.4f}), got "
        f"{sorted(vehicles)}")

    with _pytest.raises(ValueError, match="one recipe"):
        f.prepare([2019], [RECIPE, RECIPE], restrict_max_u=cut)
