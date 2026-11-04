"""Shared primitives for qual_v2: strict JSON, canonical hashing, safe paths, UTC parsing. Stdlib only (the app image has no rev3 code)."""
from __future__ import annotations
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{64}$")
_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]*$")      # a leading underscore is allowed (__init__.py in a self-contained bundle); a leading dot never is
MAX_JSON_BYTES = 16 * 1024 * 1024


class QualError(ValueError):
    """Every refusal carries a stable machine code (the app maps any QualError to 'not qualified')."""

    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def is_sha(x):
    return isinstance(x, str) and bool(SHA.match(x))


def is_bool(x):
    return isinstance(x, bool)


def canonical_hash(value):
    """Identical to rev3 contracts.canonical_hash (sha256 of sorted-key, compact, NaN-free JSON); the cross-check is in tests."""
    try:
        blob = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as e:
        raise QualError("not_canonicalisable", type(e).__name__)
    return hashlib.sha256(blob.encode()).hexdigest()


def registry_digest(value):
    """The central-registry content digest (rev3 data._digest): like canonical_hash but without allow_nan=False. Used only for READ entry receipts."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def bytes_sha256(b):
    return hashlib.sha256(b).hexdigest()


def _reject_constant(name):
    raise ValueError("non-finite JSON constant " + name)


def _no_dup_pairs(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("duplicate JSON key " + repr(k))
        out[k] = v
    return out


def loads_strict(text):
    """json.loads that refuses NaN/Infinity and duplicate keys (a duplicated 'qualified' key must never be last-wins)."""
    return json.loads(text, parse_constant=_reject_constant, object_pairs_hook=_no_dup_pairs)


def safe_relpath(rel):
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel:
        raise QualError("path_invalid", repr(rel)[:80])
    parts = rel.split("/")
    if any(p in (".", "..") or not _SEGMENT.match(p) for p in parts):
        raise QualError("path_invalid", rel[:80])
    return parts


def inside(root, rel, *, must_exist=True):
    """root/rel with no traversal and no symlink component; must be a regular file when must_exist."""
    parts = safe_relpath(rel)
    cur = Path(root)
    for part in parts:
        cur = cur / part
        if cur.is_symlink():
            raise QualError("path_invalid", "symlink " + rel)
    if must_exist and not cur.is_file():
        raise QualError("file_missing", rel)
    return cur


def read_bytes_checked(path, sha=None, *, what="file"):
    """Read a file ONCE (size-capped); if sha is given, the returned bytes are the ones that hash to it (no hash-then-reread window)."""
    p = Path(path)
    try:
        if p.stat().st_size > MAX_JSON_BYTES:
            raise QualError("json_too_large", what)
        data = p.read_bytes()
    except QualError:
        raise
    except OSError as e:
        raise QualError("file_unreadable", f"{what}: {type(e).__name__}")
    if sha is not None and bytes_sha256(data) != sha:
        raise QualError("evidence_hash_mismatch", what)
    return data


def parse_json_bytes(data, *, what="json"):
    try:
        return loads_strict(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise QualError("json_invalid", f"{what}: {type(e).__name__}")


def read_json_file(path, *, what="json", sha=None):
    return parse_json_bytes(read_bytes_checked(path, sha, what=what), what=what)


def parse_utc(ts, what="timestamp"):
    """Strict ISO-8601 with an explicit UTC offset ('Z' or +00:00). Naive or non-UTC offsets are refused (ordering checks must be unambiguous)."""
    if not isinstance(ts, str) or not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|\+00:00)$", ts):
        raise QualError("timestamp_invalid", f"{what}: {str(ts)[:40]!r}")
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        raise QualError("timestamp_invalid", what)


def write_private(path, data: bytes, *, exclusive=True):
    """0600 file, optionally refusing to overwrite, fsync'd (the same posture as rev3 private_io)."""
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())


def mkdir_private(path):
    Path(path).mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(str(path), 0o700)
