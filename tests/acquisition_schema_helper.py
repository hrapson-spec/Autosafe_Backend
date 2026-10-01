"""Test-only JSON Schema evaluator (a separate implementation from the
collector's pydantic models) for the keywords docs/acquisition/
event_schema_v1.json uses: type, const, enum, pattern, required, properties,
additionalProperties:false, oneOf, allOf, if/then, not. It exists so the
collector's hand-written combination rules can be checked against the
documented schema over a full grid (tests/test_acquisition_collector.py).
"""
import json
import re
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "docs" / "acquisition" / "event_schema_v1.json"
SUPPORTED = {
    "$schema", "$id", "title", "description", "type", "const", "enum", "pattern", "required",
    "properties", "additionalProperties", "oneOf", "allOf", "if", "then", "not",
}


def load_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _type_ok(t, v):
    return {
        "string": isinstance(v, str),
        "boolean": isinstance(v, bool),
        "integer": isinstance(v, int) and not isinstance(v, bool),
        "object": isinstance(v, dict),
    }[t]


def validates(schema, value):
    for key in schema:
        if key not in SUPPORTED:
            raise AssertionError("unsupported schema keyword in test checker: %s" % key)
    if "type" in schema and not _type_ok(schema["type"], value):
        return False
    if "const" in schema and (schema["const"] != value or type(schema["const"]) is not type(value)):
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if "pattern" in schema and not (isinstance(value, str) and re.search(schema["pattern"], value)):
        return False
    if isinstance(value, dict):
        for r in schema.get("required", []):
            if r not in value:
                return False
        props = schema.get("properties", {})
        for k, sub in props.items():
            if k in value and not validates(sub, value[k]):
                return False
        if schema.get("additionalProperties") is False:
            if any(k not in props for k in value):
                return False
    if "not" in schema and validates(schema["not"], value):
        return False
    if "allOf" in schema and not all(validates(s, value) for s in schema["allOf"]):
        return False
    if "oneOf" in schema and sum(1 for s in schema["oneOf"] if validates(s, value)) != 1:
        return False
    if "if" in schema:
        if validates(schema["if"], value):
            if "then" in schema and not validates(schema["then"], value):
                return False
    return True
