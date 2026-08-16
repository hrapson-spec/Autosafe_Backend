"""Deployability-class tagging, loaded at BUILD TIME from serve_view_classes.json.

The classification is produced by a different agent (A2) and may not exist when
the factory is written or even when a first build runs. Nothing here hardcodes a
class vocabulary: the shipped `serve_view_schema.json` describes the SHAPE of
the file, the loader validates the instance against that shape, and the class
codes themselves come from the instance's own `classes` declaration.

Absent file -> every column is tagged UNCLASSIFIED and the BUILD_MANIFEST
records `serve_view_classes: absent`. A build is never blocked by it (a missing
classification is a labelling gap, not a correctness defect), but the gap is
never silent either.
"""
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

SCHEMA_FILENAME = "serve_view_schema.json"
DEFAULT_CLASSES_FILENAME = "serve_view_classes.json"
UNCLASSIFIED = "UNCLASSIFIED"
#: blocks.ERA_RESEARCH, restated here so serve_view has no import cycle.
RESEARCH_ONLY_ERA = "research_only_input"
#: The DOWN-classification a block-level fallback yields for a column the
#: factory flags research-only. Deliberately NOT a code from the instance's own
#: vocabulary, so no downstream "production-common subset" filter can match it.
RESEARCH_ONLY_BY_ERA = "UNCLASSIFIED_RESEARCH_ONLY_INPUT"


class SchemaViolation(ValueError):
    """serve_view_classes.json does not match the shipped schema."""


def schema_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), SCHEMA_FILENAME)


def load_schema(path: Optional[str] = None) -> dict:
    with open(path or schema_path(), "r", encoding="utf-8") as fh:
        return json.load(fh)


# --- minimal structural validator (no third-party dependency) ---------------

def validate(instance: Any, schema: dict, where: str = "$") -> None:
    """Validate the subset of JSON-Schema the shipped schema uses."""
    expected = schema.get("type")
    if expected:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_is_type(instance, t) for t in types):
            raise SchemaViolation(f"{where}: expected type {expected}, got "
                                  f"{type(instance).__name__}")
    if "enum" in schema and instance not in schema["enum"]:
        raise SchemaViolation(f"{where}: {instance!r} not in enum {schema['enum']}")
    if isinstance(instance, dict):
        for name in schema.get("required", []):
            if name not in instance:
                raise SchemaViolation(f"{where}: missing required property {name!r}")
        props = schema.get("properties", {})
        for name, value in instance.items():
            if name in props:
                validate(value, props[name], f"{where}.{name}")
            elif "additionalProperties" in schema:
                extra = schema["additionalProperties"]
                if extra is False:
                    raise SchemaViolation(f"{where}: unexpected property {name!r}")
                if isinstance(extra, dict):
                    validate(value, extra, f"{where}.{name}")
    if isinstance(instance, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for idx, value in enumerate(instance):
                validate(value, item_schema, f"{where}[{idx}]")
        if "minItems" in schema and len(instance) < schema["minItems"]:
            raise SchemaViolation(f"{where}: expected >= {schema['minItems']} items")


def _is_type(value: Any, name: str) -> bool:
    return {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }.get(name, lambda v: True)(value)


# --- the loaded view --------------------------------------------------------

@dataclass
class ServeView:
    """Deployability classes for emitted columns and whole blocks."""

    available: bool = False
    source_path: Optional[str] = None
    version: Optional[str] = None
    class_codes: List[str] = field(default_factory=list)
    #: True when `class_codes` was DECLARED by the instance rather than derived
    #: from the class values it happens to use.
    classes_declared: bool = False
    by_feature: Dict[str, dict] = field(default_factory=dict)
    by_block: Dict[str, dict] = field(default_factory=dict)

    def class_for_block(self, block: str) -> str:
        entry = self.by_block.get(block)
        return str(entry.get("class")) if entry else UNCLASSIFIED

    def class_for_column(self, column: str, block: str,
                         era_observability: Optional[str] = None) -> str:
        """Class for one emitted column. AMBIGUOUS CASES CLASSIFY DOWN.

        A per-feature entry is an explicit ruling and always wins. The
        block-level FALLBACK, however, may only ever classify a column DOWN:
        a column the factory itself flags `research_only_input` (it consumes
        test_type-in-history or PRS visibility, neither of which serving has)
        must not inherit its block's deployable class. The adversarial review
        found four such columns resolving to production-common through the B1
        and B5 block entries -- a D1-class defect reintroduced by labelling.
        """
        entry = self.by_feature.get(column)
        if entry:
            return str(entry.get("class"))
        if era_observability == RESEARCH_ONLY_ERA:
            return RESEARCH_ONLY_BY_ERA
        return self.class_for_block(block)

    def flags_for_column(self, column: str, block: str) -> List[str]:
        entry = self.by_feature.get(column) or self.by_block.get(block) or {}
        return list(entry.get("flags") or [])

    def manifest_entry(self) -> dict:
        return {
            "available": self.available,
            "path": self.source_path,
            "version": self.version,
            "class_codes": self.class_codes,
            "classes_declared": self.classes_declared,
            "n_features_classified": len(self.by_feature),
            "n_blocks_classified": len(self.by_block),
        }


def load_serve_view(path: Optional[str], schema: Optional[dict] = None,
                    required: bool = False) -> ServeView:
    """Load and schema-validate the classification; tolerate absence."""
    if not path or not os.path.exists(path):
        if required:
            raise SchemaViolation(
                f"serve_view_classes.json required but not found at {path!r}")
        return ServeView(available=False, source_path=path)
    with open(path, "r", encoding="utf-8") as fh:
        instance = json.load(fh)
    validate(instance, schema or load_schema(), "$")

    declared = [str(c["code"]) for c in instance.get("classes", [])]
    by_feature = dict(instance.get("features", {}))
    by_block = dict(instance.get("blocks", {}))
    if not by_feature and not by_block:
        raise SchemaViolation(
            f"{path}: neither `features` nor `blocks` is present -- the file "
            f"classifies nothing.")

    observed = set()
    for scope, table in (("features", by_feature), ("blocks", by_block)):
        for name, entry in table.items():
            code = str(entry.get("class"))
            observed.add(code)
            if declared and code not in declared:
                raise SchemaViolation(
                    f"{scope}.{name}: class {code!r} is not declared in the file's "
                    f"own `classes` list {declared}. The factory never hardcodes a "
                    f"class vocabulary -- declare it in the instance.")
    return ServeView(
        available=True,
        source_path=path,
        version=instance.get("version"),
        # vocabulary comes from the instance, declared or observed -- never from
        # a list baked into the factory.
        class_codes=declared or sorted(observed),
        classes_declared=bool(declared),
        by_feature=by_feature,
        by_block=by_block,
    )
