"""Garage monitoring evaluators (G6 input/score PSI, G7 live bias/AUC, band monotonicity, rollback decision).

Pure, stdlib-only, side-effect-free: nothing here touches the database, the network or any model bundle.
Wiring into the app is proposed separately (execution/tickets/MONITORING_V1/wiring.patch) and is not applied.
"""
from .bands import evaluate_bands
from .g7 import G7Params, evaluate_g7
from .outcomes import band_for_probability, f_or_prs_from_candidates, settled_outcome
from .psi import evaluate_g6, psi_from_counts, simulate_null
from .reference import ReferenceArtefact, build_reference_dict, dumps_canonical
from .rollback import RollbackPolicy, decide

__all__ = ["evaluate_bands", "G7Params", "evaluate_g7", "band_for_probability", "f_or_prs_from_candidates",
           "settled_outcome", "evaluate_g6", "psi_from_counts", "simulate_null", "ReferenceArtefact",
           "build_reference_dict", "dumps_canonical", "RollbackPolicy", "decide"]
