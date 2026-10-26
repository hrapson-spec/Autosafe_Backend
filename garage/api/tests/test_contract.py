"""Contract <-> implementation drift. (Every response in every other test is also validated against the
contract by the _ContractClient in conftest.)"""
import re

import yaml

from .contract_check import CONTRACT
from .conftest import new_client

METHODS = ("get", "post", "patch", "delete", "put")


def test_contract_paths_and_methods_equal_implemented_routes(env):
    doc = yaml.safe_load(CONTRACT.read_text())
    declared = {(m.upper(), "/api/garage/v1" + p) for p, item in doc["paths"].items() for m in item if m in METHODS}
    router = new_client(env).app.state.garage_router
    implemented = {(m, r.path) for r in router.routes for m in r.methods - {"HEAD", "OPTIONS"}}
    assert declared == implemented
    ids = [op["operationId"] for item in doc["paths"].values() for m, op in item.items() if m in METHODS]
    assert len(ids) == len(set(ids))


def test_request_bodies_match_pydantic_models(env):
    doc = yaml.safe_load(CONTRACT.read_text())
    gen = new_client(env).app.openapi()

    def deref(spec, root):
        while "$ref" in spec:
            cur = root
            for part in spec["$ref"].lstrip("#/").split("/"):
                cur = cur[part]
            spec = cur
        return spec
    checked = 0
    for p, item in doc["paths"].items():
        for m, op in item.items():
            if m not in METHODS or "requestBody" not in op:
                continue
            want = deref(op["requestBody"]["content"]["application/json"]["schema"], doc)
            got_op = gen["paths"]["/api/garage/v1" + p][m]
            got = deref(got_op["requestBody"]["content"]["application/json"]["schema"], gen)
            assert set(want.get("properties", {})) == set(got.get("properties", {})), (m, p)
            assert set(want.get("required", [])) == set(got.get("required", [])), (m, p)
            checked += 1
    assert checked == 9


def test_query_parameters_match(env):
    doc = yaml.safe_load(CONTRACT.read_text())
    gen = new_client(env).app.openapi()
    want = {x["name"] for x in doc["paths"]["/bookings"]["get"]["parameters"]}
    got = {x["name"] for x in gen["paths"]["/api/garage/v1/bookings"]["get"]["parameters"]}
    assert want == got


def test_contract_states_and_ui_hint():
    doc = yaml.safe_load(CONTRACT.read_text())
    s = doc["components"]["schemas"]
    assert s["AssessmentState"]["enum"] == ["pending", "available", "unavailable", "unsupported", "failed"]
    assert s["BookingType"]["enum"] == ["mot", "service", "retest", "service_mot"]
    assert s["DecisionType"]["enum"] == ["check_repair_history", "offer_inspection", "adjust_booking_plan", "proceed_as_booked"]
    assert s["Outcome"]["properties"]["check_day"]["enum"] == [1, 3, 7, 14]
    note = doc["paths"]["/bookings/{booking_id}/decisions"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["note"]
    assert note["type"] == "string" and note["maxLength"] == 500 and "x-ui-hint" not in note        # free text
    text = CONTRACT.read_text().lower()
    assert not re.search(r"\b(customer_name|customer_email|phone_number|mobile)\b", text)
    assert not any(re.search(r"/(export|share|print)", p) for p in doc["paths"])      # pilot: staff-only, no export/share


def test_t6b_gap_closures_are_in_the_contract():
    doc = yaml.safe_load(CONTRACT.read_text())
    paths, s = doc["paths"], doc["components"]["schemas"]
    # 1 briefing, 2 feedback
    assert "get" in paths["/bookings/{booking_id}/briefing"] and "put" in paths["/bookings/{booking_id}/feedback"]
    assert s["Feedback"]["required"] == ["value", "briefing_version", "assessment_version", "updated_at"]
    assert s["FeedbackValue"]["enum"] == ["helpful", "not_helpful"]
    assert s["FeedbackReason"]["enum"] == ["inaccurate_records", "not_relevant", "too_long", "missing_information", "other"]
    assert s["BriefingRecord"]["properties"]["result"]["enum"] == ["PASSED", "FAILED", "RECTIFIED"]
    assert s["Briefing"]["properties"]["status"]["enum"] == ["ready", "pending", "unavailable"]
    # 3 demo data is client-side only and says so
    assert "NOT served by this API" in doc["info"]["description"] and not any("demo" in p.lower() for p in paths)
    # 4 display policy, 5 rank_key (order-only; no score of any kind is in the contract)
    assert set(s["DisplayPolicy"]["required"]) == {"rounding_pp", "band_edges", "band_labels", "provisional"}
    assert s["DisplayPolicy"]["properties"]["rounding_pp"]["enum"] == [5]
    assert "display_policy" in s["Assessment"]["properties"] and "rank_key" in s["Assessment"]["properties"]
    names = {p.lower() for sch in s.values() for p in sch.get("properties", {})}
    assert not [n for n in names if re.search(r"score|unrounded|probab|raw_percent", n) and n != "rank_key"]
    # 6 appointment time on bookings, import rows (request and preview) and patch
    assert "appointment_time" in s["Booking"]["properties"] and "time" in s["ImportRow"]["properties"]
    assert "appointment_time" in s["PreviewRow"]["properties"]
    assert "appointment_time" in paths["/bookings/{booking_id}"]["patch"]["requestBody"]["content"]["application/json"]["schema"]["properties"]
    # 7 asynchronous commit with N of M
    commit = paths["/imports/{import_id}/commit"]["post"]["responses"]
    assert set(commit) >= {"200", "202", "404", "410"}
    assert s["ImportRecord"]["properties"]["status"]["enum"] == ["previewed", "committing", "committed", "failed", "expired"]
    assert set(s["ImportProgress"]["required"]) == {"processed", "total"} and "ImportResult" not in s
    # 8 the server-rendered confirm page is the only one
    assert "text/html" in paths["/auth/links/{token}"]["get"]["responses"]["200"]["content"]
    assert not any("confirm" in p for p in paths)
