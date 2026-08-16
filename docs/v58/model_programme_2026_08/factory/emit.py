"""Build orchestration: stage the atoms, scan them statefully, emit + manifest.

Two phases, both bucket-wise so memory is bounded by a CONSTRUCTOR ARG rather
than by the corpus:

    prepare()  one pass per input year over (results x items) -> vehicle_day
               atoms partitioned by bucket; one pass per recipe -> events
               partitioned by bucket. Every gate runs here, BEFORE any output.
    scan()     one bucket at a time: read that bucket's day atoms + events,
               ORDER BY (vehicle_id, test_date) -- never test_id -- and run the
               emit-before-update state machine.

Outputs: parquet per (recipe, rung) for the feature frame and for the packets
view, plus BUILD_MANIFEST.json.
"""
import glob
import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from . import (CONTRACT_VERSION, atoms, blocks, capability, gates,
               observability as obs, packets, sampling, serve_view, sources)
from . import state as st
from . import taxonomy

ARROW_TYPES = {
    "BIGINT": pa.int64(), "INTEGER": pa.int32(), "DOUBLE": pa.float64(),
    "BOOLEAN": pa.bool_(), "VARCHAR": pa.string(), "DATE": pa.date32(),
}

PACKET_ARROW_SCHEMA = pa.schema([
    ("tgt_id", pa.int64()), ("vehicle_id", pa.int64()), ("tgt_date", pa.date32()),
    ("tgt_make", pa.string()), ("tgt_model", pa.string()), ("tgt_fuel", pa.string()),
    ("tgt_cc", pa.int32()), ("tgt_fud", pa.date32()), ("tgt_pc", pa.string()),
    ("n_priors", pa.int64()), ("p_test_id", pa.int64()), ("p_date", pa.date32()),
    ("p_result", pa.string()), ("p_outcome", pa.string()), ("p_miles", pa.int64()),
    ("p_ttype", pa.string()), ("p_n_items", pa.int64()),
    ("p_items_observability", pa.string()), ("defects_json", pa.string()),
])


@dataclass(frozen=True)
class WindowRecipe:
    """A target-date window. Fences are INCLUSIVE-EXCLUSIVE."""

    name: str
    start: date
    end: date
    eval_slice: bool = False
    confirmation: bool = False
    salt: str = sampling.SAMPLE_SALT

    def as_manifest(self) -> dict:
        return {"name": self.name, "target_start_inclusive": self.start.isoformat(),
                "target_end_exclusive": self.end.isoformat(),
                "eval_slice": self.eval_slice, "confirmation": self.confirmation,
                "salt": self.salt}


#: The sealed 2025-H2 confirmation slice: DEFINITION ONLY (contract). The
#: factory never builds it unless --build-confirmation + a prereg sha.
CONFIRMATION_DEFINITION = {
    "name": "confirm_2025h2",
    "salt": sampling.CONFIRMATION_SALT,
    "predicate": "test_type = 'NT' AND outcome IN ('PASS','FAIL','PRS') "
                 "AND test_date >= DATE '2025-07-01' AND test_date < DATE '2026-01-01'",
    "selection": "u = hash(CAST(vehicle_id AS VARCHAR) || 'mp2026conf') / 2^64; "
                 "include iff u < threshold pinned in the pre-registration",
    "status": "DEFINITION EMITTED, NEVER BUILT BY THE DEFAULT PATH",
}

EVAL_SLICE_DEFINITIONS = {
    "eval_2024_selection": {"salt": sampling.EVAL_SALT, "target_rows": 4_000_000,
                            "window": ["2024-01-01", "2025-01-01"]},
    "eval_2025h1_drift": {"salt": sampling.EVAL_SALT, "target_rows": 4_000_000,
                          "window": ["2025-01-01", "2025-07-01"]},
}


@dataclass
class BuildConfig:
    """Owner-set knobs. Memory/temp are CONSTRUCTOR ARGS per the contract."""

    staging_dir: str
    output_dir: str
    memory_limit: str = "3GB"
    temp_directory: Optional[str] = None
    max_temp_directory_size: str = "1.5GiB"
    n_buckets: int = 64
    #: TARGET population classes (owner ruling 2026-08-12): the D7 rule is
    #: classes 3 & 4. Applied to EVENTS only.
    target_classes: Optional[Sequence[str]] = ("3", "4")
    #: HISTORY classes. None = UNFILTERED, the default and the ruling: a
    #: vehicle's class-3/5/7 prior rows are still its history. Items on those
    #: rows keep their rfr_id; the class-4 catalogue resolves what it can and
    #: the remainder is counted as a catalogue miss -- rows are never dropped.
    history_classes: Optional[Sequence[str]] = None
    #: Which fail basis drives the per-category B2/B4 ladders: 'final' (F only,
    #: the repaired serving vocabulary) or 'initial' (F+P, the corrected lake
    #: fail-bearing set). OPEN RULING [P4] -- recorded in the manifest.
    fail_basis: str = "final"
    #: Per-channel earliest date on which history COULD have been recorded.
    #: None => derive from the build's own input years (see `resolve_floors`).
    #: NEVER left at the absolute 2005 bound on a build that loads less: that
    #: is defect D-1, which inflated b1_observable_years for every vehicle
    #: first used before the earliest loaded year.
    history_floors: Optional[blocks.HistoryFloors] = None
    #: A REFERENCE manifest whose floors this build must reproduce. Set on every
    #: eval / drift / confirmation build; leave None for the training build that
    #: defines them. A mismatch RAISES before any feature is emitted, because a
    #: silently different denominator makes a paired contrast meaningless.
    inherit_floors_from: Optional[str] = None
    #: What the packets view carries in `defects_json`. THIS IS A CAPABILITY,
    #: not a formatting knob: `counts`/`none` mean a consumer that reads defect
    #: items cannot be served, and `consumers` below is how that is asserted.
    defect_detail: str = "rows"
    emit_packets: bool = True
    max_priors: Optional[int] = None
    pathology_bound: int = packets.DEFAULT_PATHOLOGY_BOUND
    write_batch_rows: int = 50_000
    p4_certification_path: Optional[str] = None
    serve_view_classes_path: Optional[str] = None
    #: Owner-produced cell ledger declaring which (date x schema_epoch x
    #: outcome_class) cells are dark. Absent -> items_coverage_mode is
    #: `assumed_covered`, which a consumer may refuse.
    item_coverage_csv: Optional[str] = None
    #: Registered consumers this build PROMISES to serve
    #: (capability.CONSUMER_REGISTRY keys). Asserted in preflight, before a
    #: single packet is written.
    consumers: Sequence[str] = ()
    threads: Optional[int] = None

    def as_manifest(self) -> dict:
        out = asdict(self)
        out["target_classes"] = list(self.target_classes) if self.target_classes else None
        out["history_classes"] = (list(self.history_classes) if self.history_classes
                                  else "UNFILTERED")
        out["consumers"] = list(self.consumers)
        return out

    def config_sha256(self) -> str:
        """Stable hash of the build configuration (packet-set provenance)."""
        payload = json.dumps(self.as_manifest(), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- small helpers ----------------------------------------------------------

def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def code_sha() -> str:
    """sha256 over the factory package sources (sorted) -- the code identity."""
    here = os.path.dirname(os.path.abspath(__file__))
    digest = hashlib.sha256()
    for name in sorted(os.listdir(here)):
        if not (name.endswith(".py") or name.endswith(".json")):
            continue
        digest.update(name.encode())
        digest.update(_sha256_file(os.path.join(here, name)).encode())
    return digest.hexdigest()


def _glob_summary(path_or_sql: str) -> dict:
    stripped = path_or_sql.strip()
    if stripped.lower().startswith(("read_", "select", "(", "with ")):
        return {"kind": "sql_relation", "expression": stripped[:400]}
    files = sorted(glob.glob(stripped, recursive=True))
    return {"kind": "path_glob", "pattern": stripped, "n_files": len(files),
            "bytes": sum(os.path.getsize(f) for f in files if os.path.isfile(f))}


def _arrow_schema(columns: Sequence[blocks.ColumnSpec]) -> pa.Schema:
    return pa.schema([(c.name, ARROW_TYPES[c.dtype]) for c in columns])


class ParquetBatchWriter:
    """Chunked parquet writer: memory stays bounded by write_batch_rows."""

    def __init__(self, out_dir: str, schema: pa.Schema, batch_rows: int = 50_000):
        self.out_dir = out_dir
        self.schema = schema
        self.batch_rows = batch_rows
        self.rows: List[Dict[str, Any]] = []
        self.n_written = 0
        self.n_files = 0
        os.makedirs(out_dir, exist_ok=True)

    def add(self, row: Dict[str, Any]) -> None:
        self.rows.append(row)
        if len(self.rows) >= self.batch_rows:
            self.flush()

    def extend(self, rows: Iterable[Dict[str, Any]]) -> None:
        for row in rows:
            self.add(row)

    def flush(self) -> None:
        if not self.rows:
            return
        columns = {name: [r.get(name) for r in self.rows] for name in self.schema.names}
        table = pa.Table.from_pydict(columns, schema=self.schema)
        path = os.path.join(self.out_dir, f"part_{self.n_files:05d}.parquet")
        pq.write_table(table, path, compression="zstd")
        self.n_written += len(self.rows)
        self.n_files += 1
        self.rows = []

    def close(self) -> int:
        self.flush()
        return self.n_written


class Factory:
    """The build. Nothing here reads a hardcoded lake path."""

    def __init__(self, inputs: sources.Inputs, config: BuildConfig):
        self.inputs = inputs
        self.config = config
        self.con: Optional[duckdb.DuckDBPyConnection] = None
        self.location_map: Optional[Dict[str, str]] = None
        self.section_map: Dict[str, str] = {}
        self.serve = serve_view.ServeView()
        self.manifest: Dict[str, Any] = {}
        self.ledger: obs.CoverageLedger = obs.CoverageLedger()
        self.consumers: List[capability.ConsumerRequirement] = []
        self.schema_epochs: Tuple[str, ...] = ()
        self.item_observability: Dict[str, Any] = {}
        #: Resolved in preflight, so a floor divergence raises before a byte of
        #: output exists rather than after a 1.5h build.
        self.floors: Optional[blocks.HistoryFloors] = None

    # --- history floors (D-1) -----------------------------------------------

    def resolve_floors(self, years: Sequence[int]) -> blocks.HistoryFloors:
        """Resolve the per-channel history floors for THIS build.

        Precedence: an explicit `history_floors` wins; otherwise derive from the
        build's own input years. `inherit_floors_from` then verifies the result
        against a reference manifest and RAISES on any divergence -- eval, drift
        and confirmation frames must carry the training build's denominators or
        every paired contrast between them is measuring two different questions.
        """
        cfg = self.config
        floors = cfg.history_floors or blocks.HistoryFloors.from_input_years(years)
        if cfg.inherit_floors_from:
            with open(cfg.inherit_floors_from, encoding="utf-8") as fh:
                reference = json.load(fh)
            expected = (reference.get("config", {}) or {}).get("history_floors")
            if not expected:
                raise gates.GateFailure(
                    f"reference manifest {cfg.inherit_floors_from} records no "
                    f"history_floors: it predates D-1 and cannot be inherited "
                    f"from. Rebuild the training frame first.")
            actual = floors.to_manifest()
            drift = {k: (expected.get(k), actual.get(k))
                     for k in ("result", "item", "severity")
                     if expected.get(k) != actual.get(k)}
            if drift:
                raise gates.GateFailure(
                    f"history-floor divergence vs {cfg.inherit_floors_from}: "
                    f"{drift}. A downstream frame MUST reproduce the training "
                    f"build's denominators; inheriting a different floor would "
                    f"silently change b1_observable_years between arms.")
            floors = blocks.HistoryFloors(
                result=date.fromisoformat(expected["result"]),
                item=date.fromisoformat(expected["item"]),
                severity=date.fromisoformat(expected["severity"]),
                source=f"inherited:{expected.get('source', 'unknown')}")
        self.floors = floors
        return floors

    # --- connection ---------------------------------------------------------

    def connect(self) -> duckdb.DuckDBPyConnection:
        gates.assert_duckdb_pin()
        con = duckdb.connect()
        cfg = self.config
        con.execute(f"PRAGMA memory_limit='{cfg.memory_limit}'")
        if cfg.temp_directory:
            os.makedirs(cfg.temp_directory, exist_ok=True)
            con.execute(f"PRAGMA temp_directory='{cfg.temp_directory}'")
            con.execute(f"PRAGMA max_temp_directory_size='{cfg.max_temp_directory_size}'")
        if cfg.threads:
            con.execute(f"PRAGMA threads={int(cfg.threads)}")
        self.con = con
        return con

    # --- gates + taxonomy ---------------------------------------------------

    def preflight(self, years: Sequence[int], recipes: Sequence[WindowRecipe],
                  build_confirmation: bool = False,
                  confirmation_prereg_sha: Optional[str] = None,
                  confirmation_prereg_path: Optional[str] = None) -> Dict[str, Any]:
        """Every refusal happens here, before a single output byte is written."""
        con = self.con or self.connect()
        cfg = self.config
        report: Dict[str, Any] = {}

        report["p4_certification"] = gates.assert_p4_certified(
            cfg.p4_certification_path or "")
        # D-1: resolve/verify the history floors here, so a divergence from the
        # reference manifest costs a preflight, not a 1.5h build.
        report["history_floors"] = self.resolve_floors(years).to_manifest()
        self.inputs.assert_lookup_present()

        # --- BUILDER/CONSUMER CAPABILITY (PREREG_CUBE_v2 §5) -----------------
        # First, and on configuration alone, so an incompatible pair costs
        # nothing. INC-2026-08-13 was `--defect-detail counts` staged, written
        # and then consumed with `--defect-text-source section`; that pair now
        # cannot get past this line.
        self.ledger = obs.load_coverage_ledger(cfg.item_coverage_csv)
        self.consumers = [capability.requirement_for(spec) for spec in cfg.consumers]
        capability.assert_build_can_serve(
            cfg.defect_detail, self.consumers, self.ledger.coverage_mode,
            emit_packets=cfg.emit_packets,
            recipe=",".join(r.name for r in recipes))
        report["item_coverage_ledger"] = self.ledger.as_manifest()
        report["declared_consumers"] = [c.as_manifest() for c in self.consumers]

        report["years"] = sources.assert_years_present(con, self.inputs.results_rel, years)

        report["effective_target_years"] = {}
        for recipe in recipes:
            gates.assert_target_fence(recipe.start, recipe.end, recipe.eval_slice,
                                      build_confirmation or recipe.confirmation)
            covered = [y for y in report["years"]
                       if recipe.start.year <= y <= (recipe.end - date.resolution).year]
            if not covered:
                raise sources.MissingYears(
                    f"recipe {recipe.name} targets {recipe.start}..{recipe.end}, which "
                    f"intersects none of --years {report['years']}: the build would "
                    f"emit zero rows. State the intersection deliberately.")
            report["effective_target_years"][recipe.name] = covered
        if build_confirmation:
            report["confirmation_prereg_sha"] = gates.assert_confirmation_prereg(
                confirmation_prereg_sha, confirmation_prereg_path)

        n_unknown_vocab = con.execute(atoms.unknown_vocabulary_count_sql(
            self.inputs, years, cfg.history_classes)).fetchone()[0]
        gates.assert_no_unknown_vocabulary(int(n_unknown_vocab))
        report["unknown_vocabulary_rows"] = int(n_unknown_vocab)

        # Publisher/schema identity of the source, recorded in the capability so
        # a packet set can never be reconciled against the wrong publication.
        epochs = con.execute(
            f"SELECT DISTINCT schema_epoch FROM {self.inputs.results_rel} r "
            f"WHERE {sources.year_filter_sql('r', years)}").fetchall()
        self.schema_epochs = tuple(sorted(str(e[0]) for e in epochs if e[0] is not None))
        report["publisher_schema_epochs"] = list(self.schema_epochs)

        # A cell rule outranks the join, so a WRONG rule silently destroys data.
        probe = atoms.dark_cell_items_probe_sql(self.inputs, self.ledger, years,
                                                cfg.history_classes)
        n_contradictions = int(con.execute(probe).fetchone()[0]) if probe else 0
        gates.assert_coverage_ledger_consistent(n_contradictions,
                                                cfg.item_coverage_csv)
        report["coverage_ledger_contradictions"] = n_contradictions

        self.section_map = taxonomy.load_section_map(
            self.inputs.item_detail_csv, self.inputs.item_group_csv)
        report["n_class4_rfr_ids"] = taxonomy.assert_no_fanout(
            con, self.inputs.item_detail_csv, self.inputs.item_group_csv)
        taxonomy.register_category_table(con, self.section_map)

        self.location_map = taxonomy.load_location_groups(self.inputs.location_csv)
        if self.location_map:
            con.execute("DROP TABLE IF EXISTS factory_location_group")
            con.execute("CREATE TABLE factory_location_group "
                        "(location_id VARCHAR, lat_group VARCHAR, "
                        "long_group VARCHAR, vert_group VARCHAR)")
            con.executemany("INSERT INTO factory_location_group VALUES (?, ?, ?, ?)",
                            [(k, lat, lon, vert)
                             for k, (lat, lon, vert) in self.location_map.items()])
        report["location_map"] = "present" if self.location_map else "absent"

        unknown = con.execute(atoms.unknown_disposition_sample_sql(
            self.inputs, years, cfg.history_classes)).fetchall()
        gates.assert_no_unknown_dispositions(
            sum(int(r[2]) for r in unknown),
            [{"code": r[0], "era": r[1], "n": int(r[2])} for r in unknown])
        report["unknown_disposition_rows"] = 0

        # m-8: the prior-count pathology is cheap to detect HERE, not hours into
        # a real scan. Counted over the history scope, before any output.
        pathology = con.execute(f"""
            SELECT count(*) FROM (
                SELECT vehicle_id FROM {self.inputs.results_rel} r
                WHERE {sources.year_filter_sql('r', years)}
                  AND {sources.test_class_filter_sql('r', cfg.history_classes)}
                GROUP BY vehicle_id HAVING count(*) > {int(cfg.pathology_bound)})
        """).fetchone()[0]
        if pathology:
            raise gates.GateFailure(
                f"{pathology:,} vehicle(s) carry more than {cfg.pathology_bound} "
                f"test records in the requested years. Full-depth measured max is "
                f"50 (p99.9=28) -- investigate before raising --pathology-bound; "
                f"the packets emitter would otherwise abort mid-scan.")
        report["vehicles_over_pathology_bound"] = 0

        self.serve = serve_view.load_serve_view(cfg.serve_view_classes_path)
        report["serve_view"] = self.serve.manifest_entry()
        # M-3: a research-only-input column must never inherit a deployable
        # class from its block. Report any instance entry that says otherwise.
        conflicts = [c.name for c in blocks.ALL_COLUMNS
                     if c.era_observability == blocks.ERA_RESEARCH
                     and c.name in self.serve.by_feature
                     and self.serve.class_for_column(c.name, c.block)
                     != serve_view.RESEARCH_ONLY_BY_ERA]
        report["serve_view_research_only_conflicts"] = conflicts

        n_new = blocks.n_new_columns()
        if n_new > blocks.NEW_COLUMN_CAP:
            raise gates.GateFailure(
                f"B1-B6 emit {n_new} columns, above the contract cap "
                f"{blocks.NEW_COLUMN_CAP}")
        report["n_new_columns"] = n_new
        return report

    # --- phase 1: staging ---------------------------------------------------

    def _vehicle_day_dir(self) -> str:
        return os.path.join(self.config.staging_dir, "vehicle_day")

    def _events_dir(self, recipe: str) -> str:
        return os.path.join(self.config.staging_dir, "events", f"recipe={recipe}")

    @staticmethod
    def _reset_dir(path: str) -> None:
        """Delete-and-recreate a staging target.

        `COPY ... OVERWRITE_OR_IGNORE` overwrites the files it happens to write
        and leaves everything else in place, so re-running into the same
        --staging-dir silently mixes two builds (adversarial review B-2: a ghost
        vehicle from the previous run entered the scan with no error). It is
        also shrink-unsafe on an IDENTICAL re-run: FILENAME_PATTERN's {i} count
        is thread/vector dependent, so a rerun that writes fewer part-files
        leaves the surplus behind and duplicates rows.
        """
        if os.path.exists(path):
            shutil.rmtree(path)
        os.makedirs(path, exist_ok=True)

    @staticmethod
    def _staged_files(root: str) -> int:
        return len(glob.glob(os.path.join(root, "**", "*.parquet"), recursive=True))

    def prepare(self, years: Sequence[int], recipes: Sequence[WindowRecipe],
                restrict_max_u: Optional[float] = None) -> Dict[str, Any]:
        con = self.con or self.connect()
        cfg = self.config
        restrict_pred = None
        if restrict_max_u is not None:
            # Full-pop staging pushdown: stage only the sampled rung's vehicles.
            # u is vehicle-level (unit_hash on vehicle_id with the recipe salt),
            # so restriction preserves whole histories. Requires ONE recipe —
            # the predicate is salt-specific.
            if len(recipes) != 1:
                raise ValueError("restrict_max_u needs exactly one recipe "
                                 "(the predicate is salt-specific)")
            from .sampling import unit_hash_sql
            restrict_pred = (f"{unit_hash_sql('r.vehicle_id', recipes[0].salt)}"
                             f" < {float(restrict_max_u)!r}")
        out: Dict[str, Any] = {"vehicle_day_rows_by_year": {}, "event_rows": {},
                               "staging_dir": cfg.staging_dir, "staged_files": {},
                               "restrict_max_u": restrict_max_u}
        # Staging COPYs run with insertion-order preservation OFF (first real
        # build OOM'd at 3GB and 3.5GB in the 64-partition COPY, 2026-08-12).
        # SAFE here and only here: every staging consumer either re-orders
        # explicitly (the stateful scan's ORDER BY vehicle_id, test_date) or is
        # order-free (calibrate counts/quantiles, hash-threshold sampling).
        # Restored before return so frame emission keeps default semantics.
        con.execute("SET preserve_insertion_order=false")
        # B-2: the staging scope is wiped, never merged into.
        self._reset_dir(self._vehicle_day_dir())

        for year in sorted(set(int(y) for y in years)):
            sql = atoms.vehicle_day_atom_sql(
                self.inputs, years, cfg.history_classes, bool(self.location_map),
                cfg.n_buckets, cfg.defect_detail, restrict_year=year,
                restrict_pred=restrict_pred, ledger=self.ledger)
            # COPY returns the written row count: no second execution of the
            # most expensive query in the build (review m-7).
            written = con.execute(f"""
                COPY ({sql}) TO '{self._vehicle_day_dir()}'
                (FORMAT parquet, COMPRESSION zstd, PARTITION_BY (bucket),
                 OVERWRITE_OR_IGNORE true, FILENAME_PATTERN 'y{year}_{{i}}')
            """).fetchone()
            out["vehicle_day_rows_by_year"][year] = int(written[0]) if written else None
        out["staged_files"]["vehicle_day"] = self._staged_files(self._vehicle_day_dir())
        # Measured, never assumed: what the source/partitions ACTUALLY offered,
        # read back off the staged atoms (cheap -- they are already reduced).
        self.item_observability = self._measure_item_observability(con)
        out["item_observability"] = self.item_observability

        for recipe in recipes:
            sql = atoms.events_sql(self.inputs, recipe.start.isoformat(),
                                   recipe.end.isoformat(), cfg.target_classes,
                                   cfg.n_buckets, recipe.salt, years,
                                   restrict_pred=restrict_pred)
            target = self._events_dir(recipe.name)
            self._reset_dir(target)
            written = con.execute(f"""
                COPY ({sql}) TO '{target}'
                (FORMAT parquet, COMPRESSION zstd, PARTITION_BY (bucket),
                 OVERWRITE_OR_IGNORE true, FILENAME_PATTERN 'ev_{{i}}')
            """).fetchone()
            out["event_rows"][recipe.name] = int(written[0]) if written else None
            out["staged_files"][f"events/{recipe.name}"] = self._staged_files(target)
        con.execute("SET preserve_insertion_order=true")
        return out

    def _measure_item_observability(self, con) -> Dict[str, Any]:
        """Per-year item join + PREREG §4 state volumes over the STAGED atoms.

        This is the "expected source/partition availability" and "successful
        item join" the capability must carry. It is MEASURED off what was
        actually staged, so it can never drift from the packets it describes.
        """
        pattern = os.path.join(self._vehicle_day_dir(), "**", "*.parquet")
        if not glob.glob(pattern, recursive=True):
            return {"by_year": {}, "total": {}, "note": "nothing staged"}
        rel = f"read_parquet('{pattern}', union_by_name=true)"
        cols = ", ".join(f"sum({c}) AS {c}" for c in obs.DAY_COUNT_COLUMNS)
        rows = con.execute(
            f"SELECT test_year, sum(n_tests_day) AS n_tests, {cols} "
            f"FROM {rel} GROUP BY test_year ORDER BY test_year").fetchall()
        names = ["test_year", "n_tests"] + list(obs.DAY_COUNT_COLUMNS)
        by_year: Dict[str, Any] = {}
        totals = {c: 0 for c in obs.DAY_COUNT_COLUMNS}
        for raw in rows:
            record = dict(zip(names, raw))
            year = str(int(record["test_year"]))
            by_year[year] = obs.counts_manifest(record)
            for column in obs.DAY_COUNT_COLUMNS:
                totals[column] += int(record.get(column) or 0)
        return {
            "grain": "staged vehicle_day atoms, per input year",
            "coverage_mode": self.ledger.coverage_mode,
            "by_year": by_year,
            "total": obs.counts_manifest(totals),
            "states": list(obs.ALL_STATES),
            "residual_note": (
                "inside a covered cell a PASS with zero items is undecidable; "
                "bounded by the same-cell fail-bearing miss rate (<=1.6e-5 "
                "measured whole-population). Not papered over: those rows are "
                f"emitted as {obs.PRESENT_ZERO_DEFECTS!r} (certified cell) or "
                f"{obs.ASSUMED_ZERO_DEFECTS!r} (undeclared cell)."),
        }

    def packet_capability(self, recipe: str, rung: str) -> capability.PacketCapability:
        """The capability sidecar for one (recipe, rung) packet set."""
        lookup_shas = {}
        for label, path in (("item_detail_csv", self.inputs.item_detail_csv),
                            ("item_group_csv", self.inputs.item_group_csv)):
            lookup_shas[label] = _sha256_file(path) if os.path.exists(path) else None
        return capability.PacketCapability(
            packet_schema_version=capability.PACKET_SCHEMA_VERSION,
            contract_version=CONTRACT_VERSION,
            defect_payload_mode=self.config.defect_detail,
            defect_keys=tuple(packets.DEFECT_KEYS),
            items_observability_states=tuple(obs.ALL_STATES),
            items_coverage_mode=self.ledger.coverage_mode,
            item_coverage_ledger=self.ledger.as_manifest(),
            item_join=self.item_observability,
            source_partition_availability=self.item_observability.get("by_year", {}),
            publisher_schema_epochs=self.schema_epochs,
            build_config=self.config.as_manifest(),
            source_sha256={"lookup": lookup_shas,
                           "item_coverage_ledger": self.ledger.sha256,
                           "results": _glob_summary(self.inputs.results),
                           "items": _glob_summary(self.inputs.items),
                           "build_config_sha256": self.config.config_sha256()},
            code_sha256=code_sha(),
            duckdb_version=duckdb.__version__,
            recipe=recipe, rung=rung,
            capability_source="declared",
            notes=("defects_json: populated | '[]' (observed, no defect items) | "
                   "NULL (detail unavailable, or payload mode != 'rows'). "
                   "p_items_observability names the state per prior test. No "
                   "consumer may read a NULL as 'no defect' (PREREG_CUBE_v2 §4)."))

    # --- calibration --------------------------------------------------------

    def calibrate(self, recipe: WindowRecipe,
                  targets: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
        """Measure the u-thresholds that hit the requested rung row counts.

        Deterministic and cheap: a quantile over the staged event hashes. The
        owner PINS the printed fractions; the factory never silently retunes.
        """
        con = self.con or self.connect()
        targets = targets or sampling.DEFAULT_RUNG_TARGETS
        rel = f"read_parquet('{self._events_dir(recipe.name)}/**/*.parquet')"
        total = int(con.execute(f"SELECT count(*) FROM {rel}").fetchone()[0])
        out: Dict[str, Any] = {"recipe": recipe.name, "events_in_window": total,
                               "thresholds": {}}
        for name, want in sorted(targets.items(), key=lambda kv: kv[1]):
            if total == 0:
                out["thresholds"][name] = None
                continue
            frac = min(1.0, want / total)
            threshold = con.execute(
                f"SELECT quantile_cont(u, {frac}) FROM {rel}").fetchone()[0]
            realised = int(con.execute(
                f"SELECT count(*) FROM {rel} WHERE u < {threshold}").fetchone()[0])
            out["thresholds"][name] = {"base": float(threshold),
                                       "target_rows": want, "realised_rows": realised}
        return out

    # --- phase 2: the stateful scan ----------------------------------------

    def _scan_sql(self, recipe: str, bucket: int) -> str:
        day_rel = (f"read_parquet('{self._vehicle_day_dir()}/bucket={bucket}/*.parquet', "
                   f"union_by_name=true)")
        ev_rel = (f"read_parquet('{self._events_dir(recipe)}/bucket={bucket}/*.parquet', "
                  f"union_by_name=true)")
        return f"""
WITH ev AS (
    SELECT vehicle_id, tgt_date,
           list(struct_pack(
               tgt_id := tgt_id, tgt_outcome := tgt_outcome,
               y_final := y_final, y_initial := y_initial,
               tgt_test_class_id := tgt_test_class_id, tgt_test_type := tgt_test_type,
               tgt_miles := tgt_miles, tgt_make := tgt_make, tgt_model := tgt_model,
               tgt_model_id := tgt_model_id, tgt_fuel := tgt_fuel,
               tgt_colour := tgt_colour, tgt_cc := tgt_cc, tgt_fud := tgt_fud,
               tgt_pc := tgt_pc, tgt_age_at_test := tgt_age_at_test,
               tgt_taxonomy_era := tgt_taxonomy_era, u := u)) AS events
    FROM {ev_rel}
    GROUP BY vehicle_id, tgt_date
)
SELECT d.*, ev.events
FROM {day_rel} d
LEFT JOIN ev ON ev.vehicle_id = d.vehicle_id AND ev.tgt_date = d.test_date
ORDER BY d.vehicle_id, d.test_date
"""

    def _item_columns(self, con, recipe: str, bucket: int) -> List[str]:
        cols = con.execute(
            f"SELECT * FROM ({self._scan_sql(recipe, bucket)}) LIMIT 0").description
        names = [c[0] for c in cols]
        return [n for n in names
                if n.startswith(("cat_", "pos_"))
                or n in atoms.SCALAR_ITEM_COLUMNS]

    def buckets(self) -> List[int]:
        found = sorted(int(os.path.basename(p).split("=")[1])
                       for p in glob.glob(os.path.join(self._vehicle_day_dir(), "bucket=*")))
        return found

    def scan(self, recipe: WindowRecipe) -> Iterator[Tuple[Dict[str, Any], List[Dict[str, Any]]]]:
        """Yield (feature_row, packet_rows) for every event, in bucket order."""
        con = self.con or self.connect()
        cfg = self.config
        location_present = bool(self.location_map)
        for bucket in self.buckets():
            if not glob.glob(os.path.join(self._events_dir(recipe.name),
                                          f"bucket={bucket}", "*.parquet")):
                continue
            item_columns = self._item_columns(con, recipe.name, bucket)
            cursor = con.execute(self._scan_sql(recipe.name, bucket))
            names = [c[0] for c in cursor.description]
            current_vehicle: Optional[int] = None
            state: Optional[st.AsOfState] = None
            while True:
                batch = cursor.fetchmany(2_000)
                if not batch:
                    break
                for raw in batch:
                    row = dict(zip(names, raw))
                    vehicle_id = int(row["vehicle_id"])
                    if vehicle_id != current_vehicle:
                        current_vehicle = vehicle_id
                        state = st.AsOfState(vehicle_id=vehicle_id,
                                             fail_basis=cfg.fail_basis)
                    day = st.day_atom_from_row(row, item_columns)
                    for event in (row.get("events") or []):
                        yield self._emit_event(state, day, event, recipe,
                                               location_present, bucket)
                    state.update(day)

    def _emit_event(self, state: st.AsOfState, day: st.DayAtom, event: dict,
                    recipe: WindowRecipe, location_present: bool, bucket: int
                    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        cfg = self.config
        ev = dict(event)
        ev["tgt_date"] = day.test_date
        ev["vehicle_id"] = state.vehicle_id
        feature_row: Dict[str, Any] = {
            "recipe": recipe.name, "rung": None,
            "tgt_id": ev.get("tgt_id"), "vehicle_id": state.vehicle_id,
            "tgt_date": day.test_date, "tgt_year": day.test_date.year,
            "tgt_outcome": ev.get("tgt_outcome"), "y_final": ev.get("y_final"),
            "y_initial": ev.get("y_initial"),
            "tgt_test_class_id": ev.get("tgt_test_class_id"),
            "tgt_test_type": ev.get("tgt_test_type"), "tgt_miles": ev.get("tgt_miles"),
            "tgt_make": ev.get("tgt_make"), "tgt_model": ev.get("tgt_model"),
            "tgt_model_id": ev.get("tgt_model_id"), "tgt_fuel": ev.get("tgt_fuel"),
            "tgt_colour": ev.get("tgt_colour"), "tgt_cc": ev.get("tgt_cc"),
            "tgt_fud": ev.get("tgt_fud"), "tgt_pc": ev.get("tgt_pc"),
            "tgt_age_at_test": ev.get("tgt_age_at_test"),
            "tgt_taxonomy_era": ev.get("tgt_taxonomy_era"),
            "sample_u": ev.get("u"), "sample_bucket": bucket,
        }
        feature_row.update(blocks.emit_all(state, ev, location_present,
                                          floors=self.floors or blocks.DEFAULT_FLOORS))
        stratum = sampling.enrichment_stratum(
            n_prior_dangerous_days=int(state.n_dangerous_days),
            days_since_prior_fail_day=state.days_since(state.last_fail_date, day.test_date),
            n_prior_test_days=int(state.n_days))
        feature_row["enrichment_stratum"] = stratum
        feature_row["inclusion_weight"] = 1.0

        packet_rows: List[Dict[str, Any]] = []
        if cfg.emit_packets:
            packet_rows = packets.emit_packet_rows(
                ev, state.prior_tests, max_priors=cfg.max_priors,
                pathology_bound=cfg.pathology_bound,
                defect_payload_mode=cfg.defect_detail)
        return feature_row, packet_rows

    # --- write --------------------------------------------------------------

    def build(self, years: Sequence[int], recipes: Sequence[WindowRecipe],
              ladder: sampling.RungLadder, preflight_report: Dict[str, Any],
              prepare_report: Dict[str, Any]) -> Dict[str, Any]:
        cfg = self.config
        frame_schema = _arrow_schema(blocks.ALL_COLUMNS)
        results: Dict[str, Any] = {"recipes": {}}
        for recipe in recipes:
            per_rung: Dict[str, Dict[str, Any]] = {}
            writers: Dict[str, ParquetBatchWriter] = {}
            packet_writers: Dict[str, ParquetBatchWriter] = {}
            counters: Dict[str, Dict[str, int]] = {}
            for rung in ladder.rungs:
                base = os.path.join(cfg.output_dir, f"recipe={recipe.name}",
                                    f"rung={rung.name}")
                writers[rung.name] = ParquetBatchWriter(
                    os.path.join(base, "frame"), frame_schema, cfg.write_batch_rows)
                if cfg.emit_packets:
                    packet_writers[rung.name] = ParquetBatchWriter(
                        os.path.join(base, "packets"), PACKET_ARROW_SCHEMA,
                        cfg.write_batch_rows)
                counters[rung.name] = {"rows": 0, "enriched": 0, "packet_rows": 0}

            for feature_row, packet_rows in self.scan(recipe):
                u = feature_row["sample_u"]
                stratum = feature_row["enrichment_stratum"]
                for rung in ladder.rungs:
                    if not rung.selects(u, stratum):
                        continue
                    row = dict(feature_row)
                    row["rung"] = rung.name
                    row["inclusion_weight"] = rung.inclusion_weight(u, stratum)
                    writers[rung.name].add(row)
                    counters[rung.name]["rows"] += 1
                    if u >= rung.base:
                        counters[rung.name]["enriched"] += 1
                    if cfg.emit_packets:
                        packet_writers[rung.name].extend(packet_rows)
                        counters[rung.name]["packet_rows"] += len(packet_rows)

            for rung in ladder.rungs:
                n_rows = writers[rung.name].close()
                n_packets = (packet_writers[rung.name].close()
                             if cfg.emit_packets else 0)
                ok, share = sampling.check_enrichment_share(
                    counters[rung.name]["enriched"], n_rows)
                per_rung[rung.name] = {
                    "rows": n_rows, "packet_rows": n_packets,
                    "base_threshold": rung.base, "enriched_threshold": rung.enriched,
                    "target_rows": rung.target_rows,
                    "enriched_rows": counters[rung.name]["enriched"],
                    "enriched_share": share,
                    "enriched_share_within_cap": ok,
                }
                if cfg.emit_packets:
                    # The capability sidecar is written INSIDE the packets
                    # directory so it can never be separated from the parquet it
                    # describes, and so a consumer given only the packets glob
                    # can always find it.
                    cap = self.packet_capability(recipe.name, rung.name)
                    per_rung[rung.name]["packet_capability"] = cap.write(
                        packet_writers[rung.name].out_dir)
                    per_rung[rung.name]["defect_payload_mode"] = cap.defect_payload_mode
                    per_rung[rung.name]["items_coverage_mode"] = cap.items_coverage_mode
            results["recipes"][recipe.name] = {"window": recipe.as_manifest(),
                                               "rungs": per_rung}

        self.manifest = self.build_manifest(years, recipes, ladder, preflight_report,
                                            prepare_report, results, dry_run=False)
        self.write_manifest()
        return self.manifest

    # --- manifest -----------------------------------------------------------

    def build_manifest(self, years: Sequence[int], recipes: Sequence[WindowRecipe],
                       ladder: sampling.RungLadder,
                       preflight_report: Dict[str, Any],
                       prepare_report: Dict[str, Any],
                       results: Dict[str, Any], dry_run: bool) -> Dict[str, Any]:
        lookup_shas = {}
        for label, path in (("item_detail_csv", self.inputs.item_detail_csv),
                            ("item_group_csv", self.inputs.item_group_csv)):
            lookup_shas[label] = _sha256_file(path) if os.path.exists(path) else None
        return {
            "dry_run": dry_run,
            "contract_version": CONTRACT_VERSION,
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "duckdb_version": duckdb.__version__,
            "code_sha256": code_sha(),
            "inputs": {
                "results": _glob_summary(self.inputs.results),
                "items": _glob_summary(self.inputs.items),
                "completed_ts": (_glob_summary(self.inputs.completed_ts)
                                 if self.inputs.completed_ts else None),
                "lookup_sha256": lookup_shas,
                "location_csv": self.inputs.location_csv,
            },
            "input_years": sorted(int(y) for y in years),
            "history_floors": (self.floors or self.resolve_floors(years)).to_manifest(),
            "config": self.config.as_manifest(),
            "salts": {"sample": sampling.SAMPLE_SALT, "eval": sampling.EVAL_SALT,
                      "confirmation": sampling.CONFIRMATION_SALT,
                      "bucket": sampling.BUCKET_SALT,
                      "rule": "u = hash(CAST(vehicle_id AS VARCHAR) || salt) / 2^64"},
            "rungs": [{"name": r.name, "base": r.base, "enriched": r.enriched,
                       "target_rows": r.target_rows} for r in ladder.rungs],
            "recipes": [r.as_manifest() for r in recipes],
            "eval_slice_definitions": EVAL_SLICE_DEFINITIONS,
            "confirmation_slice_definition": CONFIRMATION_DEFINITION,
            "columns": {
                "n_meta": len(blocks.META_COLUMNS),
                "n_new_b1_b6": blocks.n_new_columns(),
                "cap": blocks.NEW_COLUMN_CAP,
                "by_block": {b: len(blocks.BLOCK_COLUMNS[b]) for b in blocks.NEW_BLOCKS},
                "deployability_classes": {
                    c.name: self.serve.class_for_column(c.name, c.block,
                                                        c.era_observability)
                    for c in blocks.ALL_COLUMNS},
            },
            "packets": {
                "emitted": self.config.emit_packets,
                "shape": "long (one row per target x prior; zero-prior targets emit one NULL-prior row)",
                "columns": packets.PACKET_COLUMNS,
                "defect_keys": list(packets.DEFECT_KEYS),
                "no_free_text": True,
                "result_map": packets.RESULT_MAP,
                "schema_version": capability.PACKET_SCHEMA_VERSION,
                "defect_payload_mode": self.config.defect_detail,
                "defects_json_states": {
                    "populated": "observed, defect items present",
                    "[]": "observed, no defect items",
                    "null": ("detail unavailable, OR defect_payload_mode != 'rows'. "
                             "NEVER read as 'no defect' (PREREG_CUBE_v2 §4)."),
                },
                "declared_consumers": [c.as_manifest() for c in self.consumers],
                "capability_sidecar": capability.CAPABILITY_FILENAME,
            },
            "item_observability": {
                "states": list(obs.ALL_STATES),
                "coverage_mode": self.ledger.coverage_mode,
                "ledger": self.ledger.as_manifest(),
                "measured": self.item_observability,
            },
            "preflight": preflight_report,
            "prepare": prepare_report,
            "results": results,
        }

    def write_manifest(self, filename: str = "BUILD_MANIFEST.json") -> str:
        os.makedirs(self.config.output_dir, exist_ok=True)
        path = os.path.join(self.config.output_dir, filename)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.manifest, fh, indent=1, default=str)
        return path
