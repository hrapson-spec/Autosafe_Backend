"""DVSA MOT-history interface, DTOs, fixture-backed fake, HTTP client and global throttle.

No real call is made anywhere in tests. The HTTP client is tested against httpx.MockTransport.
"""
from __future__ import annotations

import json
import logging
import pathlib
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Protocol

import httpx

from .timeutil import london_date

log = logging.getLogger("garage.dvsa")
FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures" / "dvsa"


class DvsaNotFound(Exception):
    """Registration unknown to DVSA (HTTP 404)."""


class DvsaTransientError(Exception):
    """Timeout, 429, 5xx, token refresh: worth retrying."""


class DvsaPermanentError(Exception):
    """Credentials or request problem: retrying will not help."""


@dataclass(frozen=True)
class DvsaTest:
    completed_at: datetime            # aware, UTC
    result: str                       # PASSED | FAILED (API spec enum, T1 Q2)
    test_number: str | None = None
    odometer_value: int | None = None
    odometer_unit: str | None = None
    expiry_date: date | None = None
    defects: tuple = ()

    @property
    def test_date(self) -> date:
        return london_date(self.completed_at)

    def to_json(self) -> dict:
        return {"completed_at": self.completed_at.isoformat(), "result": self.result,
                "test_number": self.test_number, "odometer_value": self.odometer_value,
                "odometer_unit": self.odometer_unit,
                "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
                "defects": list(self.defects)}

    @classmethod
    def from_json(cls, d: dict) -> "DvsaTest":
        return cls(completed_at=_parse_dt(d["completed_at"]), result=d["result"],
                   test_number=d.get("test_number"), odometer_value=d.get("odometer_value"),
                   odometer_unit=d.get("odometer_unit"),
                   expiry_date=date.fromisoformat(d["expiry_date"]) if d.get("expiry_date") else None,
                   defects=tuple(d.get("defects") or ()))


@dataclass(frozen=True)
class DvsaHistory:
    registration: str
    make: str | None = None
    model: str | None = None
    fuel_type: str | None = None
    first_used_date: date | None = None
    registration_date: date | None = None
    manufacture_date: date | None = None
    engine_size: str | None = None
    tests: tuple = ()

    def before(self, cutoff_date: date, cutoff_at: datetime) -> "DvsaHistory":
        """Only tests strictly before both the cutoff date (London) and instant (R10)."""
        kept = tuple(t for t in self.tests if t.test_date < cutoff_date and t.completed_at < cutoff_at)
        return DvsaHistory(**{**self.__dict__, "tests": kept})

    def to_json(self) -> dict:
        return {"registration": self.registration, "make": self.make, "model": self.model,
                "fuel_type": self.fuel_type, "engine_size": self.engine_size,
                "first_used_date": _d(self.first_used_date), "registration_date": _d(self.registration_date),
                "manufacture_date": _d(self.manufacture_date),
                "tests": [t.to_json() for t in self.tests]}

    @classmethod
    def from_json(cls, d: dict) -> "DvsaHistory":
        return cls(registration=d["registration"], make=d.get("make"), model=d.get("model"),
                   fuel_type=d.get("fuel_type"), engine_size=d.get("engine_size"),
                   first_used_date=_pd(d.get("first_used_date")), registration_date=_pd(d.get("registration_date")),
                   manufacture_date=_pd(d.get("manufacture_date")),
                   tests=tuple(DvsaTest.from_json(t) for t in d.get("tests", [])))


def _d(x):
    return x.isoformat() if x else None


def _pd(x):
    return date.fromisoformat(x) if x else None


def _parse_dt(s: str) -> datetime:
    s = s.strip().replace("Z", "+00:00")
    if len(s) == 10:
        s += "T00:00:00+00:00"
    dt = datetime.fromisoformat(s.replace(".", "-", 2) if s[4:5] == "." else s)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


class DvsaClient(Protocol):
    def fetch_history(self, registration: str) -> DvsaHistory: ...


# --------------------------------------------------------------------- fake
class FakeDvsaClient:
    """Fixture-backed fake. Looks in `histories` first, then FIXTURE_DIR/<REG>.json.

    A fixture file may be {"__error": "transient"|"permanent"|"not_found"} to simulate upstream trouble.
    `errors[reg]` (an exception instance or class) overrides everything, and may be a list consumed
    one call at a time (the last entry repeats).
    """

    def __init__(self, histories: dict | None = None, errors: dict | None = None,
                 fixture_dir: pathlib.Path | None = FIXTURE_DIR):
        self.histories = dict(histories or {})
        self.errors = dict(errors or {})
        self.fixture_dir = fixture_dir
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def set_history(self, reg: str, history: DvsaHistory) -> None:
        self.histories[reg] = history

    def fetch_history(self, registration: str) -> DvsaHistory:
        with self._lock:
            self.calls.append(registration)
            n = self.calls.count(registration)
        err = self.errors.get(registration)
        if isinstance(err, list):
            err = err[min(n, len(err)) - 1]
        if err is not None:
            raise err() if isinstance(err, type) else err
        if registration in self.histories:
            return self.histories[registration]
        if self.fixture_dir is not None:
            f = self.fixture_dir / f"{registration}.json"
            if f.exists():
                d = json.loads(f.read_text())
                if "__error" in d:
                    raise {"transient": DvsaTransientError, "permanent": DvsaPermanentError,
                           "not_found": DvsaNotFound}[d["__error"]](d["__error"])
                return DvsaHistory.from_json(d)
        raise DvsaNotFound(registration)


# --------------------------------------------------------------------- http
class HttpDvsaClient:
    """Real client (credentials from env, same names as dvsa_client.py:158-170).

    One HTTP request per call: no internal retry and no cache (outcome checks must see fresh data and the
    throttle must count every request). Retries are the job queue's business. Endpoint, headers and 404
    handling follow dvsa_client.py:372-384 (x-api-key + bearer, URL, 404) and :479-483 (rfrAndComments fallback). Never exercised against the real API here.
    """
    BASE_URL = "https://history.mot.api.gov.uk/v1"

    def __init__(self, client_id: str, client_secret: str, token_url: str, api_key: str,
                 scope: str = "https://tapi.dvsa.gov.uk/.default", http: httpx.Client | None = None,
                 timeout: float = 10.0):
        self.client_id, self.client_secret, self.token_url = client_id, client_secret, token_url
        self.api_key, self.scope = api_key, scope
        self._http = http or httpx.Client(timeout=timeout)
        self._token: str | None = None
        self._expires = 0.0

    @classmethod
    def from_env(cls) -> "HttpDvsaClient":
        import os
        e = os.environ.get
        vals = [e("DVSA_CLIENT_ID"), e("DVSA_CLIENT_SECRET"), e("DVSA_TOKEN_URL"), e("DVSA_API_KEY")]
        if not all(vals):
            raise DvsaPermanentError("DVSA credentials not configured")
        return cls(*vals, scope=e("DVSA_SCOPE") or "https://tapi.dvsa.gov.uk/.default")

    def _access_token(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        try:
            r = self._http.post(self.token_url, data={
                "grant_type": "client_credentials", "client_id": self.client_id,
                "client_secret": self.client_secret, "scope": self.scope})
        except httpx.HTTPError as exc:
            raise DvsaTransientError("token request failed") from exc
        if r.status_code >= 500 or r.status_code == 429:
            raise DvsaTransientError(f"token endpoint {r.status_code}")
        if r.status_code != 200:
            raise DvsaPermanentError(f"token endpoint {r.status_code}")
        data = r.json()
        self._token, self._expires = data["access_token"], time.time() + int(data.get("expires_in", 3600))
        return self._token

    def fetch_history(self, registration: str) -> DvsaHistory:
        token = self._access_token()
        try:
            r = self._http.get(f"{self.BASE_URL}/trade/vehicles/registration/{registration}",
                               headers={"Authorization": f"Bearer {token}", "x-api-key": self.api_key})
        except httpx.HTTPError as exc:
            raise DvsaTransientError("request failed") from exc
        if r.status_code == 404:
            raise DvsaNotFound(registration)
        if r.status_code == 401:
            self._token = None
            raise DvsaTransientError("401: token refreshed")
        if r.status_code == 429 or r.status_code >= 500:
            raise DvsaTransientError(f"status {r.status_code}")
        if r.status_code != 200:
            raise DvsaPermanentError(f"status {r.status_code}")
        try:
            return parse_api_response(registration, r.json())
        except (ValueError, KeyError, TypeError) as exc:
            raise DvsaTransientError("unparseable response") from exc


def parse_api_response(registration: str, data) -> DvsaHistory:
    if isinstance(data, list):
        if not data:
            raise DvsaNotFound(registration)
        data = data[0]
    tests = []
    for t in data.get("motTests") or []:
        result = t.get("testResult")
        if result not in ("PASSED", "FAILED") or not t.get("completedDate"):
            continue  # CVS/other record shapes are outside the Class 4 pilot
        defects = t.get("defects")
        if defects is None:
            defects = t.get("rfrAndComments") or []
        odo = t.get("odometerValue")
        try:
            odo = int(str(odo).replace(",", "").strip()) if odo else None
        except ValueError:
            odo = None
        exp = t.get("expiryDate")
        tests.append(DvsaTest(completed_at=_parse_dt(t["completedDate"]), result=result,
                              test_number=t.get("motTestNumber"), odometer_value=odo,
                              odometer_unit=t.get("odometerUnit"),
                              expiry_date=date.fromisoformat(exp[:10]) if exp else None,
                              defects=tuple({"type": d.get("type"), "text": d.get("text"),
                                             "dangerous": d.get("dangerous")} for d in defects)))
    tests.sort(key=lambda x: x.completed_at)
    return DvsaHistory(
        registration=registration, make=data.get("make"), model=data.get("model"),
        fuel_type=data.get("fuelType"), engine_size=data.get("engineSize"),
        first_used_date=_pd((data.get("firstUsedDate") or "")[:10] or None),
        registration_date=_pd((data.get("registrationDate") or "")[:10] or None),
        manufacture_date=_pd((data.get("manufactureDate") or "")[:10] or None),
        tests=tuple(tests))


# ----------------------------------------------------------------- throttle
class DbThrottle:
    """Global DVSA throttle across all workers/processes: at most `rps` requests per second.

    Every caller reserves a start slot from one Postgres row (row-locked UPDATE), slots are
    1/rps + 5 ms apart, then sleeps outside the transaction until its slot. Any closed one-second
    interval therefore holds at most `rps` slots. Pacing is best-effort if a thread is descheduled
    after waking.
    """

    def __init__(self, db, rps: int = 5, sleep: Callable[[float], None] = time.sleep):
        self.db, self.rps, self.sleep = db, rps, sleep
        self.spacing = 1.0 / rps + 0.005

    def reserve_slot(self):
        """Reserve a slot; returns (seconds to wait from now, slot instant)."""
        with self.db.tx() as cur:
            cur.execute(
                """WITH c AS (SELECT next_slot, clock_timestamp() AS now_ts FROM dvsa_throttle
                              WHERE id = 1 FOR UPDATE)
                   UPDATE dvsa_throttle t
                      SET next_slot = GREATEST(c.now_ts, c.next_slot) + make_interval(secs => %s)
                     FROM c WHERE t.id = 1
                   RETURNING EXTRACT(EPOCH FROM GREATEST(c.now_ts, c.next_slot) - c.now_ts) AS wait,
                             GREATEST(c.now_ts, c.next_slot) AS slot""", (self.spacing,))
            r = cur.fetchone()
        return float(r["wait"]), r["slot"]

    def reserve(self) -> float:
        return self.reserve_slot()[0]

    def acquire(self) -> None:
        wait = self.reserve()
        if wait > 0:
            self.sleep(wait)


class ThrottledDvsa:
    def __init__(self, inner: DvsaClient, throttle: DbThrottle):
        self.inner, self.throttle = inner, throttle

    def fetch_history(self, registration: str) -> DvsaHistory:
        self.throttle.acquire()
        return self.inner.fetch_history(registration)
