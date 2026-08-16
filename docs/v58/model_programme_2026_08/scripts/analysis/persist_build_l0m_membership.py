#!/usr/bin/env python3
"""Build out/PERSIST_B0_ADVISORY_COLUMNS.json — the frozen L0m membership.

REPRODUCIBLE BY CONSTRUCTION: tier 1 is derived mechanically from a regex over the
live FEATURE_NAMES list; tier 2 is a declared list where every entry carries a
file:line citation to the code that makes it advisory-derived; tier 3 is the
documented RESIDUAL that L0m deliberately does NOT remove.

Feature metadata only. Reads no outcome.
"""
import json
import re
from pathlib import Path

PROG = Path("/Users/henrirapson/autosafe-v58/docs/v58/model_programme_2026_08")
FE = Path("/Users/henrirapson/autosafe/feature_engineering_v55.py")   # named by the B0 manifest

src = FE.read_text()
names = re.findall(r"['\"]([^'\"]+)['\"]",
                   re.search(r"FEATURE_NAMES\s*=\s*\[(.*?)\]", src, re.S).group(1))
assert len(names) == 104, f"expected 104 B0 features, got {len(names)}"

# ---- Tier 1: mechanical name match -----------------------------------------
TIER1_RE = re.compile(r"advis|_adv_|prev_adv")
tier1 = [n for n in names if TIER1_RE.search(n)]

# ---- Tier 2: code-verified advisory-derived, NOT name-matched ---------------
# Every entry cites the line that makes it advisory-derived. Verified 2026-08-16.
TIER2 = {
    "brake_system_stress":                "feature_engineering_v55.py:870 = prev_adv_brakes + age_factor",
    "historic_negligence_ratio_smoothed": "…:886 = prev_count_advisory / len(tests)",
    "negligence_band":                    "…:894-901 banding of historic_negligence_ratio_smoothed",
    "raw_behavioral_count":               "…:904 = prev_count_advisory (exact alias)",
    "mech_decay_brake":                   "…:610 = len(component_advisories['brakes']) * 0.2",
    "mech_decay_suspension":              "…:611 = len(component_advisories['suspension']) * 0.2",
    "mech_decay_structure":               "…:612 = len(component_advisories['structure']) * 0.2",
    "mech_decay_steering":                "…:613 = len(component_advisories['steering']) * 0.2",
    "mech_decay_index":                   "…:620 = max(mech_decay_*)",
    "mech_decay_index_normalized":        "…:621+ age-normalisation of mech_decay_index",
    "mech_risk_driver":                   "…:641-649 argmax over mech_decay_* values",
    "text_corrosion_index":               "…:584 text_signals_total, incremented ONLY inside the ADVISORY branch (:381-393)",
    "text_wear_index":                    "…:584 idem",
    "text_leak_index":                    "…:584 idem",
    "text_damage_index":                  "…:584 idem",
    "text_corrosion_index_log":           "…:585 log1p of the above",
    "text_wear_index_log":                "…:585 idem",
    "text_leak_index_log":                "…:585 idem",
    "text_damage_index_log":              "…:585 idem",
    "has_corrosion_history":              "…:586 = text count > 0",
    "has_wear_history":                   "…:586 idem",
    "has_leak_history":                   "…:586 idem",
    "has_damage_history":                 "…:586 idem",
    "mechanism_count":                    "…:590-591 = len(mechanisms with text_signals_total>0)",
    "dominant_mechanism":                 "…:595-602 argmax over text_signals_total",
    "max_severity_score":                 "…:605 = 1 if len(all_advisories) > 0 (advisory-gated)",
}
tier2 = [n for n in names if n in TIER2]
missing = set(TIER2) - set(names)
assert not missing, f"TIER2 names absent from FEATURE_NAMES: {missing}"
assert not (set(tier1) & set(tier2)), "tier1/tier2 overlap"

# ---- Non-B0 blocks inside the 241 substrate --------------------------------
TIER1_OTHER = {
    "b3_n_advisory_items": "blocks.py:308 — prior advisory items, disposition A. Purely advisory.",
    "b4_n_adv_to_fail_transitions": "blocks.py:313 — advisory→fail transitions.",
    "b4_adv_to_fail_categories": "blocks.py:314 — categories with ≥1 advisory→fail transition.",
    "b4_days_since_adv_to_fail": "blocks.py:315 — days since most recent advisory→fail transition.",
}

# ---- Tier 3: RESIDUAL — partly advisory, deliberately NOT removed -----------
TIER3_NOTE = (
    "B2's 50 per-category columns (b2_{cat}_n_days/_days_since/_max_run/_persistence, "
    "b2_breadth_categories, b2_last_day_n_categories, b2_n_items_total) are computed over "
    "ANY DISPOSITION (blocks.py:262-272), so they carry advisory information mixed with "
    "minor/major/dangerous and cannot be cleanly attributed. L0m does NOT remove them. "
    "This is the principal reason (L0 − L0m) ≈ 0 CANNOT be read as 'AutoSafe uses no "
    "advisory information'.")

out = {
    "artifact": "PERSIST L0m membership — the advisory columns removed from the 241-col baseline",
    "frozen_for": "PREREG_PERSIST_2026_08_16.md §10.5",
    "generated": "2026-08-16",
    "reproducibility": ("Tier 1 is regenerated mechanically by this script's regex over the live "
                        "FEATURE_NAMES. Tier 2 is a declared list; every entry carries a file:line "
                        "citation and the script asserts each name exists in FEATURE_NAMES."),
    "b0_source_module": str(FE),
    "b0_n_features": len(names),
    "tier1_name_matched": {"regex": TIER1_RE.pattern, "n": len(tier1), "columns": tier1},
    "tier2_code_verified_derived": {"n": len(tier2),
                                    "columns": {n: TIER2[n] for n in tier2}},
    "tier1_other_blocks": {"n": len(TIER1_OTHER), "columns": TIER1_OTHER},
    "L0m_removes": {
        "n_b0": len(tier1) + len(tier2),
        "n_other_blocks": len(TIER1_OTHER),
        "n_total": len(tier1) + len(tier2) + len(TIER1_OTHER),
        "baseline_n": 241,
        "L0m_n": 241 - (len(tier1) + len(tier2) + len(TIER1_OTHER)),
    },
    "tier3_residual_NOT_removed": {"note": TIER3_NOTE, "n_approx": 50, "block": "B2"},
    "interpretive_constraint": (
        "L0m is a DECLARED-MEMBERSHIP ablation, not a lineage purge. Retained features may still "
        "encode advisory-derived or highly correlated information (tier 3). Therefore "
        "(L0 − L0m) ≈ 0 means 'the removed block carries no unique marginal signal conditional on "
        "the remaining features' — NOT 'AutoSafe does not use advisory information'. The stronger "
        "claim requires a separate lineage-purged ablation and is OUT OF SCOPE for PERSIST-1 "
        "unless separately preregistered."),
    "audit_correction": (
        "out/SERVE_VIEW_AUDIT.md and PREREG_ADVSTRUCT §2.2 record '≥23 B0 advisory columns'. "
        f"Code inspection finds {len(tier1) + len(tier2)} of {len(names)} B0 columns are "
        "advisory-derived — the audit counted only name-matched ones and missed the "
        "text_*/mech_decay_*/negligence families, all of which are advisory-gated at source."),
}

dest = PROG / "out/PERSIST_B0_ADVISORY_COLUMNS.json"
dest.write_text(json.dumps(out, indent=1))

print(f"B0 FEATURE_NAMES            : {len(names)}")
print(f"tier 1 (name-matched)       : {len(tier1)}")
print(f"tier 2 (code-verified)      : {len(tier2)}")
print(f"  -> B0 advisory-derived    : {len(tier1)+len(tier2)} of {len(names)} "
      f"({(len(tier1)+len(tier2))/len(names):.0%})")
print(f"other blocks (B3/B4)        : {len(TIER1_OTHER)}")
print(f"L0m removes                 : {out['L0m_removes']['n_total']} -> L0m = "
      f"{out['L0m_removes']['L0m_n']} columns (from 241)")
print(f"tier 3 residual NOT removed : ~50 (B2, any-disposition)")
print(f"\nwrote {dest}")
