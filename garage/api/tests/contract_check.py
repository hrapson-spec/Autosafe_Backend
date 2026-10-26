"""Validate live responses against the hand-written OpenAPI contract (3.0 -> JSON Schema draft 7)."""
from __future__ import annotations

import copy
import functools
import pathlib
import re

import jsonschema
import yaml

CONTRACT = pathlib.Path(__file__).resolve().parents[2] / "contract" / "openapi.yaml"
UNIVERSAL = {401, 403, 422}          # documented once in the header notes; all use the Error schema


@functools.lru_cache(maxsize=1)
def load():
    doc = yaml.safe_load(CONTRACT.read_text())
    comps = _convert(copy.deepcopy(doc["components"]))
    ops = []
    for path, item in doc["paths"].items():
        rx = re.compile("^/api/garage/v1" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$")
        for method, op in item.items():
            if method in ("get", "post", "patch", "delete", "put"):
                ops.append((method.upper(), path, rx, op))
    return doc, comps, ops


def _convert(node):
    if isinstance(node, dict):
        node = {k: _convert(v) for k, v in node.items()}
        if node.pop("nullable", False):
            return {"anyOf": [node, {"type": "null"}]}
        return node
    if isinstance(node, list):
        return [_convert(v) for v in node]
    return node


def resolve(ref: dict, doc):
    if "$ref" in ref:
        cur = doc
        for part in ref["$ref"].lstrip("#/").split("/"):
            cur = cur[part]
        return cur
    return ref


def find_op(method: str, path: str):
    ops = load()[2]
    for m, tmpl, rx, op in ops:
        if m == method and rx.match(path):
            return tmpl, op
    return None, None


def check_response(method: str, path: str, resp) -> None:
    doc, comps, _ = load()
    tmpl, op = find_op(method, path)
    assert op is not None, f"{method} {path} is not in the contract"
    status = str(resp.status_code)
    responses = op["responses"]
    if status not in responses:
        assert resp.status_code in UNIVERSAL, f"{method} {tmpl}: status {status} not in contract {list(responses)}"
        spec = doc["components"]["responses"]["Error"]
    else:
        spec = resolve(responses[status], doc)
    ctype = resp.headers.get("content-type", "").split(";")[0]
    content = spec.get("content", {})
    if not content:
        assert not resp.content or resp.status_code in (303,), f"{method} {tmpl} {status}: unexpected body"
        return
    assert ctype in content, f"{method} {tmpl} {status}: content-type {ctype} not in {list(content)}"
    if ctype == "application/json":
        schema = _convert(copy.deepcopy(content[ctype]["schema"]))
        jsonschema.Draft7Validator({"allOf": [schema], "components": comps}).validate(resp.json())
