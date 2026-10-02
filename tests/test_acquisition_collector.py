"""OA-005: first-party acquisition collector (acquisition_routes.py,
acquisition_store.py), checked against DECISIONS.md D-005.

Async: this venv has no pytest-asyncio; store-level tests drive coroutines
with asyncio.run(...) in plain sync tests, and route tests use TestClient.

Backends: every store-level test runs on SQLite. If ACQUISITION_TEST_PG_DSN
is set (a throwaway local Postgres, never production) the same tests also run
on PostgreSQL inside a private schema.
"""
import asyncio
import itertools
import json
import logging
import os
import sqlite3
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

import acquisition_routes as routes  # noqa: E402
import acquisition_store as store_mod  # noqa: E402
from acquisition_store import AcquisitionRecord, PostgresStore, SqliteStore  # noqa: E402
from main import app, limiter  # noqa: E402

from acquisition_schema_helper import load_schema, validates  # noqa: E402

UTC = timezone.utc
ROUTE = "/api/acquisition/events"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"


def uid() -> str:
    return str(uuid.uuid4())


def envelope(**over):
    base = {
        "schema_version": 2,
        "metric_version": "oa-journey-30m-v2",
        "event_id": uid(),
        "session_id": uid(),
        "landing_id": uid(), "window_start_minute": store_mod.minute(store_mod.utcnow()), "pilot_group": "none",
        "page_family": "guide",
        "source_group": "google_organic",
    }
    base.update(over)
    return base


def _with(ev, landing_id=None, **over):
    if landing_id:
        ev["landing_id"] = landing_id
    ev.update(over)
    return ev


def landing(landing_id=None, **over):
    return _with(envelope(event="landing_observed", observation_state="observed"), landing_id or uid(), **over)


def check_started(op, landing_id=None, **over):
    return _with(envelope(event="check_started", operation_id=op, entry_mode="fresh_check", page_family="app"),
                 landing_id, **over)


def report_created(op, landing_id=None, **over):
    return _with(envelope(event="report_created", operation_id=op, entry_mode="fresh_check",
                          result_kind="comparison", match_scope="exact_band", persistence_mode="saved",
                          page_family="app"), landing_id, **over)


def result_rendered(op=None, landing_id=None, **over):
    ev = envelope(
        event="result_rendered", entry_mode="fresh_check", persistence_mode="saved", render_delivered=True,
        supported_result=True, outcome_group="exact_comparison", rate_valid=True, sample_nonzero=True,
        scope_visible=True, result_kind="comparison", match_scope="exact_band", page_family="app",
    )
    if op:
        ev["operation_id"] = op
    if landing_id:
        ev["landing_id"] = landing_id
    ev.update(over)
    return ev


def render_failed(op, landing_id=None, **over):
    return _with(envelope(event="render_failed", operation_id=op, entry_mode="fresh_check", stage="render",
                          page_family="app"), landing_id, **over)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sqlite_path(tmp_path):
    return str(tmp_path / "acq.sqlite")


@pytest.fixture
def collector(monkeypatch, sqlite_path):
    monkeypatch.setenv("ACQUISITION_INGEST_ENABLED", "1")
    monkeypatch.setenv("ACQUISITION_SQLITE_PATH", sqlite_path)
    store = SqliteStore(sqlite_path)
    routes.set_store_for_tests(store)
    limiter.reset()
    client = TestClient(app)
    yield client, sqlite_path
    routes.set_store_for_tests(None)
    limiter.reset()


def post(client, body, headers=None, raw=None):
    h = {"Content-Type": "application/json", "User-Agent": BROWSER_UA}
    h.update(headers or {})
    data = raw if raw is not None else json.dumps(body)
    return client.post(ROUTE, content=data, headers=h)


def rows(path, sql="SELECT * FROM acquisition_v2_events"):
    if not os.path.exists(path):
        return []
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql).fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Acceptance
# ---------------------------------------------------------------------------

class TestAcceptance:
    def test_every_event_type_is_accepted_and_stored(self, collector):
        client, path = collector
        op, lid = uid(), uid()
        events = [
            landing(lid),
            check_started(op, lid),
            report_created(op, lid),
            result_rendered(op, lid),
            envelope(event="result_unavailable", entry_mode="restored_link", reason="expired", page_family="app"),
            envelope(event="check_failed", operation_id=uid(), entry_mode="fresh_check",
                     error_category="network_error", stage="create_report", page_family="app"),
            render_failed(uid()),
        ]
        for ev in events:
            r = post(client, ev)
            assert r.status_code == 202, (ev["event"], r.text)
            assert r.json() == {"status": "accepted"}
        stored = rows(path)
        assert [r["event"] for r in stored] == [e["event"] for e in events]
        assert all(r["window_start_minute"] for r in stored)

    def test_demo_outcome_group_is_accepted(self, collector):
        client, path = collector
        assert post(client, result_rendered(outcome_group="demo", supported_result=False)).status_code == 202
        pred = result_rendered(outcome_group="demo", supported_result=False, result_kind="vehicle_prediction",
                               match_scope="model_prediction")
        del pred["sample_nonzero"]
        assert post(client, pred).status_code == 202
        assert [r["outcome_group"] for r in rows(path)] == ["demo", "demo"]

    def test_paid_search_landing_is_accepted_and_stored(self, collector):
        client, path = collector
        assert post(client, landing(source_group="paid_search")).status_code == 202
        assert rows(path) == []  # advertising arrivals are not collected
        # the paid-click marker itself is not a field the collector knows
        for field in ("gclid", "gbraid", "wbraid", "utm_medium", "utm_campaign"):
            ev = landing(source_group="paid_search")
            ev[field] = "SECRET-VALUE"
            assert post(client, ev).status_code == 400
        assert "SECRET-VALUE" not in json.dumps(rows(path))

    def test_duplicate_event_is_202_and_one_row(self, collector):
        client, path = collector
        ev = landing()
        first, second = post(client, ev), post(client, ev)
        assert first.status_code == second.status_code == 202
        assert first.json() == second.json()
        assert len(rows(path)) == 1

    def test_responses_are_never_cacheable(self, collector, monkeypatch):
        client, path = collector
        assert post(client, landing()).headers["cache-control"] == "no-store"
        assert post(client, {"x": 1}).headers["cache-control"] == "no-store"
        assert post(client, None, raw="x" * 5000).headers["cache-control"] == "no-store"
        monkeypatch.setenv("ACQUISITION_INGEST_ENABLED", "0")
        r = post(client, landing())
        assert r.status_code == 404 and r.headers["cache-control"] == "no-store"

    def test_rate_limited_returns_429_no_store(self, collector):
        client, path = collector
        # slowapi uses a fixed window, so a minute boundary inside this loop can
        # grant a second 120: allow up to two windows before expecting a 429.
        accepted, limited = 0, None
        for _ in range(250):
            r = post(client, landing())
            if r.status_code == 429:
                limited = r
                break
            assert r.status_code == 202
            accepted += 1
        assert limited is not None, "no 429 within 250 requests"
        assert 100 <= accepted <= 240
        assert limited.headers["cache-control"] == "no-store"
        assert len(rows(path)) == accepted  # a limited request stores nothing

    def test_disabled_server_returns_404_and_creates_nothing(self, collector, monkeypatch):
        client, path = collector
        monkeypatch.setenv("ACQUISITION_INGEST_ENABLED", "")
        assert post(client, landing()).status_code == 404
        assert not os.path.exists(path)

    def test_global_privacy_control_header_records_nothing(self, collector):
        client, path = collector
        r = post(client, landing(), headers={"Sec-GPC": "1"})
        assert r.status_code == 202
        assert rows(path) == []


# ---------------------------------------------------------------------------
# Rejection
# ---------------------------------------------------------------------------

FORBIDDEN_FIELDS = {
    "url": "https://www.autosafe.one/app/report/abc",
    "referrer": "https://www.google.com/search?q=mot",
    "referer": "https://www.google.com/",
    "landing_path": "/guides/mot-cost",
    "token": "9c7f2b1a",
    "report_token": "9c7f2b1a",
    "report_id": "123",
    "vrm": "AB12CDE",
    "registration": "AB12CDE",
    "postcode": "SW1A 1AA",
    "make": "FORD",
    "model": "FIESTA",
    "failure_risk": 0.31,
    "total_tests": 1200,
    "total_failures": 300,
    "ip": "203.0.113.9",
    "user_agent": "Mozilla/5.0",
    "email": "a@b.co",
    "message": "free text here",
    "comment": "free text here",
    "received_at": "2026-10-01T00:00:00Z",
    "is_bot": False,
    "query": "?reg=AB12CDE",
    "title": "page title",
}


class TestRejection:
    @pytest.mark.parametrize("field", sorted(FORBIDDEN_FIELDS))
    def test_forbidden_field_is_rejected_on_every_event_type(self, collector, field):
        client, path = collector
        op, lid = uid(), uid()
        for ev in (landing(lid), check_started(op, lid), report_created(op, lid), result_rendered(op, lid)):
            ev[field] = FORBIDDEN_FIELDS[field]
            r = post(client, ev)
            assert r.status_code == 400, (field, ev["event"])
            assert r.json() == {"detail": "invalid_event"}
        assert rows(path) == []

    def test_oversize_body_is_413(self, collector):
        client, path = collector
        ev = landing()
        ev["observation_state"] = "x" * 3000
        assert post(client, ev).status_code == 413
        assert rows(path) == []

    def test_oversize_body_without_content_length_is_413(self, collector):
        client, path = collector

        def gen():
            yield b'{"a":"' + b"x" * 1500
            yield b"y" * 1500 + b'"}'

        r = client.post(ROUTE, content=gen(), headers={"Content-Type": "application/json"})
        assert r.status_code == 413

    def test_body_at_the_limit_is_judged_on_content_not_size(self, collector):
        client, path = collector
        ev = landing()
        raw = json.dumps(ev)
        padded = raw[:-1] + " " * (routes.MAX_BODY_BYTES - len(raw) - 0) + "}"
        assert len(padded.encode()) <= routes.MAX_BODY_BYTES
        assert post(client, None, raw=padded).status_code == 202

    @pytest.mark.parametrize("mutate", [
        lambda e: e.update(schema_version=1),
        lambda e: e.update(schema_version="1"),
        lambda e: e.update(schema_version=True),
        lambda e: e.update(event_id=uid().upper()),
        lambda e: e.update(event_id="not-a-uuid"),
        lambda e: e.update(event_id=uid().replace("-", "")),
        lambda e: e.update(session_id=""),
        lambda e: e.update(session_id=None),
        lambda e: e.pop("session_id"),
        lambda e: e.pop("event_id"),
        lambda e: e.pop("page_family"),
        lambda e: e.pop("source_group"),
        lambda e: e.update(page_family="/guides/x"),
        lambda e: e.update(page_family="report"),
        lambda e: e.update(source_group="https://google.com"),
        lambda e: e.update(source_group="Google_Organic"),
        lambda e: e.update(metric_version="Bad Version!"),
        lambda e: e.update(release_sha="not-hex"),
        lambda e: e.update(release_sha="abc"),
        lambda e: e.update(release_sha="a" * 41),
        lambda e: e.update(landing_id="x"),
        lambda e: e.update(event="nope"),
        lambda e: e.pop("event"),
        lambda e: e.update(observation_state="maybe"),
        lambda e: e.pop("landing_id"),
    ])
    def test_bad_landing_events_are_rejected(self, collector, mutate):
        client, path = collector
        ev = landing()
        mutate(ev)
        assert post(client, ev).status_code == 400
        assert rows(path) == []

    @pytest.mark.parametrize("mutate", [
        lambda e: e.update(supported_result="true"),
        lambda e: e.update(supported_result=1),
        lambda e: e.update(render_delivered=False),
        lambda e: e.update(render_delivered=1),
        lambda e: e.update(rate_valid=1),
        lambda e: e.update(schema_version=2.0),
        lambda e: e.update(rate_valid=None),
        lambda e: e.update(outcome_group="unavailable"),
        lambda e: e.update(outcome_group="error"),
        lambda e: e.update(outcome_group="broad_fallback"),
        lambda e: e.update(match_scope="unavailable"),
        lambda e: e.update(result_kind="broad_fallback"),
        lambda e: e.update(entry_mode="typed"),
        lambda e: e.update(persistence_mode="cookie"),
        lambda e: e.update(operation_id="AB12CDE"),
        lambda e: e.pop("sample_nonzero"),
        lambda e: e.update(rate_valid=False),
        lambda e: e.update(supported_result=False),
        lambda e: e.update(match_scope="model_average"),
    ])
    def test_bad_result_rendered_events_are_rejected(self, collector, mutate):
        client, path = collector
        ev = result_rendered(uid(), uid())
        mutate(ev)
        assert post(client, ev).status_code == 400
        assert rows(path) == []

    @pytest.mark.parametrize("raw", ["", "{", "[]", "null", "42", '"s"', "\xff", '{"event":"landing_observed"}'])
    def test_malformed_bodies_are_400(self, collector, raw):
        client, path = collector
        assert post(client, None, raw=raw.encode("latin-1")).status_code == 400
        assert rows(path) == []

    def test_wrong_content_type_is_400(self, collector):
        client, path = collector
        r = post(client, landing(), headers={"Content-Type": "text/plain"})
        assert r.status_code == 400 and rows(path) == []

    def test_query_string_on_the_collector_is_rejected(self, collector):
        client, path = collector
        r = client.post(ROUTE + "?registration=SECRET", json=landing(), headers={"User-Agent":BROWSER_UA})
        assert r.status_code == 400
        assert rows(path) == []



# ---------------------------------------------------------------------------
# Privacy: nothing identifying is stored or logged
# ---------------------------------------------------------------------------

class TestPrivacy:
    def test_no_ip_user_agent_referer_or_url_is_stored(self, collector):
        client, path = collector
        ev = landing()
        r = post(client, ev, headers={
            "User-Agent": "Mozilla/5.0 UniqueUAString-7f3a Chrome/126",
            "X-Forwarded-For": "203.0.113.77",
            "Referer": "https://www.google.com/search?q=secret-query-9",
            "Origin": "https://evil.example",
            "Cookie": "sid=cookiesecret",
        })
        assert r.status_code == 202
        dump = json.dumps(rows(path)) + open(path, "rb").read().decode("latin-1")
        for needle in ("UniqueUAString", "203.0.113.77", "secret-query-9", "google.com/search",
                       "evil.example", "cookiesecret", "testclient"):
            assert needle not in dump, needle
        row = rows(path)[0]
        assert set(row) == set(store_mod.COLUMNS)

    def test_record_type_has_no_field_that_could_carry_a_request_secret(self):
        banned = {"ip", "user_agent", "ua", "referer", "referrer", "url", "path", "query", "cookie", "host", "origin"}
        assert banned.isdisjoint(store_mod.COLUMNS)

    def test_logs_never_contain_body_ids_ip_or_user_agent(self, collector, caplog):
        client, path = collector
        caplog.set_level(logging.DEBUG)
        sentinel = "SENTINEL-MESSAGE-xyz"
        good, bad = landing(), landing()
        bad["comment"] = sentinel
        ids = [good["event_id"], good["session_id"], good["landing_id"],
               bad["event_id"], bad["session_id"], bad["landing_id"]]
        hdr = {"User-Agent": "Mozilla/5.0 LoggedUA-1", "X-Forwarded-For": "203.0.113.5"}
        post(client, good, headers=hdr)
        post(client, bad, headers=hdr)
        post(client, None, raw=b"{" + b"z" * 5000, headers=hdr)
        post(client, good, headers=hdr)  # duplicate

        class Boom:
            backend = "boom"

            async def ensure_schema(self):
                pass

            async def insert_event(self, rec):
                raise RuntimeError("db exploded for " + rec.event_id + " " + rec.session_id)

        routes.set_store_for_tests(Boom())
        fail = landing()
        ids += [fail["event_id"], fail["session_id"], fail["landing_id"]]
        assert post(client, fail, headers=hdr).status_code == 503
        text = caplog.text
        for needle in ids + [sentinel, "LoggedUA-1", "203.0.113.5", "z" * 20]:
            assert needle not in text, needle
        assert "acquisition_record_failed exception_type=RuntimeError" in text

    def test_rejection_reasons_are_fixed_codes(self, collector, caplog):
        client, path = collector
        caplog.set_level(logging.INFO)
        bad = landing()
        bad["vrm"] = "AB12CDE"
        post(client, bad)
        assert "acquisition_event_rejected reason=invalid_event" in caplog.text
        assert "AB12CDE" not in caplog.text and "vrm" not in caplog.text


class TestNoClientIpInLogs:
    """D-007.2: slowapi's `ratelimit ... (<client ip>) exceeded` warning must not carry the IP."""

    IP = "203.0.113.77"

    def _assert_clean(self, text):
        assert self.IP not in text
        assert "203.0.113" not in text

    def test_429_on_the_collector_logs_no_client_ip(self, collector, caplog):
        client, path = collector
        caplog.set_level(logging.DEBUG)
        hdr = {"X-Forwarded-For": self.IP}
        statuses = [post(client, landing(), headers=hdr).status_code for _ in range(250)]
        assert 429 in statuses
        assert "ratelimit" in caplog.text and "exceeded" in caplog.text  # the warning still exists...
        self._assert_clean(caplog.text)                                   # ...without the address
        assert "[redacted]" in caplog.text
        assert "/api/acquisition/events" in caplog.text                   # the endpoint is still logged

    def test_429_with_ingest_off_logs_no_client_ip(self, collector, monkeypatch, caplog):
        client, path = collector
        monkeypatch.setenv("ACQUISITION_INGEST_ENABLED", "")
        caplog.set_level(logging.DEBUG)
        hdr = {"X-Forwarded-For": self.IP}
        statuses = [post(client, landing(), headers=hdr).status_code for _ in range(250)]
        assert 429 in statuses  # the limiter runs before the ingest check
        self._assert_clean(caplog.text)

    def test_429_on_another_route_logs_no_client_ip(self, collector, caplog):
        client, path = collector
        caplog.set_level(logging.DEBUG)
        statuses = [client.get("/api/v2/reports/" + "a" * 43, headers={"X-Forwarded-For": self.IP}).status_code
                    for _ in range(130)]
        assert 429 in statuses
        assert "ratelimit" in caplog.text
        self._assert_clean(caplog.text)

    def test_ipv6_key_is_redacted_too(self, caplog):
        logger = logging.getLogger("slowapi")
        routes.install_log_redaction()
        caplog.set_level(logging.DEBUG)
        logger.warning("ratelimit %s (%s) exceeded at endpoint: %s", "5 per 1 minute", "2001:db8::1234", "/x")
        logger.warning("something about %s", "198.51.100.9")
        assert "2001:db8" not in caplog.text and "198.51.100.9" not in caplog.text
        assert "exceeded at endpoint: /x" in caplog.text

    def test_filter_is_installed_once(self):
        routes.install_log_redaction()
        routes.install_log_redaction()
        filters = [f for f in logging.getLogger("slowapi").filters if isinstance(f, routes._RedactRateLimitKey)]
        assert len(filters) == 1


class TestReceivedAtGranularity:
    def test_received_at_is_stored_truncated_to_the_minute(self, collector, monkeypatch):
        client, path = collector
        monkeypatch.setattr(store_mod, "utcnow", lambda: datetime(2026, 11, 5, 13, 47, 52, 123456, tzinfo=UTC))
        assert post(client, landing()).status_code == 202
        stored = rows(path)[0]["received_at"]
        assert stored == "2026-11-05T13:47:00.000000Z"


class TestBotFlag:
    @pytest.mark.parametrize("ua,bot", [
        (BROWSER_UA, False),
        ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1", False),
        ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36", False),
        ("Mozilla/5.0 (iPhone) AppleWebKit/605.1.15 Mobile/15E148 [FBAN/FBIOS;FBAV/450.0]", False),
        ("Mozilla/5.0 (Linux; Android 10; CUBOT KINGKONG 5) AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36", False),
        ("Mozilla/5.0 (Linux; Android 11; CUBOT_X30) AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36", False),
        ("Mozilla/5.0 (Linux; Android 9; Cubot P30) AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36", False),
        ("Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)", True),
        ("Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)", True),
        ("Mozilla/5.0 (compatible; DuckDuckBot/1.1)", True),
        ("Mozilla/5.0 (compatible; SomeNewBot/1.2)", True),
        ("Mozilla/5.0 (compatible; bot)", True),
        ("Mozilla/5.0 (compatible; MyCrawler/1.0)", True),
        ("Mozilla/5.0 (compatible; Baiduspider/2.0)", True),
        ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Googlebot/2.1; +http://www.google.com/bot.html)", True),
        ("Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)", True),
        ("Mozilla/5.0 AppleWebKit/537.36 HeadlessChrome/126.0 Safari/537.36", True),
        ("Mozilla/5.0 (Linux) Chrome-Lighthouse", True),
        ("curl/8.4.0", True),
        ("python-requests/2.31", True),
        ("Go-http-client/2.0", True),
        ("UptimeRobot/2.0", True),
        ("facebookexternalhit/1.1", True),
        ("", True),
        (None, True),
    ])
    def test_user_agent_classification(self, ua, bot):
        assert routes.is_bot_user_agent(ua) is bot

    def test_flag_is_stored_and_user_agent_is_not(self, collector):
        client, path = collector
        post(client, landing(), headers={"User-Agent": "Googlebot/2.1"})
        post(client, landing(), headers={"User-Agent": BROWSER_UA})
        r = rows(path)
        assert [x["is_bot"] for x in r] == [1, 0]


class TestFailureIsolation:
    def test_store_down_returns_503_quickly_and_other_routes_still_work(self, collector, monkeypatch):
        client, path = collector

        class Slow:
            backend = "slow"

            async def ensure_schema(self):
                pass

            async def insert_event(self, rec):
                await asyncio.sleep(30)

        monkeypatch.setattr(routes, "RECORD_TIMEOUT_SECONDS", 0.2)
        routes.set_store_for_tests(Slow())
        t0 = time.monotonic()
        r = post(client, landing())
        assert r.status_code == 503 and r.headers["cache-control"] == "no-store"
        assert time.monotonic() - t0 < 2
        # circuit breaker: the next call does not wait for the store at all
        t1 = time.monotonic()
        assert post(client, landing()).status_code == 503
        assert time.monotonic() - t1 < 0.15
        assert client.get("/api/version").status_code == 200
        assert client.get("/health").status_code == 200

    def test_no_store_configured_is_503(self, monkeypatch):
        monkeypatch.setenv("ACQUISITION_INGEST_ENABLED", "1")
        monkeypatch.delenv("ACQUISITION_SQLITE_PATH", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        routes.set_store_for_tests(None)
        limiter.reset()
        assert post(TestClient(app), landing()).status_code == 503


class TestOtherRoutesUnaffected:
    def test_report_route_headers_do_not_gain_collector_headers(self, collector):
        client, _ = collector
        r = client.get("/api/v2/reports/" + "a" * 43)
        baseline = {k.lower() for k in r.headers}
        before = dict(r.headers)
        post(client, landing())
        after = client.get("/api/v2/reports/" + "a" * 43)
        assert {k.lower() for k in after.headers} == baseline
        assert after.headers.get("cache-control") == before.get("cache-control")
        assert after.headers["referrer-policy"] == "no-referrer"

    def test_non_collector_paths_are_not_marked_no_store_by_the_collector(self, collector):
        client, _ = collector
        assert "no-store" not in client.get("/api/version").headers.get("cache-control", "")

    def test_collector_is_same_origin_only_by_csp(self):
        # connect-src 'self' already covers the first-party POST; CSP must not be loosened.
        import re
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")).read()
        csp = re.search(r'Content-Security-Policy"\] = "([^"]+)"', src).group(1)
        assert "connect-src 'self'" in csp
        assert "acquisition" not in csp


# ---------------------------------------------------------------------------
# Wire schema parity with docs/acquisition/event_schema_v2.json
# ---------------------------------------------------------------------------

ENVELOPE_ONLY = {"session_id", "landing_id", "release_sha", "window_start_minute", "pilot_group"}


def to_event_schema_form(ev):
    """Strip the D-005 envelope so the body can be judged by the event schema."""
    out = {k: v for k, v in ev.items() if k not in ENVELOPE_ONLY}
    if ev["event"] != "landing_observed":
        out.pop("page_family", None)
        out.pop("source_group", None)
    return out


def accepted_by_collector(ev):
    try:
        routes.parse_event(ev)
        return True
    except Exception:  # noqa: BLE001
        return False


class TestSchemaParity:
    def test_result_rendered_combination_grid_matches_the_documented_schema(self):
        schema = load_schema()
        kinds = ["comparison", "vehicle_prediction"]
        scopes = ["exact_band", "age_band_only", "model_average", "population_default", "model_prediction"]
        groups = ["prediction", "exact_comparison", "broader_supported_comparison", "dataset_reference", "demo"]
        bools = [True, False]
        samples = [None, True, False]
        n = disagreements = 0
        for k, s, g, sup, rate, vis, smp in itertools.product(kinds, scopes, groups, bools, bools, bools, samples):
            ev = result_rendered(result_kind=k, match_scope=s, outcome_group=g, supported_result=sup,
                                 rate_valid=rate, scope_visible=vis)
            if smp is None:
                ev.pop("sample_nonzero")
            else:
                ev["sample_nonzero"] = smp
            n += 1
            expected = validates(schema, to_event_schema_form(ev))
            actual = accepted_by_collector(ev)
            if expected != actual:
                disagreements += 1
                pytest.fail("disagreement on %s expected=%s actual=%s" % (
                    {x: ev[x] for x in ("result_kind", "match_scope", "outcome_group", "supported_result",
                                         "rate_valid", "scope_visible", "sample_nonzero") if x in ev},
                    expected, actual))
        assert n == 2 * 5 * 5 * 2 * 2 * 2 * 3 and disagreements == 0

    def test_other_events_match_the_documented_schema(self):
        schema = load_schema()
        op, lid = uid(), uid()
        cases = []
        for pf, sg, st in itertools.product(
            ["home", "app", "guide", "make", "model", "comparison", "pillar", "problem_hub", "other_public", "bad"],
            ["google_organic", "other_search", "direct", "referral", "unknown", "internal", "paid_search", "internal_test", "bad"],
            ["observed", "consent_not_given", "unsupported", "bad"],
        ):
            cases.append(landing(lid, page_family=pf, source_group=sg, observation_state=st))
        for rk, ms, pm in itertools.product(["comparison", "vehicle_prediction"],
                                            ["exact_band", "age_band_only", "model_average", "population_default",
                                             "unavailable", "model_prediction"], ["saved", "inline_unsaved", "x"]):
            cases.append(report_created(op, result_kind=rk, match_scope=ms, persistence_mode=pm))
        for em, reason in itertools.product(["fresh_check", "restored_link", "x"],
                                            ["not_found", "expired", "unavailable", "error", "x"]):
            cases.append(envelope(event="result_unavailable", entry_mode=em, reason=reason, page_family="app"))
        for em, stage in itertools.product(["fresh_check", "restored_link"],
                                           ["lazy_load", "render", "contract_invalid", "x"]):
            cases.append(render_failed(op, entry_mode=em, stage=stage))
        for cat, stage in itertools.product(["network_error", "unknown", "vehicle_not_found", "x"],
                                            ["create_report", "x"]):
            cases.append(envelope(event="check_failed", operation_id=op, entry_mode="fresh_check",
                                  error_category=cat, stage=stage, page_family="app"))
        cases.append(check_started(op))
        for ev in cases:
            assert validates(schema, to_event_schema_form(ev)) == accepted_by_collector(ev), ev


# ---------------------------------------------------------------------------
# Store-level: retention, exactly-once aggregation, primary metric
# ---------------------------------------------------------------------------

def _backends():
    params = ["sqlite"]
    if os.environ.get("ACQUISITION_TEST_PG_DSN"):
        params.append("postgres")
    return params


@pytest.fixture(params=_backends())
def store(request, tmp_path):
    if request.param == "sqlite":
        s = SqliteStore(str(tmp_path / "ret.sqlite"))
        asyncio.run(s.ensure_schema())
        yield s
        return
    import asyncpg

    schema = "acq_test_" + uuid.uuid4().hex[:10]
    dsn = os.environ["ACQUISITION_TEST_PG_DSN"]
    loop = asyncio.new_event_loop()

    async def mk():
        admin = await asyncpg.connect(dsn)
        await admin.execute('CREATE SCHEMA "%s"' % schema)
        await admin.close()
        return await asyncpg.create_pool(dsn, min_size=1, max_size=4, server_settings={"search_path": schema})

    pool = loop.run_until_complete(mk())

    async def get_pool():
        return pool

    s = PostgresStore(get_pool)
    s._loop = loop
    # asyncpg pools are loop-bound: run store coroutines on this loop.
    _run_on[id(s)] = loop
    loop.run_until_complete(s.ensure_schema())
    yield s

    async def drop():
        await pool.close()
        admin = await asyncpg.connect(dsn)
        await admin.execute('DROP SCHEMA "%s" CASCADE' % schema)
        await admin.close()

    loop.run_until_complete(drop())
    loop.close()
    _run_on.pop(id(s), None)


_run_on: dict = {}


def run(store, coro):
    loop = _run_on.get(id(store))
    if loop is not None:
        return loop.run_until_complete(coro)
    return asyncio.run(coro)


def rec(received_at, **over):
    base = dict(
        event_id=uid(), received_at=received_at, schema_version=2, metric_version="oa-journey-30m-v2",
        event="landing_observed", session_id=uid(), landing_id=uid(), page_family="guide",
        window_start_minute=store_mod.minute(received_at), pilot_group="none",
        source_group="google_organic", is_bot=False, observation_state="observed",
    )
    base.update(over)
    return AcquisitionRecord(**base)


def ins(store, r):
    store.clock = lambda: r.received_at
    return run(store, store.insert_event(r))


def daily(store):
    return run(store, store.fetch_all(
        "SELECT day, page_family, source_group, event, outcome_group, entry_mode, persistence_mode, is_bot, count "
        "FROM acquisition_v2_daily ORDER BY day, page_family, source_group, event, outcome_group, entry_mode, "
        "persistence_mode, is_bot"))


def raw_count(store):
    return run(store, store.fetch_all("SELECT COUNT(*) AS n FROM acquisition_v2_events"))[0]["n"]


NOW = datetime(2026, 12, 20, 12, 0, 0, tzinfo=UTC)


class TestBoundedJourney:
    def journey(self, store, start=NOW, pilot="none", source="google_organic"):
        lid, op = uid(), uid()
        common = dict(landing_id=lid, window_start_minute=store_mod.minute(start), pilot_group=pilot, source_group=source)
        arrival = rec(start, **common)
        assert ins(store, arrival)
        assert not ins(store, arrival)
        def add(event, elapsed=1, operation=op, **kw):
            return ins(store, rec(start + timedelta(minutes=elapsed), **common, event=event,
                        operation_id=operation, entry_mode="fresh_check", **kw))
        return lid, op, add

    def raw(self, store):
        return run(store, store.raw_metric(NOW.replace(hour=0), NOW.replace(hour=0) + timedelta(days=1)))

    def agg(self, store, pilot=None):
        return run(store, store.landing_aggregate_metric(NOW.date(), NOW.date() + timedelta(days=1), pilot))

    def test_late_completion_in_window_survives_atomic_aggregation_and_deletion(self, store):
        _, _, add = self.journey(store)
        add("check_started")
        early = run(store, store.run_retention(NOW + timedelta(minutes=20)))
        assert early.rolled_up_events == early.deleted_raw_events == 0
        assert self.raw(store) == {"denominator":1,"numerator":0}
        add("result_rendered", elapsed=29, supported_result=True, render_delivered=True)
        before = self.raw(store)
        assert before == {"denominator":1,"numerator":1}
        result = run(store, store.run_retention(NOW + timedelta(minutes=30)))
        assert result.rolled_up_events == result.deleted_raw_events == 3
        assert raw_count(store) == 0
        assert self.agg(store) == before
        assert run(store, store.primary_metric(NOW.replace(hour=0), NOW + timedelta(days=1))) == before
        assert run(store, store.run_retention(NOW + timedelta(minutes=35))).rolled_up_events == 0
        assert self.agg(store) == before

    def test_late_failure_in_window_cancels_previously_rendered_operation(self, store):
        _, _, add = self.journey(store)
        add("check_started")
        add("result_rendered", elapsed=2, supported_result=True, render_delivered=True)
        assert self.raw(store)["numerator"] == 1
        add("render_failed", elapsed=29)
        assert self.raw(store) == {"denominator":1,"numerator":0}
        run(store, store.run_retention(NOW + timedelta(minutes=31)))
        assert self.agg(store) == {"denominator":1,"numerator":0}
        assert raw_count(store) == 0

    def test_after_window_events_and_replayed_old_arrival_cannot_recreate_deleted_ids(self, store):
        lid, _, add = self.journey(store)
        add("check_started")
        assert not add("result_rendered", elapsed=30, supported_result=True, render_delivered=True)
        run(store, store.run_retention(NOW + timedelta(minutes=31)))
        assert not add("render_failed", elapsed=32)
        assert not ins(store, rec(NOW + timedelta(minutes=32), landing_id=lid,
                                 window_start_minute=store_mod.minute(NOW)))
        assert raw_count(store) == 0
        assert self.agg(store) == {"denominator":1,"numerator":0}

    def test_repeated_checks_count_one_landing_and_another_success_can_survive_one_failure(self, store):
        _, _, add = self.journey(store)
        add("check_started")
        add("result_rendered", supported_result=True, render_delivered=True)
        add("render_failed")
        second = uid()
        add("check_started", operation=second)
        add("result_rendered", operation=second, supported_result=True, render_delivered=True)
        add("result_rendered", operation=second, supported_result=True, render_delivered=True)
        assert self.raw(store) == {"denominator":1,"numerator":1}
        run(store, store.run_retention(NOW + timedelta(minutes=31)))
        assert self.agg(store) == {"denominator":1,"numerator":1}

    def test_unobserved_start_or_restored_link_does_not_complete_a_landing(self, store):
        lid, op, add = self.journey(store)
        add("result_rendered", supported_result=True, render_delivered=True)
        assert self.raw(store)["numerator"] == 0
        add("check_started")
        add("render_failed")
        restore_op = uid()
        add("check_started", operation=restore_op)
        assert ins(store, rec(NOW, landing_id=lid, operation_id=restore_op,
                             event="result_rendered", entry_mode="restored_link",
                             supported_result=True, render_delivered=True))
        assert self.raw(store)["numerator"] == 0

    def test_pilot_source_and_window_cannot_change_under_one_identifier(self, store):
        lid, _, _ = self.journey(store, pilot="cost")
        for changes in (dict(pilot_group="corsa"), dict(source_group="direct"),
                        dict(window_start_minute=store_mod.minute(NOW)-1)):
            base = dict(landing_id=lid,pilot_group="cost")
            base.update(changes)
            assert not ins(store, rec(NOW, **base))
        assert raw_count(store) == 1

    def test_duplicate_arrival_event_ids_and_concurrent_finalisers_count_once(self, store):
        lid, _, _ = self.journey(store)
        ins(store, rec(NOW, landing_id=lid))  # second event ID, same journey
        async def jobs():
            return await asyncio.gather(*(store.run_retention(NOW + timedelta(minutes=31)) for _ in range(3)))
        results = run(store, jobs())
        assert sum(r.deleted_raw_events for r in results) == 2
        assert self.agg(store) == {"denominator":1,"numerator":0}
        assert raw_count(store) == 0

    def test_pilot_dimensions_survive_deletion_and_paid_or_bot_traffic_is_excluded(self, store):
        self.journey(store,pilot="cost")
        self.journey(store,pilot="corsa")
        self.journey(store,source="direct")
        assert not ins(store,rec(NOW,source_group="paid_search"))
        ins(store,rec(NOW,is_bot=True))
        run(store, store.run_retention(NOW + timedelta(minutes=31)))
        assert self.agg(store) == {"denominator":2,"numerator":0}
        assert self.agg(store,"cost") == {"denominator":1,"numerator":0}
        rows = run(store,store.fetch_all("SELECT * FROM acquisition_v2_landing_daily"))
        assert all(r["metric_version"] == store_mod.METRIC_VERSION for r in rows)
        assert not any("id" in key or key in ("received_at","window_start_minute") for r in rows for key in r)

    def test_orphan_events_deleted_without_inventing_a_denominator(self,store):
        ins(store,rec(NOW,event="check_started",operation_id=uid(),entry_mode="fresh_check"))
        run(store,store.run_retention(NOW+timedelta(minutes=31)))
        assert raw_count(store)==0
        assert self.agg(store)=={"denominator":0,"numerator":0}

    def test_aggregate_expiry_and_utc_midnight_cohort(self,store):
        start=NOW.replace(hour=23,minute=55)
        self.journey(store,start=start)
        run(store,store.run_retention(start+timedelta(minutes=31)))
        assert self.agg(store)["denominator"]==1
        result=run(store,store.run_retention(start+timedelta(days=100)))
        assert result.deleted_aggregate_rows==2
        assert self.agg(store)=={"denominator":0,"numerator":0}

    def test_new_collection_stops_if_raw_deletion_is_overdue(self,store):
        self.journey(store)
        with pytest.raises(store_mod.RetentionOverdue):
            ins(store,rec(NOW+timedelta(minutes=36)))
        assert raw_count(store)==1
        run(store,store.run_retention(NOW+timedelta(minutes=36)))
        assert ins(store,rec(NOW+timedelta(minutes=36)))

    def test_future_start_and_naive_clock_are_refused(self,store):
        assert not ins(store,rec(NOW,window_start_minute=store_mod.minute(NOW)+1))
        with pytest.raises(ValueError):
            run(store,store.run_retention(NOW.replace(tzinfo=None)))
        assert store_mod.months_before(date(2024,5,31),3)==date(2024,2,29)


class TestBackgroundRetention:
    def test_not_started_when_no_store_is_configured(self, monkeypatch):
        monkeypatch.delenv("ACQUISITION_SQLITE_PATH", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        routes.set_store_for_tests(None)

        async def go():
            return routes.start_background_retention()

        assert asyncio.run(go()) is None

    def test_retention_runs_with_ingest_off_when_tables_exist_and_deletes(self, monkeypatch, tmp_path):
        # D-007.3: switching ingest off must never stop deletion.
        monkeypatch.setenv("ACQUISITION_INGEST_ENABLED", "")
        s = SqliteStore(str(tmp_path / "off.sqlite"))
        asyncio.run(s.ensure_schema())
        old_event = rec(NOW - timedelta(days=100))
        ins(s,old_event)
        routes.set_store_for_tests(s)
        try:
            res = asyncio.run(routes.run_retention_once(now=NOW))
        finally:
            routes.set_store_for_tests(None)
        assert res is not None and res.deleted_raw_events == 1
        assert asyncio.run(s.fetch_all("SELECT COUNT(*) AS n FROM acquisition_v2_events"))[0]["n"] == 0

    def test_background_task_runs_with_ingest_off_and_stops_cleanly(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ACQUISITION_INGEST_ENABLED", "")
        s = SqliteStore(str(tmp_path / "off2.sqlite"))
        asyncio.run(s.ensure_schema())
        asyncio.run(s.insert_event(rec(NOW - timedelta(days=400))))
        routes.set_store_for_tests(s)

        async def go():
            task = routes.start_background_retention(startup_delay=0.01, daily=3600, retry=3600)
            assert task is not None
            await asyncio.sleep(0.4)
            await routes.stop_background_retention()

        try:
            asyncio.run(go())
        finally:
            routes.set_store_for_tests(None)
        assert asyncio.run(s.fetch_all("SELECT COUNT(*) AS n FROM acquisition_v2_events"))[0]["n"] == 0

    def test_with_ingest_off_and_no_tables_nothing_is_created(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ACQUISITION_INGEST_ENABLED", "")
        path = str(tmp_path / "none.sqlite")
        s = SqliteStore(path)
        routes.set_store_for_tests(s)
        try:
            assert asyncio.run(routes.run_retention_once(now=NOW)) is None
        finally:
            routes.set_store_for_tests(None)
        assert not os.path.exists(path)

    def test_with_ingest_off_and_a_partial_schema_nothing_is_created(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ACQUISITION_INGEST_ENABLED", "")
        path = str(tmp_path / "partial.sqlite")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE acquisition_v2_events (id INTEGER)")
        conn.commit()
        conn.close()
        s = SqliteStore(path)
        assert asyncio.run(s.tables_exist()) is False
        routes.set_store_for_tests(s)
        try:
            assert asyncio.run(routes.run_retention_once(now=NOW)) is None
        finally:
            routes.set_store_for_tests(None)
        conn = sqlite3.connect(path)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert names == {"acquisition_v2_events"}

    def test_runs_in_background_without_blocking_and_stops_cleanly(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ACQUISITION_INGEST_ENABLED", "1")
        calls = []

        class Slow:
            backend = "slow"

            async def ensure_schema(self):
                pass

            async def run_retention(self, now):
                calls.append(now)
                await asyncio.sleep(0.2)
                return store_mod.RetentionResult()

        routes.set_store_for_tests(Slow())

        async def go():
            t0 = time.monotonic()
            task = routes.start_background_retention(startup_delay=0.01, daily=3600, retry=3600)
            started_in = time.monotonic() - t0
            await asyncio.sleep(0.05)
            assert calls, "retention should have started"
            await routes.stop_background_retention()
            return started_in, task.cancelled() or task.done()

        try:
            started_in, done = asyncio.run(go())
        finally:
            routes.set_store_for_tests(None)
        assert started_in < 0.05 and done

    def test_failure_is_logged_by_type_only_and_retried_later(self, monkeypatch, caplog):
        monkeypatch.setenv("ACQUISITION_INGEST_ENABLED", "1")
        caplog.set_level(logging.INFO)
        attempts = []

        class Failing:
            backend = "failing"

            async def ensure_schema(self):
                pass

            async def run_retention(self, now):
                attempts.append(1)
                raise ConnectionError("postgres://user:secretpw@host/db unreachable")

        routes.set_store_for_tests(Failing())

        async def go():
            routes.start_background_retention(startup_delay=0.0, daily=3600, retry=0.05)
            await asyncio.sleep(0.2)
            await routes.stop_background_retention()

        try:
            asyncio.run(go())
        finally:
            routes.set_store_for_tests(None)
        assert len(attempts) >= 2
        assert "secretpw" not in caplog.text
        assert "acquisition_retention_failed exception_type=ConnectionError" in caplog.text

    def test_main_lifespan_wires_the_job(self):
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")).read()
        assert "acquisition_routes.start_background_retention()" in src
        assert "await acquisition_routes.stop_background_retention()" in src
        assert "register_acquisition_routes(app, limiter)" in src


class TestPostgresWorkerSafety:
    def test_second_worker_skips_while_the_advisory_lock_is_held(self, store):
        if store.backend != "postgres":
            pytest.skip("advisory lock is Postgres-specific (SQLite uses BEGIN IMMEDIATE)")
        ins(store, rec(NOW - timedelta(days=3)))

        async def go():
            pool = await store._pool()
            async with pool.acquire() as holder:
                async with holder.transaction():
                    await holder.execute("SELECT pg_advisory_xact_lock($1)", store_mod._RETENTION_LOCK_KEY)
                    blocked = await store.run_retention(NOW)
            free = await store.run_retention(NOW)
            return blocked, free

        blocked, free = run(store, go())
        assert blocked.skipped_locked and blocked.rolled_up_events == 0
        assert not free.skipped_locked and free.rolled_up_events == 1
        assert sum(a["count"] for a in daily(store)) == 1


class TestMigrationScript:
    def test_dry_run_prints_ddl_without_connecting(self, capsys, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "add_acquisition_tables",
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations",
                         "add_acquisition_tables.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.run(mod.forward_statements(), dry_run=True)
        out = capsys.readouterr().out
        assert "CREATE TABLE IF NOT EXISTS acquisition_v2_events" in out
        assert "CREATE TABLE IF NOT EXISTS acquisition_v2_daily" in out
        assert "event_id UUID PRIMARY KEY" in out
        for banned in ("ip ", "user_agent", "referer", "url "):
            assert banned not in out.lower()
        assert "CREATE TABLE IF NOT EXISTS acquisition_v2_landing_daily" in out
        assert mod.rollback_statements() == ["DROP TABLE IF EXISTS acquisition_v2_landing_daily",
                                             "DROP TABLE IF EXISTS acquisition_v2_daily",
                                             "DROP TABLE IF EXISTS acquisition_v2_events"]


class TestSchemaEnsure:
    def test_schema_is_ensured_once_per_store_object_not_per_object_address(self):
        calls = []

        class Fake:
            def __init__(self, name):
                self.name = name

            async def ensure_schema(self):
                calls.append(self.name)

        async def go():
            a = Fake("a")
            await routes._ensure_schema_once(a)
            await routes._ensure_schema_once(a)
            del a  # its address may be reused by the next object; the flag must not follow it
            b = Fake("b")
            await routes._ensure_schema_once(b)
            await routes._ensure_schema_once(b)

        asyncio.run(go())
        assert calls == ["a", "b"]
