"""Garage monitoring evaluators (G6 input/score PSI, G7 live bias/AUC, band monotonicity, rollback decision).

The evaluators exported here are pure and stdlib-only. Three modules are NOT imported by this package and are used only by the
operator commands: reader.py (read-only SELECTs), replay.py (runs a verified bundle's scorer in features mode as a subprocess)
and runs.py (append-only INSERT into monitoring_runs, only with --record). The CLI commands (garage/api/cli.py monitor-g6 / monitor-g7) read every value from a policy file.
"""
from ._stats import MonitoringInputError
from .bands import evaluate_bands
from .classify import OutcomeClass, OutcomePolicy, PrsPairingRule, classify_booking_outcome
from .g7 import G7Params, evaluate_g7
from .outcomes import band_for_probability, f_or_prs_from_candidates, settled_outcome
from .psi import evaluate_g6, psi_from_counts, simulate_null
from .reference import ReferenceArtefact, build_reference_dict, dumps_canonical
from .rollback import RollbackPolicy, decide
from .settled import build_records, evaluate_settled

__all__ = ["MonitoringInputError", "evaluate_bands", "G7Params", "evaluate_g7", "band_for_probability", "f_or_prs_from_candidates",
           "settled_outcome", "evaluate_g6", "psi_from_counts", "simulate_null", "ReferenceArtefact",
           "build_reference_dict", "dumps_canonical", "RollbackPolicy", "decide", "OutcomeClass", "OutcomePolicy",
           "PrsPairingRule", "classify_booking_outcome", "build_records", "evaluate_settled"]
