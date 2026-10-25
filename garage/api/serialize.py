"""Row -> API JSON. Timestamps are rendered in Europe/London."""
from __future__ import annotations

from .timeutil import london_iso

REASON_TEXT = {
    "model_not_packaged": "The assessment model is not packaged in this build.",
    "service_only": "Service-only bookings have no MOT to assess.",
    "retest_booking": "Retests are not assessed.",
    "no_mot_history": "No MOT history was found for this registration.",
    "no_prior_mot": "No earlier MOT test is recorded before the cut-off date.",
    "booking_cancelled": "The booking was cancelled before the assessment ran.",
    "upstream_retrying": "The MOT history service did not answer; trying again.",
    "upstream_unavailable": "The MOT history service did not answer after several attempts.",
    "upstream_error": "The MOT history service refused the request.",
    "fair_use_deferred": "Daily assessment allowance used; this will run the next day.",
    "provider_error": "The assessment provider failed.",
    "invalid_provider_output": "The assessment provider returned an invalid result.",
}


def booking(b: dict, assessment=None, decision=None, outcome=None) -> dict:
    return {
        "id": b["id"], "registration": b["registration"],
        "appointment_date": b["appointment_date"].isoformat(),
        "original_appointment_date": b["import_key_date"].isoformat(),
        "booking_type": b["booking_type"], "status": b["status"], "source": b["source"],
        "rescheduled": b["rescheduled_at"] is not None,
        "created_at": london_iso(b["created_at"]),
        "assessment": assessment_json(assessment) if assessment else None,
        "decision": decision_json(decision) if decision else None,
        "outcome": outcome_json(outcome) if outcome else None,
    }


def assessment_json(a: dict) -> dict:
    ev = a["evidence"]
    return {
        "id": a["id"], "booking_id": a["booking_id"], "version": a["version"], "state": a["state"],
        "reason": a["reason"], "reason_text": a["reason_text"] or REASON_TEXT.get(a["reason"]),
        "result": a["result"], "evidence": ev, "trigger": a["trigger"],
        "input_cutoff_date": a["input_cutoff_date"].isoformat(),
        "input_cutoff_at": london_iso(a["input_cutoff_at"]),
        "provider": ({"name": a["provider_name"], "version": a["provider_version"]} if a["provider_name"] else None),
        "retry_at": london_iso(a["retry_at"]), "created_at": london_iso(a["created_at"]),
        "completed_at": london_iso(a["completed_at"]),
    }


def decision_json(d: dict) -> dict:
    return {"id": d["id"], "booking_id": d["booking_id"], "decision_type": d["decision_type"], "note": d["note"],
            "actor": {"membership_id": d["membership_id"], "email": d["actor_email"], "role": d["actor_role"]},
            "assessment_id": d["assessment_id"], "supersedes_id": d["supersedes_id"],
            "created_at": london_iso(d["created_at"])}


def outcome_json(o: dict) -> dict:
    return {"id": o["id"], "booking_id": o["booking_id"], "check_day": o["check_day"], "status": o["status"],
            "due_at": london_iso(o["due_at"]), "checked_at": london_iso(o["checked_at"]),
            "matched_test": o["matched"], "candidates": o["candidates"] or [], "detail": o["detail"],
            "final": o["check_day"] == 14}
