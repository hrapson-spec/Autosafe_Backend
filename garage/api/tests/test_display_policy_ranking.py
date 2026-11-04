"""display_policy (contract gap 4) and the order-only rank_key (gap 5). The unrounded score never reaches a client."""
import dataclasses
import json

import pytest

from garage.api import ranking, worker
from garage.api.provider import STUB_DISPLAY_POLICY, DisplayPolicy, ProviderResult
from .conftest import available, day, history, import_rows, make_workshop, sql

V = "/api/garage/v1"


def run_one(env, c, result, reg="AB12CDE"):
    env.dvsa.set_history(reg, history(reg, [("2023-01-01", "PASSED")]))
    env.provider.result = result
    bid = import_rows(c, [(reg, day(2), "mot")])["created_booking_ids"][0]
    worker.drain(env.ctx)
    return bid, c.get(f"{V}/bookings/{bid}").json()["assessment"]


def test_stub_supplies_a_provisional_display_policy_even_without_an_estimate(env):
    c = make_workshop(env, "A", "o@a.example")
    env.dvsa.set_history("AB12CDE", history("AB12CDE", [("2023-01-01", "PASSED")]))
    bid = import_rows(c, [("AB12CDE", day(2), "mot")])["created_booking_ids"][0]
    assert c.get(f"{V}/bookings/{bid}").json()["assessment"]["display_policy"] is None       # nothing ran yet
    worker.drain(env.ctx)
    a = c.get(f"{V}/bookings/{bid}").json()["assessment"]
    assert a["state"] == "unavailable" and a["rank_key"] is None
    assert a["display_policy"] == {"rounding_pp": 5, "band_edges": [20, 35],
                                   "band_labels": {"low": "Low", "medium": "Medium", "high": "High"},
                                   "provisional": True, "display_state": "unavailable", "policy_version": "garage-display-v1",
                                   "model_version": None, "suppression_reason": "model_not_qualified",
                                   "operational_qualified": False, "ranking_enabled": False}
    # services never go through a provider: no policy
    sid = import_rows(c, [("AB13CDE", day(2), "service")])["created_booking_ids"][0]
    assert c.get(f"{V}/bookings/{sid}").json()["assessment"]["display_policy"] is None


def test_available_assessment_carries_policy_and_rank_key_and_never_the_score(env):
    c = make_workshop(env, "A", "o@a.example")
    bid, a = run_one(env, c, available(25, score=0.25371))
    assert a["state"] == "available" and a["result"] == {"percent_rounded": 25, "band": "medium"}
    assert a["display_policy"]["provisional"] is True and a["display_policy"]["rounding_pp"] == 5
    assert a["rank_key"] is None
    assert c.get(f"{V}/bookings").json()["items"][0]["assessment"]["rank_key"] == 1
    # not in the booking, the assessment list, or anywhere else a client can reach
    blobs = [c.get(f"{V}/bookings/{bid}").text, c.get(f"{V}/bookings/{bid}/assessments").text, c.get(f"{V}/bookings").text,
             c.get(f"{V}/bookings/{bid}/briefing").text, c.get(f"{V}/bookings/{bid}/decisions").text]
    for b in blobs:
        assert "0.25371" not in b and "25371" not in b and "score" not in b.lower()
    assert sql(env, "SELECT score_unrounded FROM assessments WHERE id = %s", (a["id"],))[0]["score_unrounded"] == 0.25371


def test_rank_key_separates_equal_rounded_percentages_in_the_right_order(env):
    c = make_workshop(env, "A", "o@a.example")
    out = {}
    for reg, score in (("AB11CDE", 0.2549), ("AB12CDE", 0.2501), ("AB13CDE", 0.2523)):
        _, a = run_one(env, c, available(25, score=score), reg)
        out[reg] = a
    out = {b["registration"]: b["assessment"] for b in c.get(f"{V}/bookings").json()["items"]}
    assert {a["result"]["percent_rounded"] for a in out.values()} == {25}
    order = sorted(out, key=lambda r: out[r]["rank_key"], reverse=True)
    assert order == ["AB11CDE", "AB13CDE", "AB12CDE"]
    assert len({a["rank_key"] for a in out.values()}) == 3


def test_ordinal_keys_reveal_only_order_and_ties():
    assert ranking.ordinal_keys({"a": 0.2549, "b": 0.2501, "c": 0.2523, "d": 0.2523}) == {"a": 3, "b": 1, "c": 2, "d": 2}
    assert ranking.ordinal_keys({"a": .9, "b": .1, "c": .5, "d": .5}) == {"a": 3, "b": 1, "c": 2, "d": 2}


def test_list_ordinals_are_stable_across_pagination_and_ties(env):
    c = make_workshop(env, "A", "o@a.example")
    for reg, score in (("AB11CDE", .2549), ("AB12CDE", .2501), ("AB13CDE", .2523), ("AB14CDE", .2523)):
        run_one(env, c, available(25, score=score), reg)
    full = c.get(f"{V}/bookings").json()["items"]
    cursor, pages = None, []
    while True:
        response = c.get(f"{V}/bookings", params={"limit": 1, **({"cursor": cursor} if cursor else {})}).json()
        pages += response["items"]
        cursor = response["next_cursor"]
        if not cursor:
            break
    assert [(b["id"], b["assessment"]["rank_key"]) for b in pages] == [(b["id"], b["assessment"]["rank_key"]) for b in full]
    by_reg = {b["registration"]: b["assessment"]["rank_key"] for b in full}
    assert by_reg["AB13CDE"] == by_reg["AB14CDE"] == 2
    assert set(by_reg.values()) == {1, 2, 3}


@pytest.mark.parametrize("res", [
    dataclasses.replace(available(25), band="high"),                                              # band disagrees with edges
    dataclasses.replace(available(25), rank_score=None),                                          # score required
    dataclasses.replace(available(25), rank_score=0.40),                                          # score disagrees with percent
    dataclasses.replace(available(25), rank_score=1.5),
    dataclasses.replace(available(25), display_policy=None),                                      # policy required
    dataclasses.replace(available(25), display_policy=DisplayPolicy(10, (20, 40))),               # rounding must be 5
    dataclasses.replace(available(25), display_policy=DisplayPolicy(5, (22, 35))),                # edges on the 5-pp grid
    dataclasses.replace(available(25), display_policy=DisplayPolicy(5, (35, 20))),
    dataclasses.replace(available(25), display_policy=DisplayPolicy(5, (20, 35), {"low": "L"})),
    ProviderResult("unavailable", reason="x", rank_score=0.2),                                    # score only when available
])
def test_invalid_provider_outputs_are_failed_and_store_nothing_to_rank(env, res):
    c = make_workshop(env, "A", "o@a.example")
    bid, a = run_one(env, c, res)
    assert (a["state"], a["reason"]) == ("failed", "invalid_provider_output")
    assert a["rank_key"] is None and a["display_policy"] is None
    row = sql(env, "SELECT rank_key, score_unrounded, display_policy FROM assessments WHERE id = %s", (a["id"],))[0]
    assert (row["rank_key"], row["score_unrounded"], row["display_policy"]) == (None, None, None)


def test_database_refuses_a_score_on_a_non_available_assessment(env):
    c = make_workshop(env, "A", "o@a.example")
    bid, a = run_one(env, c, ProviderResult("unavailable", reason="model_not_packaged"))
    assert a["state"] == "unavailable"
    with pytest.raises(Exception, match="frozen"):
        sql(env, "UPDATE assessments SET rank_key = 5 WHERE id = %s", (a["id"],))
    with pytest.raises(Exception, match="assessments_score_only_when_available"):
        sql(env, "INSERT INTO assessments (workshop_id, booking_id, version, trigger, state, input_cutoff_date, input_cutoff_at, "
                 "rank_key, completed_at) SELECT workshop_id, booking_id, 99, 'refresh', 'unavailable', current_date, now(), 5, now() "
                 "FROM assessments WHERE id = %s", (a["id"],))


def test_policy_band_for_follows_the_edges():
    p = STUB_DISPLAY_POLICY
    assert [p.band_for(x) for x in (0, 15, 20, 30, 35, 100)] == ["low", "low", "medium", "medium", "high", "high"]
    assert p.problem() is None and json.dumps(p.to_json())


def test_unqualified_real_provider_is_unavailable_and_cannot_rank(env):
    c = make_workshop(env, "A", "o@a.example")
    _, a = run_one(env, c, dataclasses.replace(available(25), display_policy=STUB_DISPLAY_POLICY))
    assert (a["state"], a["reason"]) == ("unavailable", "model_not_qualified")
    assert a["result"] is None and a["rank_key"] is None
    row = sql(env, "SELECT rank_key, score_unrounded FROM assessments WHERE id = %s", (a["id"],))[0]
    assert row["rank_key"] is None and row["score_unrounded"] is None


@pytest.mark.parametrize("mode", ["shadow", "unavailable"])
def test_shadow_policy_suppresses_operational_output(env, mode):
    c = make_workshop(env, "A", "o@a.example")
    pol = dataclasses.replace(STUB_DISPLAY_POLICY, display_state=mode)
    _, a = run_one(env, c, dataclasses.replace(available(25), display_policy=pol))
    assert a["state"] == "unavailable" and a["result"] is None and a["rank_key"] is None


def test_bands_only_policy_suppresses_percentage_and_default_ranking(env):
    c = make_workshop(env, "A", "o@a.example")
    pol = dataclasses.replace(STUB_DISPLAY_POLICY, display_state="bands", provisional=False,
                              operational_qualified=True, model_version="test-qualified", suppression_reason=None)
    _, a = run_one(env, c, dataclasses.replace(available(25), display_policy=pol))
    assert a["state"] == "available" and a["result"] == {"band": "medium", "percent_rounded": None}
    assert a["rank_key"] is None


@pytest.mark.parametrize("changes", [{"display_state": "unknown"}, {"display_state": "percentage"},
    {"display_state": "percentage", "provisional": False, "operational_qualified": True, "model_version": None}])
def test_inconsistent_display_authority_is_rejected(env, changes):
    c = make_workshop(env, "A", "o@a.example")
    _, a = run_one(env, c, dataclasses.replace(available(25), display_policy=dataclasses.replace(STUB_DISPLAY_POLICY, **changes)))
    assert a["state"] == "failed" and a["result"] is None and a["rank_key"] is None


def test_serialization_suppresses_stale_nonavailable_and_unqualified_rows(env):
    from garage.api.serialize import assessment_json
    c = make_workshop(env, "A", "o@a.example")
    _, a = run_one(env, c, available(25))
    row = sql(env, "SELECT * FROM assessments WHERE id = %s", (a["id"],))[0]
    for state, policy in (("failed", row["display_policy"]), ("available", None),
                          ("available", {**row["display_policy"], "display_state": "shadow"})):
        out = assessment_json({**row, "state": state, "display_policy": policy})
        assert out["result"] is None and out["rank_key"] is None


def test_simulated_provider_cannot_enable_live_output(env):
    from garage.api.serialize import assessment_json
    c = make_workshop(env, "A", "o@a.example")
    _, a = run_one(env, c, available(25))
    row = sql(env, "SELECT * FROM assessments WHERE id = %s", (a["id"],))[0]
    assert assessment_json(row)["result"] is None
    env.ctx.settings = dataclasses.replace(env.ctx.settings, allow_simulated_assessments=False)
    _, out = run_one(env, c, available(25), "AB13CDE")
    assert out["state"] == "unavailable" and out["result"] is None and out["rank_key"] is None


def test_list_ordinals_exclude_suppressed_rows_and_other_workshops(env):
    c = make_workshop(env, "A", "o@a.example")
    other = make_workshop(env, "B", "o@b.example")
    run_one(env, c, available(25, score=.25), "AB11CDE")
    run_one(env, c, ProviderResult("unavailable", reason="model_not_qualified"), "AB12CDE")
    run_one(env, other, available(95, score=.95), "AB13CDE")
    items = c.get(f"{V}/bookings").json()["items"]
    keys = {b["registration"]: b["assessment"]["rank_key"] for b in items}
    assert keys == {"AB11CDE": 1, "AB12CDE": None}
    assert other.get(f"{V}/bookings").json()["items"][0]["assessment"]["rank_key"] == 1
