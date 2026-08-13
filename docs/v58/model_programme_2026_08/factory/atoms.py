"""The three atoms (contract §Atoms), as explicit SQL over injected relations.

1. item_test_atom  -- grain test_id
2. vehicle_day_atom -- grain (vehicle_id, test_date), an UNORDERED multiset
3. as_of_state      -- state.py (Python; the only stateful layer)

Everything here is set-based. There is no ORDER BY, min() or max() on test_id
anywhere in this module except the single documented representation tiebreak in
`packet_struct_sql` (marked D13-ALLOW and registered in the falsifier-9
allowlist): it orders the packet payload by CONTENT first, so it can only ever
break ties between rows that are byte-identical apart from their id.
"""
from typing import List, Optional, Sequence

from pipeline.lake.target_population import initial_test_sql

from . import observability as obs
from . import severity as sev
from . import sources
from .taxonomy import (CATEGORY_KEYS, LATERAL_GROUPS, LONGITUDINAL_GROUPS,
                       VERTICAL_GROUPS)

#: Outcomes DVSA counts as a completed test with a result.
DEFINITIVE_OUTCOMES = ("PASS", "FAIL", "PRS")

#: A mileage reading is "valid" when it is present and strictly positive; zero
#: readings are the documented unreadable/aborted encoding (DATA_ASSESSMENT §8).
VALID_MILEAGE_SQL = "(i_or_r.test_mileage IS NOT NULL AND i_or_r.test_mileage > 0)"

#: Two readings on the same day conflict when they differ by more than 1%.
MILEAGE_CONFLICT_TOLERANCE = 0.01


def _era(alias: str = "r") -> str:
    return sev.era_expr(f"{alias}.test_date")


def item_test_atom_sql(inputs: sources.Inputs, years: Sequence[int],
                       history_classes: Optional[Sequence[str]],
                       has_location_map: bool,
                       fail_basis_note: str = "",
                       restrict_year: Optional[int] = None,
                       restrict_pred: Optional[str] = None) -> str:
    """Per-test defect aggregate + (optionally) the raw defect rows.

    `history_classes` is normally None: HISTORY IS UNFILTERED BY TEST CLASS
    (owner ruling 2026-08-12). A class-3/5/7 prior test is still this vehicle's
    history. Its items keep their rfr_id; the class-4 catalogue resolves what it
    can and the rest is counted as a catalogue miss -- rows are never dropped.

    Severity is derived HERE from rfr_type_code + dangerous_mark + the parent
    test's era. The lake's stored is_fail_item/is_advisory/is_dangerous/
    rfr_class columns are never read (F-22).
    """
    era = _era("r")
    disp = sev.disposition_expr("i.rfr_type_code", era)
    sever = sev.severity_expr("i.rfr_type_code", "i.dangerous_mark", era)
    fail_init = sev.fail_bearing_expr("i.rfr_type_code", era, sev.BASIS_INITIAL)
    fail_final = sev.fail_bearing_expr("i.rfr_type_code", era, sev.BASIS_FINAL)
    post = f"({era} = '{sev.POST_2018}')"

    cat_cols: List[str] = []
    for key in CATEGORY_KEYS:
        cat_cols += [
            f"count(*) FILTER (WHERE m.category_key = '{key}') AS cat_{key}_n",
            f"count(*) FILTER (WHERE m.category_key = '{key}' AND {disp} = "
            f"'{sev.ADVISORY}') AS cat_{key}_adv",
            f"count(*) FILTER (WHERE m.category_key = '{key}' AND {fail_init}) "
            f"AS cat_{key}_fail_initial",
            f"count(*) FILTER (WHERE m.category_key = '{key}' AND {fail_final}) "
            f"AS cat_{key}_fail_final",
        ]

    pos_cols: List[str] = []
    axes = [("lat", "lat_group", LATERAL_GROUPS),
            ("long", "long_group", LONGITUDINAL_GROUPS),
            ("vert", "vert_group", VERTICAL_GROUPS)]
    for prefix, column, groups in axes:
        for group in groups:
            if has_location_map:
                pos_cols.append(
                    f"count(*) FILTER (WHERE coalesce(g.{column}, 'unknown') = "
                    f"'{group}') AS pos_{prefix}_{group}_n")
            else:
                # absent map: NULL, never zero -- a zero would assert "no defect
                # in this position", which is not what an unavailable lookup means.
                pos_cols.append(f"CAST(NULL AS BIGINT) AS pos_{prefix}_{group}_n")
    pos_cols.append(
        "count(*) FILTER (WHERE g.location_id IS NOT NULL) AS pos_resolved_n"
        if has_location_map else "CAST(NULL AS BIGINT) AS pos_resolved_n")

    defect_rows = (
        "list(struct_pack("
        f"rfr := i.rfr_id, code := i.rfr_type_code, disp := {disp}, sev := {sever}, "
        "sect := m.section, cat := m.category_key, comp := m.serving_component, "
        f"dang := (trim(coalesce(i.dangerous_mark, '')) = '{sev.DANGEROUS_MARK}'), "
        "loc := i.location_id, pos := "
        + ("(g.lat_group || '/' || g.long_group || '/' || g.vert_group)"
           if has_location_map
           else "CAST(NULL AS VARCHAR)")
        + ")) AS defect_rows"
    )

    location_join = (
        "LEFT JOIN factory_location_group g ON g.location_id = trim(i.location_id)"
        if has_location_map else ""
    )
    return f"""
-- item_test_atom{(' -- ' + fail_basis_note) if fail_basis_note else ''}
SELECT
    i.test_id,
    count(*)                                                   AS n_items,
    count(*) FILTER (WHERE m.rfr_id IS NULL)                   AS n_catalogue_miss,
    count(*) FILTER (WHERE {disp} = '{sev.ADVISORY}')          AS n_advisory_items,
    count(*) FILTER (WHERE {fail_init})                        AS n_fail_items_initial,
    count(*) FILTER (WHERE {fail_final})                       AS n_fail_items_final,
    count(*) FILTER (WHERE {disp} = '{sev.FAIL_RECTIFIED}')    AS n_prs_items,
    count(*) FILTER (WHERE {post} AND {sever} = '{sev.SEVERITY_DANGEROUS}') AS n_dangerous,
    count(*) FILTER (WHERE {post} AND {sever} = '{sev.SEVERITY_MAJOR}')     AS n_major,
    count(*) FILTER (WHERE {post} AND {sever} = '{sev.SEVERITY_MINOR}')     AS n_minor,
    count(*) FILTER (WHERE {sev.unknown_disposition_expr('i.rfr_type_code', era)})
                                                               AS n_unknown_disposition,
    {', '.join(cat_cols)},
    {', '.join(pos_cols)},
    {defect_rows}
FROM {inputs.items_rel} i
JOIN {inputs.results_rel} r ON r.test_id = i.test_id
LEFT JOIN factory_rfr_category m ON m.rfr_id = trim(i.rfr_id)
{location_join}
WHERE {sources.year_filter_sql('r', years)}
  AND {f"year(r.test_date) = {int(restrict_year)} AND " if restrict_year is not None else ""}{f"({restrict_pred}) AND " if restrict_pred else ""}{sources.test_class_filter_sql('r', history_classes)}
GROUP BY i.test_id
"""


#: The scalar item_test_atom aggregates rolled up per day.
SCALAR_ITEM_COLUMNS: Sequence[str] = (
    "n_items", "n_catalogue_miss", "n_advisory_items", "n_fail_items_initial",
    "n_fail_items_final", "n_prs_items", "n_dangerous", "n_major", "n_minor",
    "n_unknown_disposition")


def _sum_item_columns(alias: str, observed: str) -> List[str]:
    """Day-level union of the item_test_atom aggregates, OVER OBSERVED TESTS ONLY.

    MECHANISM M2 CLOSED (was `coalesce(sum(x), 0)`, atoms.py:140/144). A test
    whose defect detail is not observable contributed a confident 0 to every
    aggregate; it now contributes nothing at all, and a day on which NO test is
    item-observable emits NULL rather than 0.

    `observed` is a REQUIRED argument with no default: a caller that forgets it
    fails at call time rather than silently restoring the coalesce.

    The damage-ratio guard (PREREG §4, 325:1) lives in the inner `coalesce`:
    a day whose tests ARE observable and simply carry no items still emits 0,
    because `sum()` over observed-but-item-less rows is NULL and that NULL is a
    genuine zero. Only the OUTER CASE -- driven by observability, never by the
    join -- can produce NULL.
    """
    n_observed = f"count(*) FILTER (WHERE {observed})"
    cols: List[str] = []
    for name in list(SCALAR_ITEM_COLUMNS) + [
            f"cat_{key}_{suffix}" for key in CATEGORY_KEYS
            for suffix in ("n", "adv", "fail_initial", "fail_final")]:
        cols.append(
            f"CASE WHEN {n_observed} > 0 THEN "
            f"coalesce(sum({alias}.{name}) FILTER (WHERE {observed}), 0) "
            f"ELSE CAST(NULL AS BIGINT) END AS {name}")
    pos_names = ([f"pos_lat_{g}_n" for g in LATERAL_GROUPS]
                 + [f"pos_long_{g}_n" for g in LONGITUDINAL_GROUPS]
                 + [f"pos_vert_{g}_n" for g in VERTICAL_GROUPS] + ["pos_resolved_n"])
    for name in pos_names:
        # Already NULL-preserving before this repair (atoms.py:150, the B6
        # precedent this fix generalises): sum() of all-NULL stays NULL.
        cols.append(f"sum({alias}.{name}) FILTER (WHERE {observed}) AS {name}")
    return cols


def _day_observability_columns(alias: str, observed: str) -> List[str]:
    """Per-day item-observability counts (ITEM_OBSERVABILITY_DESIGN §3.2).

    Emitted so every frame can reconstruct the denominator it was scored on,
    and so an item aggregate of NULL is always accompanied by its reason.
    """
    state = f"{alias}.items_obs"
    zero = ", ".join(f"'{s}'" for s in (obs.PRESENT_ZERO_DEFECTS,
                                        obs.ASSUMED_ZERO_DEFECTS))
    return [
        f"count(*) FILTER (WHERE {observed}) AS n_tests_items_observed",
        f"count(*) FILTER (WHERE NOT {observed}) AS n_tests_items_unobserved",
        f"count(*) FILTER (WHERE {state} = '{obs.PRESENT_WITH_DEFECTS}') "
        f"AS n_tests_items_with_defects",
        f"count(*) FILTER (WHERE {state} IN ({zero})) AS n_tests_items_zero_defects",
        f"count(*) FILTER (WHERE {state} = '{obs.UNAVAILABLE}') "
        f"AS n_tests_items_unavailable",
        f"count(*) FILTER (WHERE {state} = '{obs.EXPECTED_MISSING}') "
        f"AS n_tests_items_expected_missing",
    ]


def packet_struct_sql(defect_detail: str, alias: str = "r") -> str:
    """Per-test packet payload, shaped for the packets emitter.

    Field order IS the sort order (`list_sort` below): content first, id last.
    -- D13-ALLOW: test_id is the LAST struct field, so it can only break ties
    -- between rows identical in test_type, outcome, mileage and defect payload.
    -- This is representation determinism (cycles.py CHRONOLOGY_ORDER_SQL
    -- precedent), never chronology: no emitted feature reads packet order.

    MECHANISM M4 CLOSED (was `n_items := coalesce(a.n_items, 0)` and the two
    `counts`-mode coalesces, atoms.py:167/168/174): those carried the
    item-unavailable/no-defect conflation straight into the packets view and
    thence into all 104 serving features. Each count is now NULL unless the
    test's defect detail is observable.

    `items_obs` travels WITH the payload so the packets emitter can render the
    three states without re-deriving anything: populated, `[]` + observed
    status, or NULL + explicit status.
    """
    observed = obs.observed_predicate_sql(f"{alias}.items_obs")

    def graded(expr: str) -> str:
        return f"CASE WHEN {observed} THEN coalesce({expr}, 0) ELSE NULL END"

    if defect_detail == "rows":
        # NULL here means EITHER "observed, no items" OR "not observable"; the
        # packets emitter resolves it from items_obs and never guesses.
        defects = f"defects := {alias}.defect_rows"
    elif defect_detail == "counts":
        defects = ("defects := CAST(NULL AS STRUCT(rfr VARCHAR)[]), "
                   f"n_adv := {graded(f'{alias}.n_advisory_items')}, "
                   f"n_fail_final := {graded(f'{alias}.n_fail_items_final')}")
    elif defect_detail == "none":
        defects = "defects := CAST(NULL AS STRUCT(rfr VARCHAR)[])"
    else:
        raise ValueError(f"unknown defect_detail {defect_detail!r}")
    return (f"struct_pack(ttype := {alias}.test_type, outcome := {alias}.outcome, "
            f"miles := {alias}.test_mileage, "
            f"items_obs := {alias}.items_obs, "
            f"n_items := {graded(f'{alias}.n_items')}, "
            f"{defects}, test_id := {alias}.test_id)")


def vehicle_day_atom_sql(inputs: sources.Inputs, years: Sequence[int],
                         history_classes: Optional[Sequence[str]],
                         has_location_map: bool, n_buckets: int,
                         defect_detail: str = "rows",
                         restrict_year: Optional[int] = None,
                         restrict_pred: Optional[str] = None,
                         ledger: Optional[obs.CoverageLedger] = None) -> str:
    """Grain (vehicle_id, test_date). Every column is a set aggregate.

    HISTORY IS UNFILTERED BY TEST CLASS unless `history_classes` is given: the
    target population is classes 3&4 (events_sql), but a vehicle's prior tests
    in any class are still its history.

    `tests` carries the day's per-test packet payload, canonically sorted by
    content (see packet_struct_sql). `n_initial_day` uses the imported DVSA
    rule (`target_population.initial_test_sql`), never a re-implementation.

    MECHANISM M1 CLOSED (was a bare `LEFT JOIN ita a` whose unmatched NULLs were
    immediately coalesced, atoms.py:240). The join still runs -- it is how we
    learn a test HAS items -- but it is no longer allowed to answer the
    availability question on its own. `items_obs` is resolved in the `res`
    stage below from the coverage ledger (cell grain, never consults the join)
    and the fail-bearing definitional impossibility (row grain, evidence), and
    every downstream aggregate is filtered on it.
    """
    from .sampling import bucket_sql

    ledger = ledger if ledger is not None else obs.CoverageLedger()
    # restrict_year must reach the ita CTE too: the LEFT JOIN to one year of
    # results discards other years' CTE rows anyway (output-identical), but an
    # UNrestricted CTE aggregates ALL staged years of items — a multi-GB hash
    # state with list-of-struct payloads — on every per-year pass. That was the
    # first real build's OOM (3 strikes at 3/3.5/4GB, 2026-08-12 night).
    item_atom = item_test_atom_sql(inputs, years, history_classes,
                                   has_location_map, restrict_year=restrict_year,
                                   restrict_pred=restrict_pred)
    year_pred = sources.year_filter_sql("r", years)
    if restrict_year is not None:
        year_pred = f"({year_pred} AND year(r.test_date) = {int(restrict_year)})"
    if restrict_pred:
        # Vehicle-population pushdown (full-pop builds): stage ONLY the sampled
        # rung's vehicles. u is VEHICLE-level (unit_hash on vehicle_id), so the
        # predicate keeps whole histories intact for every staged vehicle.
        year_pred = f"({year_pred} AND ({restrict_pred}))"
    valid_mileage = "(r.test_mileage IS NOT NULL AND r.test_mileage > 0)"
    observed = obs.observed_predicate_sql("r.items_obs")
    # The item join and the observability resolution happen ONCE, here, in a
    # single pass; every aggregate below reads the resolved state.
    res = f"""
    SELECT r.*, a.* EXCLUDE (test_id),
           {obs.observability_case_sql(ledger, 'r', 'a')} AS items_obs
    FROM {inputs.results_rel} r
    LEFT JOIN ita a ON a.test_id = r.test_id
    WHERE {year_pred}
      AND {sources.test_class_filter_sql('r', history_classes)}
      AND r.test_date IS NOT NULL
      AND r.vehicle_id IS NOT NULL"""
    return f"""
WITH ita AS ({item_atom})
SELECT
    r.vehicle_id,
    r.test_date,
    {bucket_sql('r.vehicle_id', n_buckets)}                       AS bucket,
    year(r.test_date)                                             AS test_year,
    count(*)                                                      AS n_tests_day,
    count(*) FILTER (WHERE {initial_test_sql('r')})                AS n_initial_day,
    count(*) FILTER (WHERE r.outcome IN {DEFINITIVE_OUTCOMES})     AS n_definitive_day,
    count(*) FILTER (WHERE r.outcome NOT IN {DEFINITIVE_OUTCOMES}) AS n_nonresult_day,
    bool_or(r.outcome = 'PASS')                                    AS has_pass,
    bool_or(r.outcome = 'FAIL')                                    AS has_fail,
    bool_or(r.outcome = 'PRS')                                     AS has_prs,
    bool_or(r.outcome NOT IN {DEFINITIVE_OUTCOMES})                AS has_nonresult,
    count(DISTINCT r.outcome)                                      AS n_distinct_outcomes,
    max(r.test_mileage) FILTER (WHERE {valid_mileage})             AS max_valid_mileage_day,
    min(r.test_mileage) FILTER (WHERE {valid_mileage})             AS min_valid_mileage_day,
    count(*) FILTER (WHERE {valid_mileage})                        AS n_valid_mileage_day,
    (max(r.test_mileage) FILTER (WHERE {valid_mileage})
     > min(r.test_mileage) FILTER (WHERE {valid_mileage})
       * {1.0 + MILEAGE_CONFLICT_TOLERANCE})                       AS mileage_conflict,
    ({_era('r')} = '{sev.POST_2018}')                              AS severity_observable,
    max(r.first_use_date)                                          AS first_use_date,
    {', '.join(_day_observability_columns('r', observed))},
    {', '.join(_sum_item_columns('r', observed))},
    list_sort(list({packet_struct_sql(defect_detail, 'r')}))       AS tests
FROM ({res}
) r
GROUP BY r.vehicle_id, r.test_date, {_era('r')},
         {bucket_sql('r.vehicle_id', n_buckets)}, year(r.test_date)
"""


def dark_cell_items_probe_sql(inputs: sources.Inputs, ledger: obs.CoverageLedger,
                              years: Sequence[int],
                              history_classes: Optional[Sequence[str]]) -> Optional[str]:
    """Rows the ledger declares DARK that nevertheless carry item rows.

    A cell rule outranks the join by design, so a wrong rule would silently
    discard real defect data. This probe makes that impossible to do quietly:
    a non-zero count is a build refusal (gates.assert_coverage_ledger_consistent).
    """
    dark = ledger.dark_cell_predicate_sql("r")
    if not dark:
        return None
    return f"""
SELECT count(*) FROM {inputs.results_rel} r
WHERE {dark}
  AND {sources.year_filter_sql('r', years)}
  AND {sources.test_class_filter_sql('r', history_classes)}
  AND EXISTS (SELECT 1 FROM {inputs.items_rel} i WHERE i.test_id = r.test_id)
"""


def events_sql(inputs: sources.Inputs, window_start: str, window_end: str,
               target_classes: Optional[Sequence[str]], n_buckets: int,
               sample_salt: str, years: Optional[Sequence[int]] = None,
               restrict_pred: Optional[str] = None) -> str:
    """Prediction events: DVSA initial tests with a recorded result.

    `initial_test_sql` is imported from pipeline.lake.target_population -- the
    module is the single authority for population membership (D7).
    Fences are inclusive-exclusive.

    The explicit year list is applied HERE as well as to the window: an event in
    a year that was never staged would have no vehicle_day atom and would be
    silently dropped by the scan. Restricting it here makes the emitted
    population exactly the stated intersection, recorded in the manifest.
    """
    from .sampling import bucket_sql, unit_hash_sql

    return f"""
SELECT
    r.test_id                                       AS tgt_id,
    r.vehicle_id,
    r.test_date                                     AS tgt_date,
    {bucket_sql('r.vehicle_id', n_buckets)}         AS bucket,
    year(r.test_date)                               AS tgt_year,
    r.outcome                                       AS tgt_outcome,
    (r.outcome = 'FAIL')                            AS y_final,
    (r.outcome IN ('FAIL', 'PRS'))                  AS y_initial,
    r.test_class_id                                 AS tgt_test_class_id,
    r.test_type                                     AS tgt_test_type,
    r.test_mileage                                  AS tgt_miles,
    r.make                                          AS tgt_make,
    r.model                                         AS tgt_model,
    r.model_id                                      AS tgt_model_id,
    r.fuel_type                                     AS tgt_fuel,
    r.colour                                        AS tgt_colour,
    r.cylinder_capacity                             AS tgt_cc,
    r.first_use_date                                AS tgt_fud,
    r.postcode_area                                 AS tgt_pc,
    r.age_at_test                                   AS tgt_age_at_test,
    r.taxonomy_era                                  AS tgt_taxonomy_era,
    {unit_hash_sql('r.vehicle_id', sample_salt)}    AS u
FROM {inputs.results_rel} r
WHERE {initial_test_sql('r')}
  AND {sources.test_class_filter_sql('r', target_classes)}
  AND {sources.year_filter_sql('r', years) if years else 'TRUE'}
  AND r.test_date >= DATE '{window_start}'
  AND r.test_date <  DATE '{window_end}'
  AND {f"({restrict_pred})" if restrict_pred else "TRUE"}
"""


def unknown_vocabulary_count_sql(inputs: sources.Inputs, years: Sequence[int],
                                 history_classes: Optional[Sequence[str]]) -> str:
    """Fail-closed counter for test_type/outcome outside the DVSA vocabulary."""
    from pipeline.lake.target_population import unknown_vocabulary_sql

    return f"""
SELECT count(*) FROM {inputs.results_rel} r
WHERE {unknown_vocabulary_sql('r')}
  AND {sources.year_filter_sql('r', years)}
  AND {sources.test_class_filter_sql('r', history_classes)}
"""


def unknown_disposition_sample_sql(inputs: sources.Inputs, years: Sequence[int],
                                   history_classes: Optional[Sequence[str]],
                                   limit: int = 10) -> str:
    """The rfr_type_codes that would fail the disposition gate, with volumes."""
    era = _era("r")
    return f"""
SELECT i.rfr_type_code, {era} AS era, count(*) AS n
FROM {inputs.items_rel} i
JOIN {inputs.results_rel} r ON r.test_id = i.test_id
WHERE {sev.unknown_disposition_expr('i.rfr_type_code', era)}
  AND {sources.year_filter_sql('r', years)}
  AND {sources.test_class_filter_sql('r', history_classes)}
GROUP BY 1, 2
ORDER BY n DESC
LIMIT {int(limit)}
"""


def completed_ts_diagnostic_sql(inputs: sources.Inputs) -> Optional[str]:
    """OPTIONAL 2024-25 diagnostic: same-day tests with distinguishable times.

    Explicitly labelled a diagnostic. No factory feature may require it: pre-2024
    has no sidecar at all, so anything built on it would be era-degenerate.
    """
    if not inputs.completed_ts_rel:
        return None
    return f"""
SELECT c.test_id, c.completed_at
FROM {inputs.completed_ts_rel} c
"""
