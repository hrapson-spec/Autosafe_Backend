"""
main.get_real_client_ip is the key_func behind the slowapi Limiter that
protects, among other things, the DVSA-backed POST /api/v2/reports route
(20/minute per main.py's rate-limit decorators). Its own docstring/comment
previously claimed it takes the FIRST (leftmost) X-Forwarded-For entry
because "an attacker can add fake IPs to the END" -- but the code actually
took the LAST (rightmost) entry (`forwarded_for.split(",")[-1]`).

That contradiction is the real defect: under this app's documented
single-hop deployment (Railway is the only reverse proxy in front of the
container -- no CDN/WAF is documented anywhere in this repo, see
CLAUDE.md "Deployment: Railway.app"), a reverse proxy APPENDS the IP it
observed to whatever X-Forwarded-For value it received, so the *rightmost*
entry is the one hop of the header nobody but the proxy could have
written, and the *leftmost* entry is exactly the attacker-supplied part a
client can set to anything. Taking the first entry -- what the old comment
said the code did -- would let any client bypass the rate limit's IP
bucketing simply by sending a fresh fake X-Forwarded-For value on every
request; taking the last entry (what the code actually does) does not.

These tests pin the actual, verified behavior so a future edit that
"fixes" the code to match the old (backwards) comment is caught
immediately, and demonstrate that varying attacker-controlled leading
entries cannot change the extracted IP.
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import get_real_client_ip  # noqa: E402


class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeRequest:
    def __init__(self, headers=None, client_host=None):
        self._headers = headers or {}
        self.client = _FakeClient(client_host) if client_host else None

    @property
    def headers(self):
        return self

    def get(self, key, default=None):
        # Case-insensitive header lookup, matching Starlette's Headers.get.
        for k, v in self._headers.items():
            if k.lower() == key.lower():
                return v
        return default


def test_single_hop_xff_is_used():
    request = _FakeRequest(headers={"X-Forwarded-For": "203.0.113.7"})
    assert get_real_client_ip(request) == "203.0.113.7"


def test_takes_rightmost_entry_not_leftmost():
    # "client, proxy" shape: Railway (the one trusted hop) appended the
    # real client IP after whatever the client itself sent as the first
    # value in the header (empty here to isolate the fake-vs-real hop).
    request = _FakeRequest(headers={"X-Forwarded-For": "198.51.100.9, 203.0.113.7"})
    assert get_real_client_ip(request) == "203.0.113.7"


def test_attacker_controlled_leading_entries_do_not_change_extracted_ip():
    """A client can put anything before the proxy-appended hop. Since the
    proxy always appends its own observed IP last, the extracted IP must
    stay pinned to that last entry no matter what leading junk the client
    sends -- this is what actually defeats a rate-limit-bucket-hopping
    attempt, not which end 'looks' safer in prose."""
    real_ip = "203.0.113.7"
    for spoofed_prefix in ["1.1.1.1", "999.999.999.999", "not-an-ip", "10.0.0.1, 10.0.0.2"]:
        request = _FakeRequest(headers={"X-Forwarded-For": f"{spoofed_prefix}, {real_ip}"})
        assert get_real_client_ip(request) == real_ip, (
            f"spoofed prefix {spoofed_prefix!r} changed the extracted IP -- "
            "an attacker could rotate this to evade per-IP rate limiting"
        )


def test_falls_back_to_direct_connection_when_header_absent():
    request = _FakeRequest(headers={}, client_host="192.0.2.5")
    assert get_real_client_ip(request) == "192.0.2.5"


def test_unknown_when_no_header_and_no_client():
    request = _FakeRequest(headers={}, client_host=None)
    assert get_real_client_ip(request) == "unknown"
