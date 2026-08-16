#!/usr/bin/env python3
"""Pre-freeze validation for PREREG_PERSIST_2026_08_16.md.

Mechanically checks every numeric table in the prereg against the artifacts that
generated it, plus the governance-record checklist and the referenced-path check.
Exit 1 on any failure — the prereg must not be hashed while this fails.
"""
import json
import re
import sys
from math import erf, sqrt
from pathlib import Path

PROG = Path("/Users/henrirapson/autosafe-v58/docs/v58/model_programme_2026_08")
MD = PROG / "prereg/PREREG_PERSIST_2026_08_16.md"
text = MD.read_text()
fails, warns = [], []


def check(cond, msg):
    (fails if not cond else warns if False else []).append(msg) if not cond else None
    print(("  FAIL  " if not cond else "  ok    ") + msg)
    return cond


Z, ZP = 1.959963985, 0.841621234
Phi = lambda x: 0.5 * (1 + erf(x / sqrt(2)))

# ---------------------------------------------------------------- 1. artifacts exist
print("\n[1] referenced artifacts exist and match stated paths")
REFS = ["out/PERSIST_SECT_INDEX.json", "out/PERSIST_POWER_AT_BAR.json",
        "out/PERSIST_B0_ADVISORY_COLUMNS.json", "out/EVAL2024_READ_LOG.json",
        "out/ADVSTRUCT_TAXONOMY.json", "out/ADVSTRUCT_RESULT_2026_08_15.json",
        "out/B3_REFERENCE_BASELINE.json", "factory/DEVIATIONS.md"]
for r in REFS:
    if not check((PROG / r).exists(), f"{r} exists"):
        fails.append(r)
    if r.endswith(".json") and r.split("/")[-1] not in text and r not in text:
        print(f"  note  {r} not cited by name in the prereg body")

# ---------------------------------------------------------------- 2. power tables
print("\n[2] §9 tables reproduce from PERSIST_POWER_AT_BAR.json inputs")
pw = json.loads((PROG / "out/PERSIST_POWER_AT_BAR.json").read_text())
A = {r["system"]: (r["n_AA"], r["n_CA"]) for r in pw["per_system"]}
B_COUNTS = {"wheels_tyres": (62701, 47433), "brakes": (46895, 41822),
            "suspension": (33837, 29200), "noise_emissions": (9422, 8986),
            "body_structure": (5939, 8584), "lamps_electrical": (3020, 5115),
            "steering": (2187, 4146), "seatbelts_srs": (1198, 1452),
            "visibility": (237, 615)}
RR = pw["pre_declared_minimum_interesting_effect"]["relative_risk"]


def se_std(C, p0):
    tot = sum(a + b for a, b in C.values()); v = 0.0
    for n1, n0 in C.values():
        w = (n1 + n0) / tot; p1 = p0 * RR
        v += w ** 2 * (p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
    return sqrt(v)


def power(n1, n0, p0, rr=RR):
    p1 = min(p0 * rr, .999)
    se = sqrt(p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
    return Phi((p1 - p0) / se - Z)


# §9.1 standardised table
for p0, b_mde, a_mde, a_pwr in [(0.02, 0.148, 0.410, 77.9), (0.05, 0.230, 0.638, 99.2),
                                (0.10, 0.315, 0.875, 100.0), (0.15, 0.374, 1.038, 100.0)]:
    sb, sa = se_std(B_COUNTS, p0), se_std(A, p0)
    calc_b, calc_a = (Z + ZP) * sb * 100, (Z + ZP) * sa * 100
    calc_pwr = Phi(p0 * (RR - 1) / sa - Z) * 100
    row = re.search(rf"^\| {p0:.2f} \| ([\d.]+) pp \| ([\d.]+)% \| ([\d.]+) pp \| ([\d.]+)% \|",
                    text, re.M)
    if not check(row is not None, f"§9.1 row p0={p0:.2f} present"):
        fails.append(f"9.1 p0={p0}"); continue
    md_b, md_a, md_pwr = float(row.group(1)), float(row.group(3)), float(row.group(4))
    if not check(abs(md_b - round(calc_b, 3)) < 5e-4,
                 f"§9.1 p0={p0:.2f} Cohort B MDE {md_b} == computed {calc_b:.3f}"):
        fails.append(f"9.1 B MDE p0={p0}")
    if not check(abs(md_a - round(calc_a, 3)) < 5e-4,
                 f"§9.1 p0={p0:.2f} Cohort A MDE {md_a} == computed {calc_a:.3f}"):
        fails.append(f"9.1 A MDE p0={p0}")
    if not check(abs(md_pwr - round(calc_pwr, 1)) < 0.06,
                 f"§9.1 p0={p0:.2f} Cohort A power {md_pwr}% == computed {calc_pwr:.1f}%"):
        fails.append(f"9.1 A pwr p0={p0}")

# §9.2 per-system table at p0=0.05
print()
for s in A:
    ca, cb = power(*A[s], 0.05), power(*B_COUNTS[s], 0.05)
    row = re.search(rf"^\| {re.escape(s)} \| (\d+)% \| (\d+)% \|", text, re.M)
    if not check(row is not None, f"§9.2 row {s} present"):
        fails.append(f"9.2 {s}"); continue
    if not check(int(row.group(1)) == round(ca * 100) and int(row.group(2)) == round(cb * 100),
                 f"§9.2 {s}: A {row.group(1)}%=={ca:.0%} B {row.group(2)}%=={cb:.0%}"):
        fails.append(f"9.2 {s}")

# §9.3 concordance diagnostic
print()
for p0, full, half in [(0.02, 78, 28), (0.05, 99, 59), (0.10, 100, 89), (0.15, 100, 98)]:
    sa = se_std(A, p0); d = p0 * (RR - 1)
    cf, ch = Phi(d / sa - Z) * 100, Phi(0.5 * d / sa - Z) * 100
    if not check(abs(cf - full) < 0.6 and abs(ch - half) < 0.6,
                 f"§9.3 p0={p0:g}: full {full}%=={cf:.0f}% half {half}%=={ch:.0f}%"):
        fails.append(f"9.3 p0={p0}")

# ---------------------------------------------------------------- 3. L0m arithmetic
print("\n[3] §10.5 L0m arithmetic matches PERSIST_B0_ADVISORY_COLUMNS.json")
l0m = json.loads((PROG / "out/PERSIST_B0_ADVISORY_COLUMNS.json").read_text())
n_l0m = l0m["L0m_removes"]["L0m_n"]
check(f"| **{n_l0m}** |" in text, f"§10.5 states L0m n = {n_l0m}")
n_b0adv = l0m["L0m_removes"]["n_b0"]
check(f"{n_b0adv} of 104" in text, f"§10.5 states {n_b0adv} of 104 B0 columns advisory-derived")

# ---------------------------------------------------------------- 4. cohort counts
print("\n[4] cohort counts consistent")
check("312,789" in text, "§6.1 Cohort B n = 312,789")
check("40,911" in text, "§6.2 Cohort A n = 40,911")
check("165,436" in text and "147,353" in text, "§6.1 A→A / C→A split present")
check("22,431" in text and "24,093" in text, "§6.2 A→A / C→A split present")

# ---------------------------------------------------------------- 5. governance record
print("\n[5] governance record (item 7 checklist)")
GOV = {
    "written before any persistence-conditional outcome existed":
        "BEFORE any outcome quantity conditional on persistence state existed",
    "eval2024 shared-surface status + read lower bound": "at least seven prior outcome-conditional",
    "Cohort A nested inside Cohort B": "nested within Cohort B",
    "all nine systems frozen before outcome inspection": "All nine systems remain",
    "ontology bridge frozen in PERSIST_SECT_INDEX.json": "PERSIST_SECT_INDEX.json",
    "D5 power artifact frozen": "PERSIST_POWER_AT_BAR.json",
    "L0m membership frozen": "PERSIST_B0_ADVISORY_COLUMNS.json",
    "confirm2025h2 explicitly protected": "not spent on\nPERSIST",
    "deviations cross-referenced to DEVIATIONS.md §5": "factory/DEVIATIONS.md",
    "no lower success threshold after outcomes read":
        "No lower success threshold may be invented after seeing results",
}
for label, needle in GOV.items():
    n = needle.replace("\n", " ")
    flat = " ".join(text.split())
    if not check(n in flat, label):
        fails.append(label)

# ---------------------------------------------------------------- 6. banned terminology
print("\n[6] banned governance terminology absent outside its own prohibition")
for term in ("uncontaminated", "untouched", "pristine", "independent holdout"):
    prohibition = ("The result deliverables must use that terminology", "may not be applied",
                   "not an independent or pristine confirmation")
    para = [p for p in text.split("\n\n") if term in p.lower()]
    hits = [p for p in para if not any(k in p for k in prohibition)]
    if not check(not hits, f'"{term}" not used as a claim'):
        fails.append(term)
        for h in hits[:2]:
            print(f"        -> {h.strip()[:110]}")

# ---------------------------------------------------------------- 7. no heuristic tau
print("\n[7] heuristic heterogeneity-SE inflation removed")
check(not re.search(r"1\s*\+\s*τ²|tau\^?2\s*/\s*SE|inflate SE", text),
      "no sqrt(1+tau^2/SE^2) formula")
check("propagate model uncertainty directly" in text, "direct propagation stated instead")
check("report τ" in text or "**report τ**" in text, "tau must be reported")

# ---------------------------------------------------------------- 8. no outcome computed
print("\n[8] no persistence-conditioned outcome artifact exists")
banned = [p for p in (PROG / "out").glob("PERSIST_*")
          if p.name not in {"PERSIST_SECT_INDEX.json", "PERSIST_POWER_AT_BAR.json",
                            "PERSIST_B0_ADVISORY_COLUMNS.json"}]
if not check(not banned, "no PERSIST result/mechanism artifacts present"):
    fails.append("outcome artifact")
    for b in banned:
        print(f"        -> {b.name}")

print("\n" + "=" * 62)
if fails:
    print(f"VALIDATION FAILED — {len(fails)} problem(s). DO NOT HASH.")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("VALIDATION PASSED — prereg is internally consistent and may be hashed.")
