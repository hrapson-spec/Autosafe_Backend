"""Opaque secrets: generation and hashing. Only hashes are stored."""
from __future__ import annotations

import base64
import hashlib
import secrets


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_invite_code() -> str:
    raw = base64.b32encode(secrets.token_bytes(10)).decode().rstrip("=")  # 16 chars, 80 bits
    return "GRG-" + "-".join(raw[i:i + 4] for i in range(0, 16, 4))


def normalise_invite(code: str) -> str:
    return "".join(c for c in code.upper() if c.isalnum())


def hash_invite(code: str) -> str:
    return hashlib.sha256(("invite:" + normalise_invite(code)).encode()).hexdigest()


def sha256_json(obj) -> str:
    import json
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
