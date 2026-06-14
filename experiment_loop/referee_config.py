"""referee_config.py — THE IMMUTABLE CORE. Protected by referee_guard.py.

The only things the agent may not silently change: the datasets it is trained and
scored on, the label, the gates, the win criterion, and the held-out window. The
agent may PROPOSE changes here via eval_proposals.md; a human ratifies them between
runs. Keeping this fixed is what makes the agent's otherwise-maximal autonomy
produce trustworthy results instead of noise — see program.md.

The rule: this file governs WHAT the agent is judged against; config.py governs HOW
it builds features (mutable, the agent's to rewrite). Anything that could let the
agent train on its own eval set, relabel, neutralise a gate, or move the bar lives
HERE.
"""
from __future__ import annotations

from pathlib import Path

_BK = Path.home() / "autosafe/work/bakeoff_2026"
_AUDIT = Path.home() / "autosafe/work/audit_2026"

# --- the data the agent is trained and scored on (cannot be repointed) ------
DEV_FRAME = _BK / "v57_1_dev.parquet"        # train/val
OOT_FRAME = _BK / "v57_1_oot.parquet"        # screening eval (development-grade)
LABEL_COL = "y"                              # cannot be relabelled

# --- the gates (cannot be neutralised by repointing) ------------------------
GF17_GATE = _AUDIT / "gf_gates/gf17_train_serve_parity.py"   # train/serve parity
RG_GATES = _BK / "r3_rg_gates.py"                            # RG-1..4

# --- pre-registered, promotion-eligible slices (never a post-hoc slice) -----
# The agent may PROPOSE additions via eval_proposals.md.
SLICES = [
    "age_le_3", "age_4_7", "age_8_12", "age_13_19", "age_ge_20",
    "age_10_14", "age_14_plus",
    "no_or_low_history", "first_observed_mot",
    "class7_or_van", "ev_or_hybrid", "diesel_over_10y",
    "high_annual_miles", "low_annual_miles", "prior_advisory_present",
]

# --- the keep/revert criterion (zero agent discretion at scoring time) -------
# Units are named explicitly: *_pp = percentage points of AUC (+0.30pp == +0.0030 AUC),
# *_abs = absolute ECE. All keys are read by decision.decide / decision.summarize_report
# (arm0_harness.verdict, which read the old un-suffixed keys, has been retired).
PROMOTION = {
    "seed_dead_zone_pp": 0.05,           # |per-seed ΔAUC| <= this counts as "flat" (pp)
    "pooled_d_auc_min_pp": 0.30,         # required pooled ΔAUC (pp)
    "median_seed_d_auc_min_pp": 0.10,    # required median per-seed ΔAUC (pp)
    "within_segment_min_slices": 2,      # required # within-segment AUC wins (GF-8 defense)
    "leakage_min_auc_drop_pp": 0.10,     # shuffle-within-fold must drop AUC; < this => dead (pp)
    "ece_worsen_max_abs": 0.01,          # calibration veto: ΔECE > this in ANY slice (absolute)
    "ece_red_line_abs": 0.10,            # work/ red line (absolute ECE)
    "promotion_min_seeds": 5,            # promotion-grade requires >= this many seeds
}

# --- the single-shot honest number ------------------------------------------
# Scored ONCE on the final composite; NEVER read during search. Path is a
# placeholder until the cycle-corrected window is cut.
HELDOUT = {
    "frame": None,
    "policy": "scored once on the final composite; never read during search",
    "protocol": "cycle-initial walk-back with inclusion-prob weighting (RCA mandate)",
}
