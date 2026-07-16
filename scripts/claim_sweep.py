#!/usr/bin/env python3
"""Fail when public copy overstates the v2 report contract.

The release-candidate path serves recorded vehicle/MOT details plus a disclosed
comparable-vehicle failure rate. It is not a vehicle inspection, diagnosis, or
forecast of the next MOT result. Public copy must preserve that distinction.

Run: python scripts/claim_sweep.py   (exit 0 = clean, 1 = violations)
Wired into CI as a hard check.
"""
import csv
import gzip
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from report_contract import DATASET_TOTAL_FAILURES, DATASET_TOTAL_TESTS  # noqa: E402

# Public-copy surfaces (source files, not build outputs; static html wrappers
# are deploy-served directly so they count too).
SURFACES = [
    "index.html",
    "metadata.json",
    "App.tsx",
    "agents/**/*.py",
    "components/**/*.tsx",
    "data_stories/**/*.py",
    "seo_pages.py",
    "static/**/*.html",
    "static/**/*.js",
    "templates/**/*.html",
    "email_templates.py",
    "utils/recommendation.ts",
]

# Claims that are false for the v2 report path.
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
    # Score-semantics and remaining release-review leaks. The v2 path
    # serves a disclosed comparable-vehicle failure rate; its arithmetic
    # complement is not a second reliability/pass model.
    (r"\bMOT\s+(prediction|predictor)\b", "renames a comparison rate as an MOT prediction"),
    (r"\bfailure\s+risk\s+predictor\b", "markets the comparison as a failure-risk predictor"),
    (r"\bfree\s+prediction\b", "markets the comparison as a prediction"),
    (r"\bpredict\s+your\s+next\s+MOT", "claims a future individual outcome"),
    (r"\bpredictive\s+health\s+check\b", "claims a vehicle-specific health assessment"),
    (r"\b(pass\s+probability|reliability\s+score|AutoSafe\s+Index)\b", "renames the same rate complement"),
    (r"\brisk\s+score\b", "renames a disclosed historical failure rate as a score"),
    (r"\bwe\s+predict\b", "claims a vehicle-specific prediction"),
    (r"\brisk\s+prediction(s)?\b", "calls the comparison output a prediction"),
    (r"\bprediction\s+(algorithm|model|system|tool)s?\b", "claims a predictive model/system"),
    (r"\bautomated\s+system\s+to\s+predict\b", "claims automated next-MOT prediction"),
    (
        r"(?:AutoSafe|our\s+(?:system|tool|checker|report)|the\s+tool).{0,160}\b(full|complete)\s+MOT\s+(test\s+)?history\b",
        "promises a full history the report does not display",
    ),
    (
        r"\b(full|complete)\s+MOT\s+(test\s+)?history\b.{0,160}(?:AutoSafe|our\s+(?:system|tool|checker|report)|the\s+tool)",
        "promises a full history the report does not display",
    ),
    (r"\bAutoSafe\s+automatically\s+analyses\b", "claims individual-history analysis the report does not perform"),
    (r"\bflags\s+any\s+inconsistencies\s+in\s+the\s+mileage\s+record\b", "claims unsupported mileage anomaly detection"),
    (r"\byour\s+(?:vehicle|car)'s\s+specific\s+(?:risk|failure\s+risk)\b", "claims a vehicle-specific risk"),
    (r"\bpersonali[sz]ed\s+advice\b", "claims personalised advice from comparison data"),
    (r"\bidentify\s+your\s+(?:vehicle|car)'s\s+weak\s+points\b", "claims vehicle diagnosis from comparison data"),
    (r"\bfix\s+it\s+before\s+they\s+find\s+it\b", "implies the comparison diagnoses a defect"),
    (r"\blikely\s+to\s+have\b.{0,80}\bissues\b", "claims likely defects on an individual vehicle"),
    (r"\bavoid\b.{0,40}\bbills\b", "implies the comparison can prevent individual repair costs"),
    (r"\bpredicted\s+failure\s+risk\b", "presents a comparison rate as predicted"),
    (r"\bpredicted\s+repair\s+cost\b", "claims an individual repair-cost forecast"),
    (r"\bcomponents?\s+(?:are|is)\s+at\s+risk\b", "presents cohort categories as vehicle faults"),
    (r"\bestimate\s+your\s+(?:potential\s+)?repair\s+costs?\b", "claims an individual repair-cost estimate"),
    (r"\bfailure[- ]risk\s+(checker|estimate)s?\b", "markets the cohort comparison as a failure-risk estimate"),
    (r"\brisk\s+estimate(s|d)?\b", "calls the cohort comparison an estimate for the submitted vehicle"),
    (r"\bmost\s+likely\s+to\s+fail\b", "turns the highest recorded group rate into a future-outcome claim"),
    (r"\b(most|more)\s+(un)?reliable\b", "renames recorded group rates as a reliability judgment"),
    (r"\breliability\s+ranking(s)?\b", "renames recorded group-rate rankings as reliability"),
    (r"\breliable\s+choice\b", "turns a lower group rate into a recommendation"),
    (r"\b(specific\s+)?weak(ness(es)?|spots?)\b", "presents group component rates as vehicle/model weaknesses"),
    (r"\btop\s+risk\s+area\b", "presents a group component rate as a vehicle risk area"),
    (r"\bcomponent\s+risks?\b", "labels aggregate component involvement as component risk"),
    (r"\bcheck\s+your\s+(car|vehicle)'?s?\s+MOT\s+risk\b", "markets a group comparison as the submitted vehicle's risk"),
    (r"\bfull\s+risk\s+prediction\b", "presents a comparison report as a prediction"),
    (r"\bcommon\s+faults\s+for\s+your\b", "presents cohort component rates as this car's faults"),
    (r"\byour\s+(car|vehicle)\s+(looks\s+healthy|is\s+in\s+good\s+shape)\b", "infers condition from cohort data"),
    (r"\bno\s+(major\s+concerns\s+found|urgent\s+action\s+needed)\b", "infers condition from missing/cohort data"),
    (r"\bour\s+prediction\s+model\s+incorporates\b", "claims unserved regional/model inputs"),
    (r"\bpredict\s+your\s+future\s+MOT\s+outcome\b", "claims an individual future outcome"),
    (r"\[ADDRESS\]", "ships an unresolved legal-notice placeholder"),
    (r"\b0\.28\b", "uses the stale hard-coded UK reference rate instead of primary-data metadata"),
    (r"(?<![\d.])28\s*%", "uses the stale rounded UK reference rate instead of primary-data metadata"),
    (r"\b142000000\b", "uses the obsolete fabricated dataset-size fallback"),
    (r"\btop[- ]rated\s+MOT\s+garages\b", "claims an unsupported garage ranking"),
    (r"\bapproved\s+garages\s+in\s+your\s+area\b", "claims unsupported local approval/coverage"),
    (r"\baverage\s+MOT\s+failure\s+rate\s+in\b", "relabels the dataset reference as a local rate"),
    (r"\b(?:UK|national)\s+(?:avg|average)\b", "relabels the checked-in dataset reference as a national statistic"),
    (r"\bUK\s+MOT\s+tests\b", "claims geographic coverage not encoded by the checked-in artifact"),
    (r"\bUK\s+DVSA\b", "uses an imprecise source label; DVSA coverage should not be relabelled UK-wide"),
    # R1-T6 wording-review additions: counts are MOT tests, never vehicles,
    # and a recorded MOT odometer reading is never presented as current.
    (r"\bvehicles\s+analysed\b", "mislabels the recorded MOT test count as vehicles analysed"),
    (r"\b0\s+vehicles\b", "renders an unknown/zero count as a fabricated '0 vehicles' figure"),
    (r"\bsimilar\s+vehicles\s+tested\b", "mislabels the recorded MOT test count as vehicles tested"),
    (r"\bcurrent\s+mileage\b", "calls the last recorded MOT reading 'current mileage', overstating its freshness"),
]

# Lines that are allowed to match (documentation of the ban itself, comments
# explaining the gate, or factual statements ABOUT the data rather than the
# method). Keep this list short and reviewed.
ALLOWLIST = [
    r"claim_sweep",          # self-references
    r"do not claim",         # instructions about the rule
    r"does not (calculate|report|provide).{0,80}(pass probability|reliability score|prediction)",
    r"per REMEDIATION_PLAN", # provenance comments
]

# Owner-approved brand copy exception (2026-07-16). Keep this path- and
# line-specific so the slogan can appear only as the homepage headline;
# all diagnosis/prediction claims remain banned everywhere else.
OWNER_APPROVED_EXCEPTIONS = {
    "App.tsx": [r"^\s*Fix it before they find it\.\s*$"],
}

def main() -> int:
    violations = []
    dataset_path = ROOT / "prod_data_clean.csv.gz"
    try:
        with gzip.open(dataset_path, "rt", newline="") as handle:
            rows = csv.DictReader(handle)
            actual_tests = 0
            actual_failures = 0
            for row in rows:
                actual_tests += int(row["Total_Tests"])
                actual_failures += int(row["Total_Failures"])
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(f"CLAIM SWEEP: could not verify primary dataset metadata ({type(exc).__name__})")
        return 1

    if (actual_tests, actual_failures) != (DATASET_TOTAL_TESTS, DATASET_TOTAL_FAILURES):
        print(
            "CLAIM SWEEP: report-contract dataset metadata does not match "
            f"prod_data_clean.csv.gz: contract=({DATASET_TOTAL_TESTS}, {DATASET_TOTAL_FAILURES}) "
            f"actual=({actual_tests}, {actual_failures})"
        )
        return 1

    public_millions_floor = DATASET_TOTAL_TESTS // 1_000_000
    for pattern in SURFACES:
        for f in sorted(ROOT.glob(pattern)):
            # Vite generates static/index.html from root index.html. A local
            # build may leave that ignored output behind; scanning it would
            # double-count stale copy that cannot exist in a fresh checkout.
            relative = f.relative_to(ROOT)
            if (
                relative.as_posix() == "static/index.html"
                or "assets" in f.parts
                or ".test." in f.name
                or not f.is_file()
            ):
                continue
            try:
                text = f.read_text(errors="ignore")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if any(
                    re.search(pattern, line, re.I)
                    for pattern in OWNER_APPROVED_EXCEPTIONS.get(relative.as_posix(), [])
                ):
                    continue
                if any(re.search(a, line, re.I) for a in ALLOWLIST):
                    continue
                for match in re.finditer(r"\b(\d{3})\s*(?:million|M\+?)\b", line, re.I):
                    claimed_millions = int(match.group(1))
                    if claimed_millions != public_millions_floor:
                        violations.append((
                            relative,
                            i,
                            f"dataset-size claim {claimed_millions}M disagrees with primary artifact floor {public_millions_floor}M",
                            line.strip()[:110],
                        ))
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
