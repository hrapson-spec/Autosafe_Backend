"""Import parsing and row validation (pure functions; no database).

Registration rules are copied (not imported) from dvsa_client.py:257-299 and
:128-137 because that module pulls in pandas/cachetools and a process-wide
singleton. Keep the two in step if the rules change.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

# dvsa_client.py:128 (VRM_BASIC_PATTERN) and :131-137 (UK_PATTERNS)
VRM_BASIC = re.compile(r"^[A-Z0-9]{2,8}$")
UK_PATTERNS = [
    re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z]{3}$"),
    re.compile(r"^[A-Z][0-9]{1,3}[A-Z]{3}$"),
    re.compile(r"^[A-Z]{3}[0-9]{1,3}[A-Z]$"),
    re.compile(r"^[0-9]{1,4}[A-Z]{1,3}$"),
    re.compile(r"^[A-Z]{1,3}[0-9]{1,4}$"),
]

BOOKING_TYPES = ("mot", "service", "retest", "service_mot")
_TYPE_ALIASES = {
    "mot": "mot", "mot test": "mot", "annual mot": "mot",
    "service": "service", "servicing": "service", "full service": "service", "interim service": "service",
    "retest": "retest", "re test": "retest", "mot retest": "retest", "mot re test": "retest",
    "service mot": "service_mot", "service and mot": "service_mot",
    "mot service": "service_mot", "mot and service": "service_mot", "full service mot": "service_mot",
    "full service and mot": "service_mot", "service plus mot": "service_mot", "mot plus service": "service_mot",
    "service_mot": "service_mot",
}

HEADER_ALIASES = {
    "registration": "registration", "reg": "registration", "vrm": "registration", "reg no": "registration",
    "registration number": "registration", "plate": "registration",
    "date": "date", "appointment date": "date", "appointment_date": "date", "booking date": "date",
    "type": "type", "booking type": "type", "booking_type": "type", "job": "type", "job type": "type",
}


@dataclass
class Issue:
    code: str
    message: str
    field: str | None = None

    def as_dict(self):
        return {"code": self.code, "field": self.field, "message": self.message}


@dataclass
class ParsedRow:
    row_number: int
    registration: str | None = None
    appointment_date: date | None = None
    booking_type: str | None = None
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


class ImportFormatError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def normalise_registration(raw) -> tuple[str | None, list[Issue], list[Issue]]:
    errors: list[Issue] = []
    warnings: list[Issue] = []
    if raw is None or not str(raw).strip():
        return None, [Issue("missing_registration", "Registration is required", "registration")], warnings
    vrm = "".join(str(raw).split()).upper()
    if not vrm.isascii() or not VRM_BASIC.match(vrm):
        return None, [Issue("invalid_registration",
                            "Registration must be 2-8 letters/numbers (spaces ignored)", "registration")], warnings
    if not any(p.match(vrm) for p in UK_PATTERNS):
        warnings.append(Issue("nonstandard_registration_format",
                              "Not a standard UK format; check it (personalised and older plates can be valid)",
                              "registration"))
    return vrm, errors, warnings


def parse_date(raw, today: date, max_ahead: int, max_back: int) -> tuple[date | None, list[Issue]]:
    if raw is None or not str(raw).strip():
        return None, [Issue("missing_date", "Date is required", "date")]
    s = str(raw).strip()
    d = None
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    m2 = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    try:
        if m:
            d = date(int(m[1]), int(m[2]), int(m[3]))
        elif m2:
            d = date(int(m2[3]), int(m2[2]), int(m2[1]))
    except ValueError:
        d = None
    if d is None:
        return None, [Issue("invalid_date", "Use YYYY-MM-DD or DD/MM/YYYY", "date")]
    if d > today + timedelta(days=max_ahead) or d < today - timedelta(days=max_back):
        return None, [Issue("date_out_of_range",
                            f"Date must be within {max_back} days back and {max_ahead} days ahead", "date")]
    return d, []


def normalise_type(raw) -> tuple[str | None, list[Issue]]:
    if raw is None or not str(raw).strip():
        return None, [Issue("missing_type", "Booking type is required", "type")]
    key = re.sub(r"[^a-z0-9]+", " ", str(raw).lower()).strip()
    key = key.replace("+", " plus ")
    t = _TYPE_ALIASES.get(key) or _TYPE_ALIASES.get(key.replace("&", "and"))
    if t is None:
        return None, [Issue("invalid_type", "Type must be MOT, service, retest or service + MOT", "type")]
    return t, []


def validate_row(n: int, reg, d, typ, today: date, s) -> ParsedRow:
    row = ParsedRow(row_number=n)
    row.registration, e, w = normalise_registration(reg)
    row.errors += e
    row.warnings += w
    row.appointment_date, e = parse_date(d, today, s.max_days_ahead, s.max_days_back)
    row.errors += e
    row.booking_type, e = normalise_type(typ)
    row.errors += e
    return row


def _split_paste_line(line: str) -> list[str]:
    for sep in ("\t", ";", ","):
        if sep in line:
            return [p.strip() for p in line.split(sep)]
    m = re.match(r"^(.*?)\s+(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{4})\s+(.*)$", line)
    if m:
        return [m[1].strip(), m[2], m[3].strip()]
    return line.split()


def _is_header(cells: list[str]) -> bool:
    keys = [HEADER_ALIASES.get(re.sub(r"[^a-z0-9_]+", " ", c.lower()).strip()) for c in cells]
    return sum(k is not None for k in keys) >= 2


def raw_rows_from_request(fmt: str, content: str | None, rows: list[dict] | None, max_rows: int):
    """Return list of (registration, date, type) raw cells. Raises ImportFormatError."""
    out: list[tuple] = []
    if fmt == "manual":
        if rows is None:
            raise ImportFormatError("validation_error", "rows is required for manual entry")
        out = [(r.get("registration"), r.get("date"), r.get("type")) for r in rows]
    elif fmt == "paste":
        if content is None:
            raise ImportFormatError("validation_error", "content is required for paste")
        first = True
        for line in content.splitlines():
            if not line.strip():
                continue
            cells = _split_paste_line(line.strip())
            if first and _is_header(cells):
                first = False
                continue
            first = False
            # Typed-out types with spaces ("service + MOT") end up after the date: rejoin the tail.
            if len(cells) > 3:
                cells = cells[:2] + [" ".join(cells[2:])]
            cells += [None] * (3 - len(cells))
            out.append((cells[0], cells[1], cells[2]))
    elif fmt == "csv":
        if content is None:
            raise ImportFormatError("validation_error", "content is required for csv")
        reader = csv.reader(io.StringIO(content))
        header = None
        for rec in reader:
            if not rec or not any(c.strip() for c in rec):
                continue
            if header is None:
                header = [HEADER_ALIASES.get(re.sub(r"[^a-z0-9_]+", " ", c.lower()).strip()) for c in rec]
                missing = {"registration", "date", "type"} - set(header)
                if missing:
                    raise ImportFormatError("validation_error",
                                            "CSV header must name registration, date and type columns "
                                            f"(missing: {', '.join(sorted(missing))})")
                continue
            cell = {h: (rec[i].strip() if i < len(rec) else None) for i, h in enumerate(header) if h}
            out.append((cell.get("registration"), cell.get("date"), cell.get("type")))
            if len(out) > max_rows:
                break
    else:
        raise ImportFormatError("validation_error", "unknown format")
    if len(out) > max_rows:
        raise ImportFormatError("too_many_rows", f"At most {max_rows} rows per import")
    if not out:
        raise ImportFormatError("validation_error", "No rows found")
    return out
