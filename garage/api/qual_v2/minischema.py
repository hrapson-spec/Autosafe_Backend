"""A small JSON-Schema (draft 2020-12 subset) validator so the shipped schema is executed, not just documented (jsonschema is not installed here).

Supported keywords: type (name or list), enum, const, required, properties, additionalProperties (bool or schema), items, minItems, maxItems,
pattern, minLength, minimum, maximum, exclusiveMinimum, $ref to '#/$defs/<name>', oneOf, allOf, if/then. Unknown keywords are ignored EXCEPT that
validate() raises on a keyword it does not implement from the REFUSED set below (so a schema author cannot silently rely on an unchecked rule).
"""
from __future__ import annotations
import re

_REFUSED = {"patternProperties", "dependentRequired", "not", "anyOf", "propertyNames", "uniqueItems", "contains", "multipleOf", "format"}
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is_type(v, t):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, _TYPES[t])


def validate(doc, schema, root, path="$"):
    """Return a list of error strings (empty = valid)."""
    errs = []
    bad = _REFUSED & set(schema)
    if bad:
        raise NotImplementedError("schema uses unsupported keyword(s): " + ",".join(sorted(bad)))
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/$defs/"):
            raise NotImplementedError(ref)
        return validate(doc, root["$defs"][ref[len("#/$defs/"):]], root, path)
    if "type" in schema:
        ts = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(doc, t) for t in ts):
            return [f"{path}: type {type(doc).__name__} not in {ts}"]
    if "const" in schema and (doc != schema["const"] or type(doc) is not type(schema["const"])):
        errs.append(f"{path}: const {schema['const']!r}")
    if "enum" in schema and not any(doc == e and type(doc) is type(e) for e in schema["enum"]):
        errs.append(f"{path}: not in enum")
    if isinstance(doc, str):
        if "pattern" in schema and not re.search(schema["pattern"], doc):
            errs.append(f"{path}: pattern")
        if len(doc) < schema.get("minLength", 0):
            errs.append(f"{path}: too short")
    if isinstance(doc, (int, float)) and not isinstance(doc, bool):
        if "minimum" in schema and doc < schema["minimum"]:
            errs.append(f"{path}: < minimum")
        if "maximum" in schema and doc > schema["maximum"]:
            errs.append(f"{path}: > maximum")
        if "exclusiveMinimum" in schema and doc <= schema["exclusiveMinimum"]:
            errs.append(f"{path}: <= exclusiveMinimum")
    if isinstance(doc, list):
        if len(doc) < schema.get("minItems", 0):
            errs.append(f"{path}: fewer than minItems")
        if len(doc) > schema.get("maxItems", 1 << 30):
            errs.append(f"{path}: more than maxItems")
        if "items" in schema:
            for i, x in enumerate(doc):
                errs += validate(x, schema["items"], root, f"{path}[{i}]")
    if isinstance(doc, dict):
        for k in schema.get("required", []):
            if k not in doc:
                errs.append(f"{path}: missing {k}")
        props = schema.get("properties", {})
        for k, v in doc.items():
            if k in props:
                errs += validate(v, props[k], root, f"{path}.{k}")
            else:
                ap = schema.get("additionalProperties", True)
                if ap is False:
                    errs.append(f"{path}: unexpected key {k}")
                elif isinstance(ap, dict):
                    errs += validate(v, ap, root, f"{path}.{k}")
    for sub in schema.get("allOf", []):
        errs += validate(doc, sub, root, path)
    if "oneOf" in schema:
        ok = [s for s in schema["oneOf"] if not validate(doc, s, root, path)]
        if len(ok) != 1:
            errs.append(f"{path}: oneOf matched {len(ok)}")
    if "if" in schema and not validate(doc, schema["if"], root, path) and "then" in schema:
        errs += validate(doc, schema["then"], root, path)
    return errs


def validate_def(doc, root_schema, name):
    return validate(doc, {"$ref": "#/$defs/" + name}, root_schema)
