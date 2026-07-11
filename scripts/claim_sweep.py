#!/usr/bin/env python3
"""Claim sweep: fail if public copy claims capabilities the user path doesn't deliver.

The deployed user path today is the population-average lookup (/api/risk).
Until a per-vehicle model is actually wired into the UI (v58 flip, plan task
3.7), public copy must not claim AI/ML per-vehicle prediction. When the flip
ships, move the then-true patterns into ALLOWED_AFTER_V58 and delete them
from BANNED.

Run: python scripts/claim_sweep.py   (exit 0 = clean, 1 = violations)
Wired into CI as a hard check.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Public-copy surfaces (source files, not build outputs; static html wrappers
# are deploy-served directly so they count too).
SURFACES = [
    "index.html",
    "App.tsx",
    "components/*.tsx",
    "seo_pages.py",
    "static/*.html",
    "templates/*.html",
    "email_templates.py",
]

# Claim patterns that are FALSE while the user path is the lookup table.
BANNED = [
    (r"\bAI\s+analysis\b", "claims AI analysis"),
    (r"\bmachine[- ]learning\b", "claims machine learning in the user path"),
    (r"\bML[- ]powered\b", "claims ML-powered"),
    (r"\bCatBoost\b", "names a model the user path does not invoke"),
    (r"\bartificial\s+intelligence\b", "claims AI"),
    (r"\bour\s+AI\b", "claims 'our AI'"),
    (r"\bneural\b", "claims neural methods"),
    (r"personali[sz]ed\s+(AI|ML|machine[- ]learning)", "claims personalised AI/ML"),
    # Method claims that are false while the user path is the lookup table
    (r"not just a population average", "denies being a population average (it is one)"),
    (r"personali[sz]ed\s+(prediction|risk assessment)", "claims a personalised prediction"),
    (r"specific to your vehicle", "claims vehicle-specific output"),
    (r"Platt scaling|expected calibration error|\bECE\b", "claims calibration machinery the path lacks"),
    (r"104\s+(engineered\s+)?features", "describes the model's features as the served method"),
    (r"8[05]%\+?\s*accur", "uncited accuracy claim"),
    # Broader product-truth phrases found during the RC1 release review.
    # These describe per-vehicle modelling or evidence sources that the
    # report path does not have, even though they avoid the literal words
    # caught by the older AI/ML-only patterns above.
    (r"industry[- ]leading\s+predictive\s+model", "claims an unverified industry-leading model"),
    (r"manufacturing\s+logs", "claims a source the report path does not use"),
    (r"unique\s+DNA", "claims vehicle-specific evidence the report path does not have"),
    (r"\bAI[- ]?(trained|prediction|tool|powered)\b", "claims an AI method in the user path"),
    (
        r"personali[sz]ed\s+(MOT\s+)?(failure\s+)?(risk\s+)?(prediction|probability|assessment|breakdown)",
        "claims a personalised failure prediction",
    ),
    (r"tailored\s+specifically\s+to\s+your\s+(vehicle|car)", "claims a vehicle-specific prediction"),
    (r"based\s+on\s+your\s+exact\s+(vehicle|car)", "claims exact-vehicle evidence"),
    (r"your\s+(vehicle|car)'s\s+individual\s+MOT\s+history", "claims individual-history modelling"),
    (
        r"specific\s+components\s+most\s+likely\s+to\s+cause\s+a\s+failure\s+on\s+your\s+car",
        "presents population component rates as a diagnosis",
    ),
    (r"covering\s+(seven|7)\s+key\s+failure\s+areas", "promises components when evidence may be unavailable"),
    (r"specific\s+(vehicle|car)(?:'s)?\s+(risk|common\s+failure\s+points)", "claims vehicle-specific risk evidence"),
    (r"exact\s+vehicle(?:'s)?\s+MOT\s+risk", "claims an exact-vehicle risk"),
]

# Lines that are allowed to match (documentation of the ban itself, comments
# explaining the gate, or factual statements ABOUT the data rather than the
# method). Keep this list short and reviewed.
ALLOWLIST = [
    r"claim_sweep",          # self-references
    r"do not claim",         # instructions about the rule
    r"per REMEDIATION_PLAN", # provenance comments
]

def main() -> int:
    violations = []
    for pattern in SURFACES:
        for f in sorted(ROOT.glob(pattern)):
            # Vite generates static/index.html from root index.html. A local
            # build may leave that ignored output behind; scanning it would
            # double-count stale copy that cannot exist in a fresh checkout.
            relative = f.relative_to(ROOT)
            if relative.as_posix() == "static/index.html" or "assets" in f.parts or not f.is_file():
                continue
            try:
                text = f.read_text(errors="ignore")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if any(re.search(a, line, re.I) for a in ALLOWLIST):
                    continue
                for rx, why in BANNED:
                    if re.search(rx, line, re.I):
                        violations.append((relative, i, why, line.strip()[:110]))
    if violations:
        print(f"CLAIM SWEEP: {len(violations)} violation(s)\n")
        for path, line, why, snippet in violations:
            print(f"  {path}:{line}  [{why}]\n      {snippet}")
        return 1
    print("CLAIM SWEEP: clean — no unsupported capability claims in public copy")
    return 0

if __name__ == "__main__":
    sys.exit(main())
