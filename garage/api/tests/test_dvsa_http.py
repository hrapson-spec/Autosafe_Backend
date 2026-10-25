import httpx
import pytest

from garage.api.dvsa import (DvsaNotFound, DvsaPermanentError, DvsaTransientError, HttpDvsaClient, parse_api_response)

BODY = {"registration": "AB12CDE", "make": "FORD", "model": "FOCUS", "fuelType": "Petrol", "engineSize": "1596",
        "firstUsedDate": "2019-03-01", "registrationDate": "2019-03-01", "manufactureDate": "2019-02-01",
        "hasOutstandingRecall": "No",
        "motTests": [
            {"completedDate": "2023-03-08T09:30:00.000Z", "testResult": "FAILED", "odometerValue": "30,500",
             "odometerUnit": "MI", "motTestNumber": "123456789012", "dataSource": "DVSA",
             "defects": [{"text": "Brake disc worn", "type": "MAJOR", "dangerous": False}]},
            {"completedDate": "2022-03-10T10:00:00.000Z", "testResult": "PASSED", "odometerValue": "21000",
             "odometerUnit": "MI", "dataSource": "DVSA", "rfrAndComments": [{"text": "Tyre", "type": "ADVISORY"}]},
            {"completedDate": "2021-01-01T10:00:00.000Z", "testResult": "WEIRD", "dataSource": "CVS"},
        ]}


def client(handler):
    hits = []

    def wrapped(req: httpx.Request):
        hits.append(req)
        return handler(req)
    c = HttpDvsaClient("cid", "secret", "https://token.test/oauth", "KEY", http=httpx.Client(transport=httpx.MockTransport(wrapped)))
    return c, hits


def ok_handler(status=200, body=BODY):
    def h(req):
        if req.url.host == "token.test":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(status, json=body)
    return h


def test_parse_and_headers_and_single_request():
    c, hits = client(ok_handler())
    h = c.fetch_history("AB12CDE")
    api = [r for r in hits if r.url.host != "token.test"]
    assert len(api) == 1                                                       # no hidden retries (throttle counts requests)
    assert api[0].headers["authorization"] == "Bearer tok" and api[0].headers["x-api-key"] == "KEY"
    assert str(api[0].url).endswith("/v1/trade/vehicles/registration/AB12CDE")
    assert [t.result for t in h.tests] == ["PASSED", "FAILED"]                  # sorted oldest first; odd record dropped
    assert h.tests[1].odometer_value == 30500 and h.tests[1].defects[0]["type"] == "MAJOR"
    assert h.tests[0].defects[0]["text"] == "Tyre"                              # rfrAndComments fallback
    assert str(h.tests[1].test_date) == "2023-03-08" and h.first_used_date.isoformat() == "2019-03-01"
    c.fetch_history("AB12CDE")
    assert len([r for r in hits if r.url.host == "token.test"]) == 1           # token reused


@pytest.mark.parametrize("status,exc", [(404, DvsaNotFound), (429, DvsaTransientError), (500, DvsaTransientError),
                                        (503, DvsaTransientError), (401, DvsaTransientError), (403, DvsaPermanentError),
                                        (400, DvsaPermanentError)])
def test_status_mapping(status, exc):
    c, _ = client(ok_handler(status, {}))
    with pytest.raises(exc):
        c.fetch_history("AB12CDE")


def test_network_error_is_transient_and_token_failure_mapped():
    def boom(req):
        raise httpx.ConnectTimeout("t")
    c, _ = client(boom)
    with pytest.raises(DvsaTransientError):
        c.fetch_history("AB12CDE")
    c2, _ = client(lambda req: httpx.Response(401, json={}))
    with pytest.raises(DvsaPermanentError):
        c2.fetch_history("AB12CDE")


def test_empty_list_is_not_found_and_garbage_is_transient():
    with pytest.raises(DvsaNotFound):
        parse_api_response("AB12CDE", [])
    c, _ = client(ok_handler(200, {"motTests": [{"completedDate": "not-a-date", "testResult": "PASSED"}]}))
    with pytest.raises(DvsaTransientError):
        c.fetch_history("AB12CDE")


def test_from_env_requires_credentials(monkeypatch):
    for k in ("DVSA_CLIENT_ID", "DVSA_CLIENT_SECRET", "DVSA_TOKEN_URL", "DVSA_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(DvsaPermanentError):
        HttpDvsaClient.from_env()
