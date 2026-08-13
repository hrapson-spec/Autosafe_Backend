"""R4 independent red-team fixtures -- executable counterexamples against the
COMMITTED factory code (no cube spec was read; these derive from the data + the
contract only).

Run:  python3 out/cube/cube_redteam_fixtures.py
Pure Python + duckdb over tmp fixtures. No lake scans, no network, <1GB.

Each X* function returns (name, verdict, evidence). Verdict is one of
SUPPORTED / FALSIFIED / UNPROVEN -- for the CLAIM stated in its docstring.
"""
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_PARENT = os.path.dirname(os.path.dirname(HERE))          # .../model_programme_2026_08
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(PKG_PARENT)))  # .../autosafe-v58
for p in (PKG_PARENT, REPO_ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from factory import blocks, emit, sampling                      # noqa: E402
from factory.fixtures import FixtureLake, write_p4_certification  # noqa: E402
from factory.fixtures.generate import ItemRow, TestRow           # noqa: E402

RECIPE = emit.WindowRecipe("all", date(2005, 1, 1), date(2024, 1, 1))


def run(lake, root, recipes=(RECIPE,), years=None, **cfg):
    inputs = lake.write()
    cert = write_p4_certification(os.path.join(root, "cert", "p4.json"))
    config = emit.BuildConfig(staging_dir=os.path.join(root, "stage"),
                              output_dir=os.path.join(root, "out"),
                              memory_limit="1GB",
                              temp_directory=os.path.join(root, "duck_tmp"),
                              max_temp_directory_size="512MiB",
                              n_buckets=4, write_batch_rows=1000,
                              p4_certification_path=cert, **cfg)
    factory = emit.Factory(inputs, config)
    factory.connect()
    years = years or sorted({t.test_date.year for t in lake.tests})
    factory.preflight(years, list(recipes))
    factory.prepare(years, list(recipes))
    rows, pkts = [], []
    for recipe in recipes:
        for row, prow in factory.scan(recipe):
            rows.append(row)
            pkts.extend(prow)
    return rows, pkts


# ---------------------------------------------------------------------------
# X1  COVERAGE-ERA MASQUERADE
# ---------------------------------------------------------------------------

def x1_era_masquerade(root):
    """CLAIM: two vehicles with PHYSICALLY IDENTICAL histories (same gaps, same
    defect counts, same categories, same outcomes) but different CALENDAR
    placement emit different feature vectors, and the difference is a
    deterministic era signature -- i.e. the frame already carries a
    'which-publication-era-is-this-row' basis before any cube is added.
    """
    lake = FixtureLake(os.path.join(root, "lake"))
    tid = 0

    def history(vehicle, anchor, fud):
        """4 annual test-days: FAIL(brakes adv+fail), PASS, FAIL(brakes), target."""
        nonlocal tid
        for k in range(3):
            tid += 1
            day = anchor + timedelta(days=365 * k)
            outcome = "FAIL" if k != 1 else "PASS"
            lake.add_test(TestRow(test_id=tid, vehicle_id=vehicle, test_date=day,
                                  outcome=outcome, test_mileage=40_000 + 10_000 * k,
                                  first_use_date=fud))
            rfr_a, rfr_f = ("5003", "5001") if day < date(2018, 5, 20) else ("20003", "20001")
            lake.add_item(ItemRow(test_id=tid, rfr_id=rfr_a, rfr_type_code="A"))
            if outcome == "FAIL":
                lake.add_item(ItemRow(test_id=tid, rfr_id=rfr_f, rfr_type_code="F"))
        tid += 1
        lake.add_test(TestRow(test_id=tid, vehicle_id=vehicle,
                              test_date=anchor + timedelta(days=365 * 3),
                              outcome="FAIL", test_mileage=70_000, first_use_date=fud))
        return tid

    old_tgt = history(1, date(2012, 6, 1), date(2009, 6, 1))
    new_tgt = history(2, date(2019, 6, 1), date(2016, 6, 1))
    rows, _ = run(lake, root)
    by_id = {r["tgt_id"]: r for r in rows}
    old, new = by_id[old_tgt], by_id[new_tgt]

    feat = [c.name for c in blocks.ALL_COLUMNS if c.block != "meta"]
    differing = [c for c in feat if old[c] != new[c]]
    # The columns that should be identical if the frame were era-blind:
    physical = ["b1_n_prior_test_days", "b1_n_prior_tests", "b1_history_years",
                "b2_n_items_total", "b2_breadth_categories", "b5_days_since_prior_day",
                "b4_burden_mean_last3"]
    physical_same = all(old[c] == new[c] for c in physical)
    return ("X1 coverage-era masquerade",
            "SUPPORTED" if differing and physical_same else "FALSIFIED",
            {"n_feature_cols": len(feat),
             "n_differing": len(differing),
             "physically_identical_cols_agree": physical_same,
             "era_signature_cols": sorted(differing)[:40],
             "old_b3_status": old["b3_severity_observability_status"],
             "new_b3_status": new["b3_severity_observability_status"],
             "old_b3_obs_days": old["b3_n_days_fine_severity_observable"],
             "new_b3_obs_days": new["b3_n_days_fine_severity_observable"],
             "old_first_prior_date": str(old["b1_first_prior_date"]),
             "new_first_prior_date": str(new["b1_first_prior_date"])})


# ---------------------------------------------------------------------------
# X2  RETEST SAME-DAY BURDEN DOUBLE-COUNT
# ---------------------------------------------------------------------------

def x2_retest_double_count(root):
    """CLAIM: a same-day retest that re-lists the SAME physical defects doubles
    every burden/volume feature, while depth features are unchanged. Same
    vehicle condition, different recording practice -> different features.
    Same-day multi-test share is era-varying (7.66% 2016 -> 7.76% 2023,
    measured; 9.34%->7.76% full-year per DATA_ASSESSMENT S3), so the inflation
    factor drifts with calendar time.
    """
    lake = FixtureLake(os.path.join(root, "lake"))
    # vehicle 1: NT FAIL with 3 brake items only
    lake.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2021, 6, 1),
                          outcome="FAIL", test_mileage=40_000))
    for _ in range(3):
        lake.add_item(ItemRow(test_id=1, rfr_id="20001", rfr_type_code="F"))
    lake.add_test(TestRow(test_id=2, vehicle_id=1, test_date=date(2022, 6, 1),
                          outcome="PASS", test_mileage=50_000))
    # vehicle 2: IDENTICAL physical event, but a same-day RT re-lists the 3 items
    lake.add_test(TestRow(test_id=11, vehicle_id=2, test_date=date(2021, 6, 1),
                          outcome="FAIL", test_mileage=40_000))
    for _ in range(3):
        lake.add_item(ItemRow(test_id=11, rfr_id="20001", rfr_type_code="F"))
    lake.add_test(TestRow(test_id=12, vehicle_id=2, test_date=date(2021, 6, 1),
                          test_type="RT", outcome="PASS", test_mileage=40_000))
    for _ in range(3):
        lake.add_item(ItemRow(test_id=12, rfr_id="20001", rfr_type_code="F"))
    lake.add_test(TestRow(test_id=13, vehicle_id=2, test_date=date(2022, 6, 1),
                          outcome="PASS", test_mileage=50_000))

    rows, _ = run(lake, root)
    by_id = {r["tgt_id"]: r for r in rows}
    a, b = by_id[2], by_id[13]
    inflated = {c: (a[c], b[c]) for c in
                ("b2_n_items_total", "b3_n_fail_items_final", "b4_burden_mean_last3",
                 "b2_brakes_n_days", "b1_n_prior_test_days", "b1_n_prior_tests",
                 "b5_n_prior_multi_test_days", "b4_burden_delta_1")
                }
    doubled = (b["b2_n_items_total"] == 2 * a["b2_n_items_total"]
               and b["b4_burden_mean_last3"] == 2 * a["b4_burden_mean_last3"]
               and b["b1_n_prior_test_days"] == a["b1_n_prior_test_days"])
    return ("X2 same-day retest burden double-count",
            "SUPPORTED" if doubled else "FALSIFIED", inflated)


# ---------------------------------------------------------------------------
# X3  RETEST SEMANTICS: SAME-DAY REPAIR IS INVISIBLE TO THE REPAIR LADDER
# ---------------------------------------------------------------------------

def x3_same_day_repair_invisible(root):
    """CLAIM: state.update's `elif` (state.py:438-441) means a category that
    FAILS and is rectified the SAME DAY never reaches repair_state=2, so a later
    failure is NOT counted as recurrence-after-repair -- while the identical
    physical sequence with a NEXT-DAY retest IS. b4_n_recurrence_after_repair
    therefore has a recording-practice-dependent, era-varying definition.
    """
    lake = FixtureLake(os.path.join(root, "lake"))
    # v1: FAIL brakes + same-day RT PASS ; 1y later FAIL brakes ; target
    lake.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2020, 6, 1),
                          outcome="FAIL"))
    lake.add_item(ItemRow(test_id=1, rfr_id="20001", rfr_type_code="F"))
    lake.add_test(TestRow(test_id=2, vehicle_id=1, test_date=date(2020, 6, 1),
                          test_type="RT", outcome="PASS"))
    lake.add_test(TestRow(test_id=3, vehicle_id=1, test_date=date(2021, 6, 1),
                          outcome="FAIL"))
    lake.add_item(ItemRow(test_id=3, rfr_id="20001", rfr_type_code="F"))
    lake.add_test(TestRow(test_id=4, vehicle_id=1, test_date=date(2022, 6, 1),
                          outcome="PASS"))
    # v2: identical, except the rectifying retest is the NEXT DAY
    lake.add_test(TestRow(test_id=11, vehicle_id=2, test_date=date(2020, 6, 1),
                          outcome="FAIL"))
    lake.add_item(ItemRow(test_id=11, rfr_id="20001", rfr_type_code="F"))
    lake.add_test(TestRow(test_id=12, vehicle_id=2, test_date=date(2020, 6, 2),
                          test_type="RT", outcome="PASS"))
    lake.add_test(TestRow(test_id=13, vehicle_id=2, test_date=date(2021, 6, 1),
                          outcome="FAIL"))
    lake.add_item(ItemRow(test_id=13, rfr_id="20001", rfr_type_code="F"))
    lake.add_test(TestRow(test_id=14, vehicle_id=2, test_date=date(2022, 6, 1),
                          outcome="PASS"))

    rows, _ = run(lake, root)
    by_id = {r["tgt_id"]: r for r in rows}
    a, b = by_id[4], by_id[14]
    ev = {"same_day_repair.b4_n_recurrence_after_repair": a["b4_n_recurrence_after_repair"],
          "next_day_repair.b4_n_recurrence_after_repair": b["b4_n_recurrence_after_repair"],
          "same_day_repair.b1_n_prior_test_days": a["b1_n_prior_test_days"],
          "next_day_repair.b1_n_prior_test_days": b["b1_n_prior_test_days"],
          "same_day_repair.b4_recurrence_categories": a["b4_recurrence_categories"],
          "next_day_repair.b4_recurrence_categories": b["b4_recurrence_categories"]}
    return ("X3 same-day repair invisible to recurrence ladder",
            "SUPPORTED" if a["b4_n_recurrence_after_repair"]
            != b["b4_n_recurrence_after_repair"] else "FALSIFIED", ev)


# ---------------------------------------------------------------------------
# X4  ABSOLUTE CALENDAR DATES ARE FITTABLE FEATURES
# ---------------------------------------------------------------------------

def x4_absolute_dates_are_features(_root):
    """CLAIM: b1_first_prior_date / b1_last_prior_date are DATE columns inside
    block B1, and B1 is a fittable featureset -- so the absolute calendar
    coordinate of the row is handed to the learner, while tgt_date (the meta
    twin) is fenced out. Under a chronological train/eval split every eval date
    is outside the training range of these columns.
    """
    from factory.runners import fit_contract as fc
    cols = fc.resolve_featureset(["B1"])
    dates = [c.name for c in blocks.ALL_COLUMNS
             if c.dtype == "DATE" and c.block != "meta"]
    meta_fenced = True
    try:
        fc.resolve_featureset(["meta"])
        meta_fenced = False
    except ValueError:
        pass
    return ("X4 absolute calendar dates inside a fittable block",
            "SUPPORTED" if (set(dates) & set(cols)) and meta_fenced else "FALSIFIED",
            {"date_feature_columns": dates,
             "in_B1_featureset": sorted(set(dates) & set(cols)),
             "meta_block_is_fenced": meta_fenced,
             "tgt_date_in_features": "tgt_date" in cols})


# ---------------------------------------------------------------------------
# X5  CONFIG-DEPENDENT SILENT EVENT LOSS
# ---------------------------------------------------------------------------

def x5_silent_event_loss(root):
    """CLAIM: emit.Factory._scan_sql (emit.py:461-463) drives the scan from the
    vehicle_day relation and LEFT JOINs events onto it. If history_classes is
    ever set NARROWER than target_classes, events whose day produced no staged
    day-atom vanish with no error and no manifest discrepancy.
    """
    lake = FixtureLake(os.path.join(root, "lake"))
    # class-3 vehicle (in target_classes 3&4) with a class-3 history
    lake.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2020, 6, 1),
                          test_class_id="3", outcome="PASS"))
    lake.add_test(TestRow(test_id=2, vehicle_id=1, test_date=date(2021, 6, 1),
                          test_class_id="3", outcome="FAIL"))
    # class-4 control
    lake.add_test(TestRow(test_id=3, vehicle_id=2, test_date=date(2020, 6, 1),
                          test_class_id="4", outcome="PASS"))
    lake.add_test(TestRow(test_id=4, vehicle_id=2, test_date=date(2021, 6, 1),
                          test_class_id="4", outcome="FAIL"))

    unfiltered, _ = run(lake, os.path.join(root, "a"))
    narrow, _ = run(lake, os.path.join(root, "b"), history_classes=("4",))
    return ("X5 silent event loss when history_classes < target_classes",
            "SUPPORTED" if len(narrow) < len(unfiltered) else "FALSIFIED",
            {"rows_history_unfiltered": len(unfiltered),
             "rows_history_class4_only": len(narrow),
             "lost_tgt_ids": sorted(set(r["tgt_id"] for r in unfiltered)
                                    - set(r["tgt_id"] for r in narrow)),
             "raised_or_warned": False})


# ---------------------------------------------------------------------------
# X6  BOUNDARY / EQUALITY on the as-of fence and the trailing caps
# ---------------------------------------------------------------------------

def x6_boundary_equality(root):
    """CLAIM (defensive): the strictly-earlier-day rule holds at equality, and
    the trailing depth caps are half-open at the far edge. A prior on the target
    DAY must contribute nothing; a prior exactly cap_days(2.0) before the target
    must be OUTSIDE cap2y.
    """
    from factory import state as st
    cap2 = st.cap_days(2.0)
    lake = FixtureLake(os.path.join(root, "lake"))
    tgt = date(2022, 6, 1)
    # prior exactly on the boundary of the 2y window, and one day inside it
    lake.add_test(TestRow(test_id=1, vehicle_id=1, test_date=tgt - timedelta(days=cap2),
                          outcome="FAIL"))
    lake.add_item(ItemRow(test_id=1, rfr_id="20001", rfr_type_code="F"))
    lake.add_test(TestRow(test_id=2, vehicle_id=1,
                          test_date=tgt - timedelta(days=cap2 - 1), outcome="FAIL"))
    lake.add_item(ItemRow(test_id=2, rfr_id="20001", rfr_type_code="F"))
    # a same-DAY sibling initial test on the target day (both are events)
    lake.add_test(TestRow(test_id=3, vehicle_id=1, test_date=tgt, outcome="FAIL"))
    lake.add_item(ItemRow(test_id=3, rfr_id="20001", rfr_type_code="F"))
    lake.add_test(TestRow(test_id=4, vehicle_id=1, test_date=tgt, outcome="PASS"))

    rows, _ = run(lake, root)
    by_id = {r["tgt_id"]: r for r in rows}
    t3, t4 = by_id[3], by_id[4]
    ok = (t3["b1_n_prior_test_days"] == 2 and t3["b1_n_prior_test_days_cap2y"] == 1
          and t3["b2_n_items_total"] == 2
          and t3["b1_n_prior_test_days"] == t4["b1_n_prior_test_days"])
    same_day_siblings_identical = all(
        t3[c.name] == t4[c.name] for c in blocks.ALL_COLUMNS if c.block != "meta")
    return ("X6 as-of + cap boundary equality",
            "SUPPORTED" if ok else "FALSIFIED",
            {"cap_days_2y": cap2,
             "n_prior_test_days": t3["b1_n_prior_test_days"],
             "n_prior_test_days_cap2y": t3["b1_n_prior_test_days_cap2y"],
             "b2_n_items_total (target-day items excluded?)": t3["b2_n_items_total"],
             "same_day_sibling_feature_vectors_identical": same_day_siblings_identical,
             "sibling_labels": (t3["y_final"], t4["y_final"]),
             "NOTE": "identical X, opposite y on the same day = irreducible "
                     "label noise, NOT leakage; it caps achievable AUC"})


# ---------------------------------------------------------------------------
# X7  ENRICHMENT STRATUM IS STRUCTURALLY UNREACHABLE PRE-2018
# ---------------------------------------------------------------------------

def x7_enrichment_era_degenerate(root):
    """CLAIM: 'dangerous_prior' requires n_prior_dangerous_days>0, and the
    factory grades severity only post-2018-05-20 -- so no target whose history
    is entirely pre-2018 can EVER be enrichment-eligible on that stratum, even
    though dangerous_mark='D' IS populated pre-2018 in the real lake (measured
    1.05-2.23% of items 2005-2018). The sampling design is therefore era-tilted
    by a rule, not by the data.
    """
    lake = FixtureLake(os.path.join(root, "lake"))
    # pre-2018 vehicle with D-marked items
    lake.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2015, 6, 1),
                          outcome="FAIL"))
    lake.add_item(ItemRow(test_id=1, rfr_id="5001", rfr_type_code="F",
                          dangerous_mark="D"))
    lake.add_test(TestRow(test_id=2, vehicle_id=1, test_date=date(2016, 6, 1),
                          outcome="FAIL"))
    # post-2018 vehicle, same physical pattern
    lake.add_test(TestRow(test_id=11, vehicle_id=2, test_date=date(2020, 6, 1),
                          outcome="FAIL"))
    lake.add_item(ItemRow(test_id=11, rfr_id="20001", rfr_type_code="F",
                          dangerous_mark="D"))
    lake.add_test(TestRow(test_id=12, vehicle_id=2, test_date=date(2021, 6, 1),
                          outcome="FAIL"))

    rows, _ = run(lake, root)
    by_id = {r["tgt_id"]: r for r in rows}
    pre, post = by_id[2], by_id[12]
    return ("X7 enrichment stratum era-degenerate",
            "SUPPORTED" if pre["enrichment_stratum"] != post["enrichment_stratum"]
            else "FALSIFIED",
            {"pre2018.enrichment_stratum": pre["enrichment_stratum"],
             "post2018.enrichment_stratum": post["enrichment_stratum"],
             "pre2018.b3_n_dangerous_days": pre["b3_n_dangerous_days"],
             "post2018.b3_n_dangerous_days": post["b3_n_dangerous_days"],
             "pre2018.b3_severity_observability_status":
                 pre["b3_severity_observability_status"]})


PROBES = [x1_era_masquerade, x2_retest_double_count, x3_same_day_repair_invisible,
          x4_absolute_dates_are_features, x5_silent_event_loss,
          x6_boundary_equality, x7_enrichment_era_degenerate]


def main():
    root = tempfile.mkdtemp(prefix="r4_cube_redteam_")
    results = []
    try:
        for probe in PROBES:
            sub = os.path.join(root, probe.__name__)
            os.makedirs(sub, exist_ok=True)
            try:
                results.append(probe(sub))
            except Exception as exc:                      # noqa: BLE001
                results.append((probe.__name__, "ERROR", {"exception": repr(exc)}))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    for name, verdict, ev in results:
        print(f"\n=== {name}  ->  {verdict}")
        for k, v in ev.items():
            print(f"    {k}: {v}")
    return results


if __name__ == "__main__":
    main()
