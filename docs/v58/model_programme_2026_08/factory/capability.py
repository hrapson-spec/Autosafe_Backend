"""Builder/consumer capability contract (PREREG_CUBE_v2 §5).

INC-2026-08-13 in one line: a builder emitted packets with no defect payload,
a consumer asked those packets for defect TEXT, and the combination produced
zeros instead of an error. Neither side ever stated what it could supply or
what it required, so nothing could be asserted.

This module is the missing statement. A packet set publishes a
`PACKET_CAPABILITY.json` sidecar declaring, explicitly:

    defect_payload_mode              what the builder actually emitted
    items_observability              the PREREG §4 states, and their volumes
    items_coverage_mode              certified vs assumed
    source_partition_availability    per input year, as staged
    item_join                        measured, not assumed
    packet_schema_version            + publisher/source schema epochs
    build_config / source hashes     configuration and provenance

A consumer publishes a `ConsumerRequirement`. `assert_satisfies` refuses the
pair. It is called in TWO places, deliberately:

    * `emit.Factory.preflight` -- before packet creation, so a build that CANNOT
      serve its declared consumers never writes a byte;
    * `runners/b0_module_runner` -- before fitting/feature emission, so an
      already-written packet set cannot be consumed incompatibly.

Removing or changing a single flag is explicitly not an acceptable remedy
(PREREG §5): the flag is the trigger, the missing declaration is the defect.
"""
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional, Sequence, Tuple

from . import observability as obs
from .gates import GateFailure

#: Sidecar filename, written INSIDE the packets directory so it can never be
#: separated from the parquet it describes.
CAPABILITY_FILENAME = "PACKET_CAPABILITY.json"

#: Bumped whenever the packet column set or payload semantics change.
#: v1  the pre-incident shape (18 columns; defects_json NULL for BOTH
#:     "no defect items" and "defect detail unavailable" -- unusable).
#: v2  + p_items_observability; defects_json is '[]' for an observed-zero and
#:     NULL only when the detail is genuinely unavailable.
PACKET_SCHEMA_VERSION = 2

#: What the builder put in `defects_json`.
PAYLOAD_ROWS = "rows"        # full per-item payload
PAYLOAD_COUNTS = "counts"    # per-test counts only; NO per-item payload
PAYLOAD_NONE = "none"        # no defect payload at all
PAYLOAD_MODES = (PAYLOAD_ROWS, PAYLOAD_COUNTS, PAYLOAD_NONE)

#: Payload modes that actually carry per-item defect detail.
PAYLOAD_WITH_ITEM_DETAIL = (PAYLOAD_ROWS,)


class CapabilityMismatch(GateFailure):
    """A consumer requires something the packet set does not provide."""


class CapabilityMissing(GateFailure):
    """A packet set carries no capability declaration and none can be derived."""


@dataclass(frozen=True)
class PacketCapability:
    """What a packet set can serve. Written next to the parquet it describes."""

    packet_schema_version: int
    contract_version: str
    #: what the builder actually emitted into defects_json
    defect_payload_mode: str
    defect_keys: Tuple[str, ...]
    #: PREREG §4 three-state vocabulary carried by p_items_observability
    items_observability_states: Tuple[str, ...]
    #: certified (every cell declared) vs assumed_covered (coverage undeclared)
    items_coverage_mode: str
    item_coverage_ledger: Dict[str, Any] = field(default_factory=dict)
    #: measured item join + per-state volumes over the STAGED atoms
    item_join: Dict[str, Any] = field(default_factory=dict)
    #: per input year: what the source/partition actually offered
    source_partition_availability: Dict[str, Any] = field(default_factory=dict)
    #: publisher schema epochs observed in the staged results
    publisher_schema_epochs: Tuple[str, ...] = ()
    #: build configuration + provenance
    build_config: Dict[str, Any] = field(default_factory=dict)
    source_sha256: Dict[str, Any] = field(default_factory=dict)
    code_sha256: str = ""
    duckdb_version: str = ""
    recipe: str = ""
    rung: str = ""
    #: how this object was obtained: "declared" (sidecar) or "measured" (probe)
    capability_source: str = "declared"
    notes: str = ""

    def as_manifest(self) -> dict:
        out = asdict(self)
        out["defect_keys"] = list(self.defect_keys)
        out["items_observability_states"] = list(self.items_observability_states)
        out["publisher_schema_epochs"] = list(self.publisher_schema_epochs)
        return out

    @property
    def carries_item_detail(self) -> bool:
        return self.defect_payload_mode in PAYLOAD_WITH_ITEM_DETAIL

    def write(self, directory: str) -> str:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, CAPABILITY_FILENAME)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.as_manifest(), fh, indent=1, default=str)
        return path

    @classmethod
    def from_manifest(cls, payload: dict) -> "PacketCapability":
        known = {f for f in cls.__dataclass_fields__}          # noqa: SLF001
        kwargs = {k: v for k, v in payload.items() if k in known}
        for key in ("defect_keys", "items_observability_states",
                    "publisher_schema_epochs"):
            if key in kwargs and kwargs[key] is not None:
                kwargs[key] = tuple(kwargs[key])
        kwargs.setdefault("packet_schema_version", 1)
        kwargs.setdefault("contract_version", "")
        kwargs.setdefault("defect_payload_mode", PAYLOAD_NONE)
        kwargs.setdefault("defect_keys", ())
        kwargs.setdefault("items_observability_states", ())
        kwargs.setdefault("items_coverage_mode", obs.COVERAGE_ASSUMED)
        return cls(**kwargs)


@dataclass(frozen=True)
class ConsumerRequirement:
    """What a consumer needs from a packet set before it may run."""

    name: str
    #: the weakest acceptable payload; None = the consumer reads no defects
    needs_defect_payload: Optional[str] = None
    #: the consumer reads per-ITEM detail (text/section/component/severity)
    needs_item_detail: bool = False
    #: the consumer must be able to tell "no defect" from "unknown"
    needs_item_observability: bool = False
    #: 'certified' when assumed coverage is not good enough
    needs_coverage_mode: Optional[str] = None
    min_packet_schema_version: int = 1
    why: str = ""

    def as_manifest(self) -> dict:
        return asdict(self)


#: Known consumers, keyed by the flag combination that selects them. The B0
#: module runner's `--defect-text-source` is the flag from the incident.
CONSUMER_REGISTRY: Dict[str, ConsumerRequirement] = {
    "b0_module:section": ConsumerRequirement(
        name="b0_module_runner --defect-text-source section",
        needs_defect_payload=PAYLOAD_ROWS, needs_item_detail=True,
        needs_item_observability=True, min_packet_schema_version=2,
        why=("feature_engineering_v55 classifies components by keyword-matching "
             "each defect's text; this consumer supplies the catalogue SECTION "
             "name per defect, so it requires the per-item payload. Over a "
             "payload-free packet set it would emit zero components for every "
             "vehicle -- INC-2026-08-13.")),
    "b0_module:component": ConsumerRequirement(
        name="b0_module_runner --defect-text-source component",
        needs_defect_payload=PAYLOAD_ROWS, needs_item_detail=True,
        needs_item_observability=True, min_packet_schema_version=2,
        why="as :section, reading defect.comp instead of defect.sect."),
    "b0_module:none": ConsumerRequirement(
        name="b0_module_runner --defect-text-source none",
        needs_defect_payload=PAYLOAD_ROWS, needs_item_detail=True,
        needs_item_observability=True, min_packet_schema_version=2,
        why=("emits the text family honestly empty, but still reads each "
             "defect's TYPE and dangerous flag, so it still needs the per-item "
             "payload.")),
    "packets_only": ConsumerRequirement(
        name="prior-test tuples only (no defect payload read)",
        needs_defect_payload=None, needs_item_detail=False,
        why="depth/date/outcome/mileage features only."),
}


def requirement_for(spec: str) -> ConsumerRequirement:
    """Resolve a `--for-consumer` spec against the registry."""
    key = str(spec).strip()
    if key not in CONSUMER_REGISTRY:
        raise CapabilityMismatch(
            f"unknown consumer spec {spec!r}. Known consumers: "
            f"{sorted(CONSUMER_REGISTRY)}. A consumer must be REGISTERED before "
            f"a build may promise to serve it -- an unregistered name would be "
            f"an unasserted promise, which is the defect this gate exists for.")
    return CONSUMER_REGISTRY[key]


# --- the assertion -----------------------------------------------------------

def assert_satisfies(capability: PacketCapability,
                     requirement: ConsumerRequirement) -> None:
    """Refuse an incompatible builder/consumer pair. Raises CapabilityMismatch."""
    where = f"{capability.recipe or '?'}/{capability.rung or '?'}"

    if requirement.needs_item_detail and not capability.carries_item_detail:
        raise CapabilityMismatch(
            f"consumer {requirement.name!r} requires per-item defect detail, but "
            f"packet set {where} was built with defect_payload_mode="
            f"{capability.defect_payload_mode!r} -- defects_json carries no items. "
            f"Proceeding would convert unavailable defect data into zero, which "
            f"is INC-2026-08-13 verbatim. {requirement.why} "
            f"Rebuild with --defect-detail rows, or run a consumer that does not "
            f"read defect items. (capability source: {capability.capability_source})")

    if (requirement.needs_defect_payload is not None
            and requirement.needs_defect_payload != capability.defect_payload_mode):
        raise CapabilityMismatch(
            f"consumer {requirement.name!r} requires defect_payload_mode="
            f"{requirement.needs_defect_payload!r}; packet set {where} declares "
            f"{capability.defect_payload_mode!r}.")

    if requirement.min_packet_schema_version > capability.packet_schema_version:
        raise CapabilityMismatch(
            f"consumer {requirement.name!r} requires packet schema >= "
            f"{requirement.min_packet_schema_version}; packet set {where} is "
            f"v{capability.packet_schema_version}. v1 packets cannot distinguish "
            f"'this prior test had no defect items' from 'this prior test's "
            f"defect detail is unavailable' -- both are a NULL defects_json. "
            f"Rebuild the packet set.")

    if (requirement.needs_item_observability
            and not capability.items_observability_states):
        raise CapabilityMismatch(
            f"consumer {requirement.name!r} requires explicit item observability; "
            f"packet set {where} declares none. No feature emitter may infer "
            f"'no defect' from a NULL defects_json (PREREG_CUBE_v2 §4).")

    if (requirement.needs_coverage_mode
            and capability.items_coverage_mode != requirement.needs_coverage_mode):
        raise CapabilityMismatch(
            f"consumer {requirement.name!r} requires items_coverage_mode="
            f"{requirement.needs_coverage_mode!r}; packet set {where} is "
            f"{capability.items_coverage_mode!r}. Supply an --item-coverage-csv "
            f"declaring every cell (including a default rule) to certify it.")


def assert_build_can_serve(defect_payload_mode: str,
                           requirements: Sequence[ConsumerRequirement],
                           coverage_mode: str,
                           emit_packets: bool = True,
                           recipe: str = "", rung: str = "") -> None:
    """Preflight form: assert a CONFIGURATION before any packet is created."""
    if not requirements:
        return
    if not emit_packets:
        raise CapabilityMismatch(
            f"{len(requirements)} consumer(s) were declared but this build emits "
            f"no packets (--no-packets). Declare no consumers, or emit packets.")
    planned = PacketCapability(
        packet_schema_version=PACKET_SCHEMA_VERSION,
        contract_version="(preflight)",
        defect_payload_mode=defect_payload_mode,
        defect_keys=(),
        items_observability_states=tuple(obs.ALL_STATES),
        items_coverage_mode=coverage_mode,
        recipe=recipe, rung=rung, capability_source="preflight (planned)")
    for requirement in requirements:
        assert_satisfies(planned, requirement)


# --- reading a packet set ----------------------------------------------------

def packets_directory(packets_path: str) -> str:
    """Directory holding a packets glob / file / directory."""
    text = str(packets_path)
    if os.path.isdir(text):
        return text
    head = os.path.dirname(text)
    while head and any(ch in os.path.basename(head) for ch in "*?["):
        head = os.path.dirname(head)
    return head or "."


def load_capability(packets_path: str) -> Optional[PacketCapability]:
    """Read the sidecar next to a packet set, or None when absent."""
    path = os.path.join(packets_directory(packets_path), CAPABILITY_FILENAME)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    return PacketCapability.from_manifest(payload)


def measure_capability(con, packets_path: str) -> PacketCapability:
    """Derive a capability by MEASURING a legacy packet set.

    A packet set written before this contract has no sidecar. The fallback is to
    measure what it actually carries -- never to assume it carries anything. A
    payload-free set measures 0 non-null `defects_json` and is refused by any
    consumer that reads defect items, which is exactly what should have happened
    on 2026-08-13.
    """
    rel = f"read_parquet('{packets_path}', union_by_name=true)"
    columns = [d[0] for d in con.execute(f"SELECT * FROM {rel} LIMIT 0").description]
    if "defects_json" not in columns:
        raise CapabilityMissing(
            f"{packets_path} has no defects_json column: it is not a packets view.")
    has_state = "p_items_observability" in columns
    row = con.execute(
        f"SELECT count(*), count(defects_json), "
        f"count(*) FILTER (WHERE p_test_id IS NOT NULL) FROM {rel}").fetchone()
    n_rows, n_payload, n_priors = int(row[0]), int(row[1]), int(row[2])
    mode = PAYLOAD_ROWS if n_payload else PAYLOAD_NONE
    return PacketCapability(
        packet_schema_version=2 if has_state else 1,
        contract_version="(measured from a legacy packet set)",
        defect_payload_mode=mode,
        defect_keys=(),
        items_observability_states=tuple(obs.ALL_STATES) if has_state else (),
        items_coverage_mode=obs.COVERAGE_ASSUMED,
        item_join={"packet_rows": n_rows, "prior_rows": n_priors,
                   "rows_with_defect_payload": n_payload,
                   "defect_payload_rate": (n_payload / n_priors) if n_priors else None},
        capability_source="measured (no PACKET_CAPABILITY.json)",
        notes=("legacy packet set: capability derived by measurement. A NULL "
               "defects_json here is ambiguous by construction -- it may mean "
               "'no defect items' OR 'defect detail unavailable'."))


def assert_packet_set_supports(con, packets_path: str,
                               requirement: ConsumerRequirement) -> PacketCapability:
    """The consumer-side gate: declared capability if present, else measured."""
    capability = load_capability(packets_path)
    if capability is None:
        capability = measure_capability(con, packets_path)
    assert_satisfies(capability, requirement)
    return capability
