"""Mutable plumbing for the AutoSafe ExperimentLoop — the agent is free to edit
this whole file. It governs HOW features are built and HOW training runs; it does
NOT govern what the agent is judged against.

PROTECTED elsewhere (referee_config.py, in referee_guard's denylist): the train/eval
FRAMES, the LABEL, the GATES, the SLICES, the win criterion (PROMOTION), and the
held-out window — so the agent cannot train on its eval set, relabel, neutralise a
gate, or move the bar. evaluate.py reads those from referee_config.

Paths are the REAL locations of UNTRACKED working artifacts in the live ~/autosafe
tree (the work/ harness is not in git — see README). Nothing here runs. Values from
real interfaces: r4_bench.py, r2b_build_v57.py, b5_compare.py.
"""
from __future__ import annotations

from pathlib import Path

# --- trees -----------------------------------------------------------------
HOME = Path.home()
AUTOSAFE = HOME / "autosafe"                     # live tree (DO NOT WRITE INTO)
WORK = AUTOSAFE / "work"
BK = WORK / "bakeoff_2026"
AUDIT = WORK / "audit_2026"
EVIDENCE = AUDIT / "evidence"                    # manifest.jsonl provenance sink
ICLOUD = HOME / "Library/Mobile Documents/com~apple~CloudDocs/AutoSafe"

# model_bundle.load_contract lives here (NOT on origin/main yet; r4_bench.py:62-64)
BUNDLE_IMPORT_PATHS = [AUTOSAFE, HOME / "autosafe-phaseA"]
# deployed serving FE (model_v55, feature_engineering_v55, dvsa_client)
DEPLOYED = Path("/tmp/autosafe_deployed_e469d3a")

# --- feature-build inputs (the agent may repoint these; they change HOW it
#     computes features, never WHAT it is scored against) --------------------
CONTRACT = BK / "v57_1_feature_contract.json"    # emitted feature names+order
PACKETS = BK / "v57_packets.parquet"             # per-target prior-history rows, 100% coverage
TEXT_COLS = ["adv_text", "fail_text"]
FLAG_COLS = ["has_adv_text", "has_fail_text"]
DEV_SOURCE = ICLOUD / "stratified_samples/dev_set.parquet"     # cohort-entry targets
OOT_SOURCE = ICLOUD / "stratified_samples/oot_test_set.parquet"
R4_BENCH = BK / "r4_bench.py"                     # training machinery
AUDIT_LIB = AUDIT                                 # sys.path for `from audit_lib import emit`

# NOTE: the train/eval FRAMES (DEV_FRAME/OOT_FRAME), the LABEL, and the GATES are
# in referee_config.py (PROTECTED) — not here, on purpose.

# --- training (exact, from r4_bench.py; the agent may tune these) -----------
CAT_FEATURES = ["prev_cycle_outcome_band", "gap_band", "make", "advisory_trend",
                "usage_band_hybrid", "negligence_band", "mech_risk_driver",
                "dominant_mechanism", "test_month", "day_of_week"]
PARAMS = {"iterations": 2000, "learning_rate": 0.02, "depth": 6, "l2_leaf_reg": 4,
          "border_count": 128, "random_strength": 1.0, "bagging_temperature": 0.5}
SPLIT = {"test_size": 0.15, "random_state": 42}   # stratified on y, from dev (train/val only)
SEED_LADDER = [2, 5, "full"]                      # search escalation; 2 screen -> 5 -> full

# --- budget: maximal autonomy — no candidate / time caps -------------------
# NEVER STOP until the human interrupts. The single number here is an operational
# hygiene guard so one runaway TRAIN cannot wedge the loop — it is not an idea or
# exploration limit. (Token/compute cost scales with run length; accepted for
# agent-driven per Henri's choice. Physical throughput on the M3 is still ~3-4
# screened candidates/hr — maximal autonomy widens WHAT is explored, not raw speed.)
BUDGET = {
    "max_candidates": None,                       # unbounded
    "max_wallclock_hours": None,                  # until interrupted
    "max_minutes_per_candidate": 45,              # hygiene: kill a hung train, not an idea cap
}

# --- baseline-to-beat: UNSET until v57_auc_rca settles ---------------------
BASELINE = {
    "status": "PLACEHOLDER_PENDING_RCA",
    "oot_auc_v57_rebuilt": 0.7133,
    "oot_auc_v55_frame": 0.7273,
    "within_segment_auc_range": (0.61, 0.67),     # GF-8
}
