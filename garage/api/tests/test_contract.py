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
    assert checked == 8


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
