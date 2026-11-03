"""Receipt verification: decides whether a QualificationReceipt's bound evidence supports 'qualified'. Fails closed: the first failed check raises QualError.

verify_receipt() never trusts a boolean it can recompute or a hash it can check. What it CANNOT establish is listed in REPORT.md ('not verified'): authenticity of
the owner approvals (no signature, only a pin), truth of declared gate results (2023 diagnostic, usefulness, live qualification), real independence of the
recomputation, and that the registry lines are genuine unless issuance-level verification is given the registry file.
"""
from __future__ import annotations
import json
from pathlib import Path
from . import gate_py_v1
from .common import (QualError, canonical_hash, inside, loads_strict, parse_json_bytes, parse_utc, read_bytes_checked, read_json_file, registry_digest)
from .minischema import validate, validate_def

SCHEMA_PATH = Path(__file__).resolve().parent / "QUALIFIED_BUNDLE_V2.schema.json"
_SCHEMA = None
G6_MIN_BOOKINGS = 500       # Henri's G6 ruling, DECISIONS.md ("Henri's rulings on F1")
G6_PSI_MAX = 0.10
PARTITION_FOR_USEFULNESS = {"role5": "role5", "y2023": "y2023", "y2024": "y2024"}


def schema():
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = read_json_file(SCHEMA_PATH, what="schema")
    return _SCHEMA


def check_struct(doc, defname):
    errs = validate_def(doc, schema(), defname)
    if errs:
        raise QualError("schema_invalid", f"{defname}: {errs[0]}")


def check_sidecar(sc):
    """Validate the sidecar against the schema root (everything except $defs)."""
    s = schema()
    errs = validate(sc, {k: x for k, x in s.items() if k != "$defs"}, s)
    if errs:
        raise QualError("schema_invalid", "sidecar: " + errs[0])


class Verified:
    def __init__(self):
        self.checks, self.receipt_sha256, self.display, self.times = [], None, None, {}

    def ok(self, name):
        self.checks.append(name)


def _load_bytes(root, receipt, role):
    """The evidence file's bytes, read once, equal to the hash the receipt binds."""
    ref = receipt["evidence"][role]
    return read_bytes_checked(inside(root, ref["path"]), ref["sha256"], what=role)


def _load_doc(root, receipt, role, defname):
    doc = parse_json_bytes(_load_bytes(root, receipt, role), what=role)
    check_struct(doc, defname)
    return doc


def _same_subject(doc, identity, role):
    if doc["subject"]["model_identity_sha256"] != identity:
        raise QualError("subject_mismatch", role)


def _ts(doc, key, what):
    return parse_utc(doc[key], what)


def _read_entry(root, receipt, rules, ver, registry_path):
    entry = _load_doc(root, receipt, "read_2024_entry_receipt", "entryReceiptMinimal")
    req = rules["read_2024_entry_requirements"]
    if not req or any(k not in entry or entry[k] != want or type(entry[k]) is not type(want) for k, want in req.items()):
        raise QualError("read_entry_requirements", "entry receipt lacks the owner-registered requirements (non-empty, exact match)")
    if "content_sha256" in entry:                     # step-6 style authority receipt: self-declared content hash, verified here
        if entry["content_sha256"] != canonical_hash({k: x for k, x in entry.items() if k != "content_sha256"}):
            raise QualError("read_entry_digest", "entry receipt content_sha256 does not match its content")
        digest = entry["content_sha256"]
    else:
        digest = registry_digest(entry)
    try:
        lines = [l for l in _load_bytes(root, receipt, "read_2024_registry_records").decode("utf-8").splitlines() if l.strip()]
    except UnicodeDecodeError:
        raise QualError("read_entry_records", "not utf-8")
    try:
        recs = [loads_strict(l) for l in lines]
    except ValueError:
        raise QualError("read_entry_records", "unparsable registry line")
    for r in recs:                                    # a failure/revocation anywhere in the file wins over a count complaint
        if isinstance(r, dict) and r.get("event") in ("READ_FAIL", "AUTHORITY_REVOKED"):
            raise QualError("read_entry_failed_or_revoked", str(r.get("event")))
    if len(recs) != 2:
        raise QualError("read_entry_records", "exactly one READ_START and one READ_END line required")
    for r in recs:
        check_struct(r, "registryRecord")
    events = sorted(r["event"] for r in recs)
    if events != ["READ_END", "READ_START"]:
        raise QualError("read_entry_records", "need one READ_START and one READ_END")
    start = next(r for r in recs if r["event"] == "READ_START")
    end = next(r for r in recs if r["event"] == "READ_END")
    for r in (start, end):
        if r["entry_id"] != entry["entry_id"]:
            raise QualError("read_entry_records", "entry_id differs from the entry receipt")
        if r["entry_receipt_content_sha256"] != digest:
            raise QualError("read_entry_digest", "registry record does not bind this entry receipt's content digest")
    if "proof_sha256" not in end:
        raise QualError("read_entry_records", "READ_END has no proof_sha256")
    t_start, t_end = parse_utc(start["timestamp_utc"], "READ_START"), parse_utc(end["timestamp_utc"], "READ_END")
    if not t_start < t_end:
        raise QualError("read_entry_order", "READ_END must follow READ_START")
    if registry_path is not None:                                    # issuance-level: the lines must be the registry's own, and nothing closes/revokes them
        reg = Path(registry_path)
        if not reg.is_file():
            raise QualError("registry_unreadable", "")
        seen = []
        for line in reg.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = loads_strict(line)
            except ValueError:
                raise QualError("registry_unreadable", "line not valid JSON")
            blob = json.dumps(r, sort_keys=True)
            if isinstance(r, dict) and (r.get("entry_id") == entry["entry_id"] or entry["entry_id"] in blob or digest in blob):
                seen.append(r)
        if sorted(json.dumps(r, sort_keys=True) for r in seen) != sorted(json.dumps(r, sort_keys=True) for r in recs):
            raise QualError("registry_mismatch", "registry holds other or different records for this entry (e.g. READ_FAIL, AUTHORITY_REVOKED, a duplicate START)")
        ver.ok("registry_lines_present_verbatim_and_unrevoked")
    ver.times.update(read_start=t_start, read_end=t_end)
    ver.ok("read_2024_entry_bound")
    return start, end


def verify_receipt(root, receipt, *, base, allow_synthetic=False, pin=None, allow_unpinned=False, registry_path=None):
    """root: directory holding qualification/<files>. base: dict from base_v1.read_base_v1 for the bundle being served. Returns Verified; raises QualError."""
    ver = Verified()
    check_struct(receipt, "receipt")
    ver.receipt_sha256 = canonical_hash(receipt)
    if pin is not None:
        if ver.receipt_sha256 != pin:
            raise QualError("pin_mismatch", "receipt content hash is not the pinned value")
        ver.ok("pin_matches")
    elif not allow_unpinned:
        raise QualError("pin_missing", "a qualified bundle is accepted only against an out-of-band pinned receipt hash")
    if receipt["synthetic_fixture"] is not base["manifest"]["synthetic_fixture"]:
        raise QualError("synthetic_mismatch", "receipt vs base manifest")
    if receipt["synthetic_fixture"] and not allow_synthetic:
        raise QualError("synthetic_refused", "synthetic fixture receipts are never accepted as qualification")
    m = base["manifest"]
    sub = receipt["subject"]
    for k, want in (("arm", m["arm"]), ("model_version", m["model_version"]), ("integrated_freeze_sha256", m["integrated_freeze_sha256"]),
                    ("model_identity_sha256", base["model_identity_sha256"])):
        if sub[k] != want:
            raise QualError("subject_mismatch", "receipt.subject." + k)
    identity = sub["model_identity_sha256"]
    ver.ok("subject_binds_served_model")
    paths = [v["path"] for v in receipt["evidence"].values()]
    if len(set(paths)) != len(paths):
        raise QualError("evidence_duplicate_path", "two roles point at one file")

    rules_doc = _load_doc(root, receipt, "rules_approval", "rulesApproval")
    dispa = _load_doc(root, receipt, "display_approval", "displayApproval")
    for d, name in ((rules_doc, "rules_approval"), (dispa, "display_approval")):
        if d["synthetic_fixture"] is not receipt["synthetic_fixture"]:
            raise QualError("synthetic_mismatch", name)
    _same_subject(dispa, identity, "display_approval")
    if rules_doc["subject"]["model_identity_sha256"] != identity or rules_doc["subject"]["integrated_freeze_sha256"] != sub["integrated_freeze_sha256"]:
        raise QualError("subject_mismatch", "rules_approval")
    if rules_doc["protocol"]["amendment_sha256"] != receipt["protocol"]["amendment_sha256"]:
        raise QualError("protocol_mismatch", "amendment hash differs between receipt and rules approval")
    rules = rules_doc["rules"]
    ver.ok("approvals_bind_model_and_amendment")

    start, end = _read_entry(root, receipt, rules, ver, registry_path)
    t_start, t_end = ver.times["read_start"], ver.times["read_end"]
    t_issued = parse_utc(receipt["issued_at_utc"], "issued_at_utc")
    t_rules = parse_utc(rules_doc["approved_at_utc"], "rules approved_at")
    if not t_rules < t_start:
        raise QualError("rules_not_preregistered", "the owner's rules approval must predate the registered 2024 READ_START")
    ver.ok("rules_preregistered_before_read_start")
    latest = [t_end, t_rules]

    # 2023 pseudo-future diagnostic (declared)
    pf = _load_doc(root, receipt, "pseudo_future_2023", "declaredGate")
    _declared(pf, "pseudo_future_2023", "role5_y2023", rules["pseudo_future_2023"], identity, receipt)
    t = parse_utc(pf["produced_at_utc"], "pseudo_future produced_at")
    if not t < t_start:
        raise QualError("gate_order", "the 2023 pseudo-future diagnostic must be completed before the 2024 READ_START")
    latest.append(t)
    ver.ok("pseudo_future_2023_passed_before_read")

    # usefulness gate (declared; partition is the owner's decision)
    ug = _load_doc(root, receipt, "usefulness_gate", "declaredGate")
    ur = rules["usefulness_gate"]
    _declared(ug, "usefulness_gate", PARTITION_FOR_USEFULNESS[ur["partition"]], ur, identity, receipt)
    t = parse_utc(ug["produced_at_utc"], "usefulness produced_at")
    if (ur["partition"] == "y2024") != (t >= t_start):
        raise QualError("gate_order", "usefulness gate timing contradicts its registered partition (2024 partition is computed at/after READ_START, others before)")
    latest.append(t)
    ver.ok("usefulness_gate_passed_on_registered_partition")

    # 2024 historical assessment (recomputed from the gate.py report)
    prov = _load_doc(root, receipt, "assessment_2024_provenance", "assessmentProvenance")
    report = parse_json_bytes(_load_bytes(root, receipt, "assessment_2024_report"), what="gate report")
    a = rules["assessment_2024"]
    if prov["synthetic_fixture"] is not receipt["synthetic_fixture"]:
        raise QualError("synthetic_mismatch", "assessment provenance")
    _same_subject(prov, identity, "assessment_2024_provenance")
    if a["code_sha256"] != gate_py_v1.FROZEN_CODE_SHA256 or prov["code_sha256"] != a["code_sha256"]:
        raise QualError("assessment_evaluator_unknown", "only the frozen gate.py (FROZEN_MANIFEST sha256) has an evaluator here; the owner rules must name it and the report must be produced by it")
    if prov["report_sha256"] != receipt["evidence"]["assessment_2024_report"]["sha256"]:
        raise QualError("assessment_report_mismatch", "provenance names a different report")
    if prov["read_proof_sha256"] != end["proof_sha256"]:
        raise QualError("assessment_read_mismatch", "the assessment was not produced from the registered READ_END proof")
    t_prod = parse_utc(prov["produced_at_utc"], "assessment produced_at")
    if t_prod < t_start:
        raise QualError("gate_order", "assessment produced before READ_START")
    t_base = parse_utc(prov["base_rate"]["recorded_at_utc"], "base_rate recorded_at")
    if not t_base < t_start:
        raise QualError("base_rate_not_prior", "gate.py's base rate must be recorded before the 2024 read")
    if report.get("base_rate_constant") != prov["base_rate"]["value"]:
        raise QualError("base_rate_mismatch", "report base rate differs from the pre-recorded one")
    ev = gate_py_v1.evaluate(report)
    if any(ev["vacuous"].values()) and not a["allow_vacuous_gates"]:
        raise QualError("assessment_vacuous_gate", "a gate passed only because no subgroup/band reached its minimum n: " + ",".join(k for k, v in ev["vacuous"].items() if v))
    if not (ev["gates"]["delta_auc"] and ev["extra_gates"]["brier_below_base_rate"]):
        raise QualError("assessment_discrimination_failed", "delta AUC lower bound or Brier-vs-base-rate gate failed")
    latest.append(t_prod)
    ver.ok("assessment_2024_recomputed_from_report")

    rc = _load_doc(root, receipt, "independent_recomputation", "recomputation")
    if rc["synthetic_fixture"] is not receipt["synthetic_fixture"]:
        raise QualError("synthetic_mismatch", "recomputation")
    _same_subject(rc, identity, "independent_recomputation")
    if rc["primary_report_sha256"] != prov["report_sha256"] or rc["predictions_sha256"] != prov["predictions_sha256"]:
        raise QualError("recomputation_mismatch", "recomputation is of a different report/predictions")
    if rc["recomputer_id"] == prov["produced_by"] or rc["implementation_sha256"] == prov["code_sha256"]:
        raise QualError("recomputation_not_independent", "same agent id or same implementation hash as the author")
    if rc["agrees"] is not True or {k: rc["recomputed"][k] for k in ("gates", "extra_gates", "CALIBRATION_PASS", "MODEL_PASS")} != {k: ev[k] for k in ("gates", "extra_gates", "CALIBRATION_PASS", "MODEL_PASS")}:
        raise QualError("recomputation_disagrees", "independent recomputation differs from the values re-derived from the report")
    t_rc = parse_utc(rc["recomputed_at_utc"], "recomputed_at")
    if t_rc < t_start:
        raise QualError("gate_order", "recomputation before READ_START")
    latest.append(t_rc)
    ver.ok("independent_recomputation_agrees")

    # live qualification
    lq = _load_doc(root, receipt, "live_qualification", "liveQualification")
    if lq["synthetic_fixture"] is not receipt["synthetic_fixture"]:
        raise QualError("synthetic_mismatch", "live qualification")
    _same_subject(lq, identity, "live_qualification")
    comps = lq["components"]
    missing = [c for c in rules["live_required_components"] if c not in comps]
    if lq["passed"] is not True or missing or any(c["passed"] is not True for c in comps.values()):
        raise QualError("live_qualification_failed", "passed flag, a required component missing (%s), or a component failed" % ",".join(missing)[:80])
    t = parse_utc(lq["completed_at_utc"], "live completed_at")
    if not t < t_start:
        raise QualError("gate_order", "live qualification must be complete before the 2024 READ_START")
    latest.append(t)
    ver.ok("live_qualification_complete_before_read")

    # display policy
    disp = receipt["display"]
    if disp != dispa["display"]:
        raise QualError("display_mismatch", "receipt display differs from the owner's display approval")
    e0, e1 = disp["band_edges"]
    if not (0 < e0 < e1 <= 100 and e0 % disp["rounding_pp"] == 0 and e1 % disp["rounding_pp"] == 0):
        raise QualError("display_invalid", "band_edges must be increasing multiples of rounding_pp")
    t_da = parse_utc(dispa["approved_at_utc"], "display approved_at")
    latest.append(t_da)
    if not ev["CALIBRATION_PASS"]:
        if disp["display_state"] != "bands":
            raise QualError("percentage_requires_calibration", "calibration gates failed in the 2024 assessment: bands-only at most")
        if "band_evidence" not in receipt["evidence"]:
            raise QualError("band_evidence_required", "calibration failed: bands-only needs the band monotonicity evidence")
    if "band_evidence" in receipt["evidence"]:
        be = _load_doc(root, receipt, "band_evidence", "bandEvidence")
        _band_evidence(be, rules["band_rule"], identity, prov["report_sha256"], receipt)
        latest.append(parse_utc(be["produced_at_utc"], "band_evidence produced_at"))
        ver.ok("band_evidence_meets_owner_rule")
    if disp["display_state"] == "percentage":
        if "g6_shadow" not in receipt["evidence"]:
            raise QualError("g6_required", "percentage display needs the G6 shadow result")
    if "g6_shadow" in receipt["evidence"]:
        g6 = _load_doc(root, receipt, "g6_shadow", "g6Shadow")
        if g6["synthetic_fixture"] is not receipt["synthetic_fixture"]:
            raise QualError("synthetic_mismatch", "g6")
        _same_subject(g6, identity, "g6_shadow")
        t_g6 = parse_utc(g6["produced_at_utc"], "g6 produced_at")
        if not (g6["passed"] is True and g6["pooled_supported_bookings"] >= G6_MIN_BOOKINGS and g6["psi_max_inputs"] <= G6_PSI_MAX and g6["psi_score"] <= G6_PSI_MAX):
            raise QualError("g6_failed", "needs >=500 pooled supported bookings and PSI <= 0.10 on every input and the score")
        if not t_g6 > t_end:
            raise QualError("gate_order", "G6 shadow result must come after the 2024 assessment read")
        if disp["display_state"] == "percentage" and not t_da > t_g6:
            raise QualError("display_approval_order", "percentage approval must postdate the G6 result")
        latest.append(t_g6)
        ver.ok("g6_shadow_passed")
    ver.ok("display_policy_allowed_by_gate_results")

    # serving parity if the served v1 manifest is not the assessed one
    served = base["manifest_content_sha256"]
    need_parity = served != sub["assessed_base_manifest_sha256"]
    if need_parity and "serving_parity" not in receipt["evidence"]:
        raise QualError("serving_parity_required", "served packaging differs from the assessed packaging; a golden-vector parity receipt is required")
    if "serving_parity" in receipt["evidence"]:
        sp = _load_doc(root, receipt, "serving_parity", "servingParity")
        if sp["synthetic_fixture"] is not receipt["synthetic_fixture"]:
            raise QualError("synthetic_mismatch", "serving parity")
        _same_subject(sp, identity, "serving_parity")
        if sp["from_base_manifest_sha256"] != sub["assessed_base_manifest_sha256"] or sp["to_base_manifest_sha256"] != served:
            raise QualError("serving_parity_mismatch", "parity receipt is not assessed -> served")
        if not (sp["passed"] is True and sp["max_abs_diff"] <= rules["serving_parity_max_abs_diff"]):
            raise QualError("serving_parity_failed", "")
        latest.append(parse_utc(sp["produced_at_utc"], "parity produced_at"))
        ver.ok("serving_parity_ok")
    if any(t > t_issued for t in latest):
        raise QualError("receipt_backdated", "receipt issued before some of its evidence existed")
    ver.ok("receipt_issued_after_all_evidence")
    ver.display = disp
    return ver


def _declared(doc, gate_id, partition, rule, identity, receipt):
    if doc["gate_id"] != gate_id or doc["partition"] != partition:
        raise QualError("gate_mismatch", f"{gate_id}: gate_id/partition")
    if doc["synthetic_fixture"] is not receipt["synthetic_fixture"]:
        raise QualError("synthetic_mismatch", gate_id)
    _same_subject(doc, identity, gate_id)
    if doc["rule"]["rule_id"] != rule["rule_id"] or doc["rule"]["rule_sha256"] != rule["rule_sha256"]:
        raise QualError("gate_rule_mismatch", f"{gate_id}: not the rule the owner registered")
    if doc["passed"] is not True:
        raise QualError("gate_failed", gate_id)


def _band_evidence(be, rule, identity, report_sha, receipt):
    if be["synthetic_fixture"] is not receipt["synthetic_fixture"]:
        raise QualError("synthetic_mismatch", "band_evidence")
    _same_subject(be, identity, "band_evidence")
    if be["source_report_sha256"] != report_sha:
        raise QualError("band_evidence_mismatch", "derived from a different report")
    order = [b["band"] for b in be["bands"]]
    if order != ["low", "medium", "high"]:
        raise QualError("band_evidence_invalid", "bands must be low, medium, high in order")
    prev = None
    for b in be["bands"]:
        if b["n"] < rule["min_band_n"]:
            raise QualError("band_evidence_failed", f"band {b['band']} n below owner minimum")
        if prev is not None:
            if rule["strictly_increasing"] and not b["observed_rate"] > prev["observed_rate"]:
                raise QualError("band_evidence_failed", "rates not strictly increasing")
            lb = b["adjacent_lower_bound"]
            if lb is None or not lb > rule["adjacent_lower_bound_gt"]:
                raise QualError("band_evidence_failed", f"adjacent lower bound at {b['band']}")
        elif b["adjacent_lower_bound"] is not None:
            raise QualError("band_evidence_invalid", "lowest band has no adjacent bound")
        prev = b
