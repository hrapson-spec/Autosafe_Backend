"""Training-frame factory (model programme 2026-08, contract v1).

Builds as-of-correct, D13-safe training frames from the v58 lake. The package
is code + fixture-tested falsifiers: it never runs against the real lake in the
builder agent's hands -- the owner executes real builds.

Layout:
    severity.py   F-22-corrected disposition/severity derivation (rule + SQL twin)
    taxonomy.py   class-4 rfr_id -> section -> 7 categories (fan-out guarded)
    atoms.py      item_test_atom / vehicle_day_atom / events SQL
    sampling.py   salted-hash buckets, rungs, eval slices, enrichment weights
    state.py      AsOfState -- running per-vehicle aggregates, emit-before-update
    blocks.py     B1..B6 column emitters + the emitted-column registry
    packets.py    the packets view (contract v2: B0 comes from the packets ->
                  feature_engineering_v55 path, NOT from this factory)
    serve_view.py deployability-class loading (schema-validated, not hardcoded)
    gates.py      p4 certification / year-list / target-fence / prereg gates
    emit.py       recipes, labels, parquet writer, BUILD_MANIFEST
    build.py      CLI (`--dry-run` prints the manifest it WOULD produce)

Determinism rule of record (decision D13): no emitted value may depend on
within-day row order or on test_id. Determinism comes from
(vehicle_id, test_date) alone; tests/test_falsifiers.py::test_f9_no_bare_test_id
is the AST/source gate over this package.
"""
import sys

# The lake's semantics are imported, never re-implemented (contract, Atoms 2-3).
REPO_ROOT = "/Users/henrirapson/autosafe-v58"
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

CONTRACT_VERSION = "factory-contract-v2 (2026-08-12; B0 removed, packets view + panel-first)"
EXPECTED_DUCKDB = "1.5.5"

__all__ = ["CONTRACT_VERSION", "EXPECTED_DUCKDB", "REPO_ROOT"]
