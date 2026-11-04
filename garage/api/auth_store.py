"""Identity and authentication queries (pre-scope): invites, users, sign-in links, sessions.

Time is taken from the database clock (now()) so expiry is decided in one place.
"""
from __future__ import annotations

from .security import hash_invite, hash_token, new_invite_code, new_token


def create_invite(cur, note: str | None, ttl_days: int) -> str:
    code = new_invite_code()
    cur.execute("INSERT INTO invites (code_hash, note, expires_at) VALUES (%s, %s, now() + make_interval(days => %s))",
                (hash_invite(code), note, ttl_days))
    return code


def _issue_login_token(cur, user_id: str, workshop_id: str, ttl_minutes: int) -> str:
    token = new_token()
    cur.execute("INSERT INTO login_tokens (token_hash, user_id, workshop_id, expires_at) "
                "VALUES (%s, %s, %s, now() + make_interval(mins => %s))",
                (hash_token(token), user_id, workshop_id, ttl_minutes))
    return token


def signup(cur, invite_code: str, workshop_name: str, email: str, ttl_minutes: int) -> str | None:
    """Returns a raw sign-in token for the new owner, or None if the invite is unusable."""
    cur.execute("SELECT id FROM invites WHERE code_hash = %s AND consumed_at IS NULL AND expires_at > now() "
                "FOR UPDATE", (hash_invite(invite_code),))
    inv = cur.fetchone()
    if inv is None:
        return None
    cur.execute("INSERT INTO workshops (name) VALUES (%s) RETURNING id", (workshop_name,))
    wid = cur.fetchone()["id"]
    cur.execute("INSERT INTO users (email) VALUES (%s) ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email "
                "RETURNING id", (email,))
    uid = cur.fetchone()["id"]
    cur.execute("INSERT INTO memberships (workshop_id, user_id, role) VALUES (%s, %s, 'owner')", (wid, uid))
    cur.execute("UPDATE invites SET consumed_at = now(), consumed_by_workshop_id = %s WHERE id = %s", (wid, inv["id"]))
    return _issue_login_token(cur, uid, wid, ttl_minutes)


def request_login_token(cur, email: str, workshop_id: str | None, ttl_minutes: int, per_hour: int) -> str | None:
    """A token for an active membership of this email, or None (unknown email, rate limited)."""
    cur.execute(
        """SELECT m.workshop_id, m.user_id FROM memberships m JOIN users u ON u.id = m.user_id
            WHERE u.email = %s AND m.status = 'active' AND (%s::uuid IS NULL OR m.workshop_id = %s::uuid)
            ORDER BY m.created_at DESC LIMIT 1""", (email, workshop_id, workshop_id))
    m = cur.fetchone()
    if m is None:
        return None
    cur.execute("SELECT count(*) AS n FROM login_tokens WHERE user_id = %s AND created_at > now() - interval '1 hour'",
                (m["user_id"],))
    if cur.fetchone()["n"] >= per_hour:
        return None
    return _issue_login_token(cur, m["user_id"], m["workshop_id"], ttl_minutes)


def issue_login_token_for(cur, user_id: str, workshop_id: str, ttl_minutes: int) -> str:
    return _issue_login_token(cur, user_id, workshop_id, ttl_minutes)


def peek_token(cur, token: str) -> bool:
    """True if the link is currently usable. Read-only (the GET confirm page must never consume)."""
    cur.execute("SELECT 1 FROM login_tokens WHERE token_hash = %s AND consumed_at IS NULL AND expires_at > now()",
                (hash_token(token),))
    return cur.fetchone() is not None


def consume_token(cur, token: str, session_days: int):
    """Atomically burn the link and open a session. Returns (session_token, session_id) or None."""
    cur.execute("UPDATE login_tokens SET consumed_at = now() WHERE token_hash = %s AND consumed_at IS NULL "
                "AND expires_at > now() RETURNING user_id, workshop_id", (hash_token(token),))
    t = cur.fetchone()
    if t is None:
        return None
    cur.execute("SELECT id FROM memberships WHERE workshop_id = %s AND user_id = %s AND status = 'active'",
                (t["workshop_id"], t["user_id"]))
    m = cur.fetchone()
    if m is None:
        return None
    stok = new_token()
    cur.execute("INSERT INTO sessions (token_hash, user_id, workshop_id, membership_id, expires_at) "
                "VALUES (%s, %s, %s, %s, now() + make_interval(days => %s)) RETURNING id",
                (hash_token(stok), t["user_id"], t["workshop_id"], m["id"], session_days))
    return stok, cur.fetchone()["id"]


def resolve_session(cur, session_token: str):
    """Valid = unrevoked, unexpired, membership still active. Returns a row or None."""
    cur.execute(
        """SELECT s.id AS session_id, s.user_id, s.workshop_id, s.membership_id, m.role, u.email, w.name AS workshop_name
             FROM sessions s
             JOIN memberships m ON m.workshop_id = s.workshop_id AND m.id = s.membership_id
             JOIN users u ON u.id = s.user_id
             JOIN workshops w ON w.id = s.workshop_id
            WHERE s.token_hash = %s AND s.revoked_at IS NULL AND s.expires_at > now() AND m.status = 'active'""",
        (hash_token(session_token),))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE sessions SET last_seen_at = now() WHERE id = %s AND "
                    "(last_seen_at IS NULL OR last_seen_at < now() - interval '5 minutes')", (row["session_id"],))
    return row


def revoke_session_token(cur, session_token: str) -> None:
    cur.execute("UPDATE sessions SET revoked_at = now(), revoked_reason = 'logout' WHERE token_hash = %s "
                "AND revoked_at IS NULL", (hash_token(session_token),))


def memberships_of_user(cur, user_id: str):
    cur.execute("SELECT m.workshop_id, w.name AS workshop_name, m.role FROM memberships m "
                "JOIN workshops w ON w.id = m.workshop_id WHERE m.user_id = %s AND m.status = 'active' "
                "ORDER BY m.created_at", (user_id,))
    return cur.fetchall()
