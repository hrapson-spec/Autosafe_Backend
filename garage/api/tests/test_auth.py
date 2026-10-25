"""Sign-in links, sessions, invites."""
import threading


from garage.api.security import hash_token
from .conftest import (add_staff_and_sign_in, make_invite, make_workshop, new_client, sql,
                       token_from_mail)

V = "/api/garage/v1"


def test_signup_requires_valid_single_use_invite(env):
    c = new_client(env)
    body = {"workshop_name": "A", "email": "a@a.example"}
    assert c.post(f"{V}/auth/signup", json={**body, "invite_code": "GRG-NOPE-NOPE-NOPE-NOPE"}).status_code == 403
    code = make_invite(env)
    assert c.post(f"{V}/auth/signup", json={**body, "invite_code": code}).status_code == 202
    r = c.post(f"{V}/auth/signup", json={**body, "email": "b@b.example", "invite_code": code})
    assert r.status_code == 403 and r.json()["error"]["code"] == "invalid_invite"      # used
    code2 = make_invite(env)
    sql(env, "UPDATE invites SET expires_at = now() - interval '1 minute' WHERE consumed_at IS NULL")
    assert c.post(f"{V}/auth/signup", json={**body, "email": "c@c.example", "invite_code": code2}).status_code == 403
    # only a hash of the code is stored
    assert not sql(env, "SELECT 1 FROM invites WHERE code_hash = %s", (code,))


def test_signup_gives_nothing_until_link_used(env):
    c = new_client(env)
    c.post(f"{V}/auth/signup", json={"invite_code": make_invite(env), "workshop_name": "A", "email": "a@a.example"})
    assert c.get(f"{V}/me").status_code == 401


def test_link_single_use_and_stored_hashed(env):
    make_workshop(env, "A", "a@a.example")                      # consumed once by the helper
    tok = token_from_mail(env, "a@a.example")
    c2 = new_client(env)
    r = c2.post(f"{V}/auth/links/{tok}/consume")
    assert r.status_code == 410 and r.json()["error"]["code"] == "link_invalid"
    assert c2.get(f"{V}/me").status_code == 401
    assert not sql(env, "SELECT 1 FROM login_tokens WHERE token_hash = %s", (tok,))
    assert sql(env, "SELECT 1 FROM login_tokens WHERE token_hash = %s", (hash_token(tok),))


def test_link_expires_after_15_minutes(env):
    c = new_client(env)
    make_workshop(env, "A", "a@a.example")
    c.post(f"{V}/auth/login-links", json={"email": "a@a.example"})
    tok = token_from_mail(env, "a@a.example")
    row = sql(env, "SELECT expires_at - created_at AS ttl FROM login_tokens WHERE token_hash = %s", (hash_token(tok),))
    assert row[0]["ttl"].total_seconds() == 15 * 60
    sql(env, "UPDATE login_tokens SET expires_at = now() - interval '1 second' WHERE token_hash = %s", (hash_token(tok),))
    assert c.get(f"{V}/auth/links/{tok}").status_code == 410
    assert c.post(f"{V}/auth/links/{tok}/consume").status_code == 410


def test_prefetch_get_never_consumes(env):
    c = make_workshop(env, "A", "a@a.example")
    other = new_client(env)
    other.post(f"{V}/auth/login-links", json={"email": "a@a.example"})
    tok = token_from_mail(env, "a@a.example")
    scanner = new_client(env)
    for _ in range(3):                                        # mail scanner / browser prefetch
        r = scanner.get(f"{V}/auth/links/{tok}", headers={"Accept": "text/html"})
        assert r.status_code == 200 and "<form" in r.text and 'method="post"' in r.text
        assert r.headers["cache-control"] == "no-store" and r.headers["referrer-policy"] == "no-referrer"
    assert scanner.get(f"{V}/me").status_code == 401           # GET opened no session
    assert sql(env, "SELECT consumed_at FROM login_tokens WHERE token_hash = %s", (hash_token(tok),))[0]["consumed_at"] is None
    assert other.post(f"{V}/auth/links/{tok}/consume").status_code == 200   # the human's POST still works
    assert scanner.get(f"{V}/auth/links/{tok}").status_code == 410


def test_concurrent_consume_exactly_one_wins(env):
    make_workshop(env, "A", "a@a.example")
    new_client(env).post(f"{V}/auth/login-links", json={"email": "a@a.example"})
    tok = token_from_mail(env, "a@a.example")
    codes = []
    clients = [new_client(env) for _ in range(8)]

    def go(cl):
        codes.append(cl.post(f"{V}/auth/links/{tok}/consume").status_code)
    ts = [threading.Thread(target=go, args=(cl,)) for cl in clients]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(codes) == [200] + [410] * 7


def test_consume_sets_secure_cookie_and_14_day_session(env):
    c = new_client(env)
    c.post(f"{V}/auth/signup", json={"invite_code": make_invite(env), "workshop_name": "A", "email": "a@a.example"})
    tok = token_from_mail(env, "a@a.example")
    r = c.post(f"{V}/auth/links/{tok}/consume")
    sc = r.headers["set-cookie"]
    assert "__Host-garage_session=" in sc and "HttpOnly" in sc and "Secure" in sc and "SameSite=lax" in sc
    assert "Max-Age=1209600" in sc and "Path=/" in sc
    ttl = sql(env, "SELECT expires_at - created_at AS t FROM sessions")[0]["t"]
    assert ttl.days == 14
    # HTML flow redirects to the app (303)
    c2 = new_client(env)
    c2.post(f"{V}/auth/login-links", json={"email": "a@a.example"})
    tok2 = token_from_mail(env, "a@a.example")
    r = c2.post(f"{V}/auth/links/{tok2}/consume", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303


def test_login_link_does_not_reveal_accounts_and_is_rate_limited(env):
    make_workshop(env, "A", "a@a.example")
    c = new_client(env)
    known = c.post(f"{V}/auth/login-links", json={"email": "a@a.example"})
    unknown = c.post(f"{V}/auth/login-links", json={"email": "nobody@a.example"})
    assert known.status_code == unknown.status_code == 202 and known.json() == unknown.json()
    assert not [m for m in env.mail.outbox if m.to == "nobody@a.example"]
    for _ in range(10):
        c.post(f"{V}/auth/login-links", json={"email": "a@a.example"})
    # 1 signup link + up to 5/hour total
    assert len(sql(env, "SELECT 1 FROM login_tokens")) == 5


def test_session_logout_and_owner_revocation(env):
    owner = make_workshop(env, "A", "o@a.example")
    staff = add_staff_and_sign_in(env, owner, "s@a.example")
    assert staff.get(f"{V}/me").status_code == 200
    # staff sees only their own session; owner sees both
    assert {s["email"] for s in staff.get(f"{V}/sessions").json()["items"]} == {"s@a.example"}
    sess = owner.get(f"{V}/sessions").json()["items"]
    assert {s["email"] for s in sess} == {"o@a.example", "s@a.example"}
    staff_sid = next(s["id"] for s in sess if s["email"] == "s@a.example")
    assert owner.delete(f"{V}/sessions/{staff_sid}").status_code == 204
    r = staff.get(f"{V}/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthenticated"
    # logout revokes server-side: replaying the old cookie fails
    cookie = owner.cookies.get("__Host-garage_session")
    assert owner.post(f"{V}/auth/logout").status_code == 204
    replay = new_client(env)
    replay.cookies.set("__Host-garage_session", cookie)
    assert replay.get(f"{V}/me").status_code == 401


def test_session_expires_after_14_days(env):
    c = make_workshop(env, "A", "o@a.example")
    sql(env, "UPDATE sessions SET expires_at = now() - interval '1 second'")
    assert c.get(f"{V}/me").status_code == 401


def test_removing_staff_revokes_their_sessions_and_blocks_new_links(env):
    owner = make_workshop(env, "A", "o@a.example")
    staff = add_staff_and_sign_in(env, owner, "s@a.example")
    mid = next(m["membership_id"] for m in owner.get(f"{V}/staff").json()["items"] if m["email"] == "s@a.example")
    assert owner.delete(f"{V}/staff/{mid}").status_code == 204
    assert staff.get(f"{V}/me").status_code == 401
    n = len(env.mail.outbox)
    new_client(env).post(f"{V}/auth/login-links", json={"email": "s@a.example"})
    assert len(env.mail.outbox) == n                           # no new link for a removed member


def test_csrf_cross_site_blocked(env):
    c = make_workshop(env, "A", "o@a.example")
    r = c.post(f"{V}/auth/logout", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_blocked"
    r = c.post(f"{V}/imports/preview", headers={"Origin": "", "Sec-Fetch-Site": "cross-site"},
               json={"format": "manual", "rows": []})
    assert r.status_code == 403
    assert c.get(f"{V}/me").status_code == 200                 # reads unaffected
