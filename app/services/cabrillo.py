from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


CALLSIGN_RE = re.compile(r"^[A-Z0-9/]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^\d{4}$")


@dataclass
class ParsedQSO:
    """Normalized representation of one Cabrillo QSO line."""

    line_no: int
    raw_line: str
    qso_date: str | None
    qso_time: str | None
    frequency: str | None
    band: str | None
    mode: str | None
    worked_call: str | None
    sent_rst: str | None
    sent_exchange: str | None
    recv_rst: str | None
    recv_exchange: str | None
    parse_error: str | None = None


def normalize_band(freq_token: str) -> str | None:
    """Map a Cabrillo frequency token to a normalized amateur band label."""
    token = freq_token.upper().strip()
    if token.endswith("M"):
        return token

    if not token.isdigit():
        return None

    freq = int(token)
    if 1800 <= freq <= 2000:
        return "160M"
    if 3500 <= freq <= 4000:
        return "80M"
    if 7000 <= freq <= 7300:
        return "40M"
    if 14000 <= freq <= 14350:
        return "20M"
    if 21000 <= freq <= 21450:
        return "15M"
    if 28000 <= freq <= 29700:
        return "10M"
    return None


def parse_cabrillo(content: str) -> tuple[dict[str, str], list[ParsedQSO]]:
    """Parse Cabrillo text into header fields and normalized QSO rows."""
    header: dict[str, str] = {}
    qsos: list[ParsedQSO] = []

    for line_no, raw in enumerate(content.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue

        if line.upper().startswith("QSO:"):
            qsos.append(parse_qso_line(line_no, raw))
            continue

        if ":" in line:
            key, value = line.split(":", 1)
            header[key.strip().upper()] = value.strip()

    return header, qsos


def parse_qso_line(line_no: int, raw_line: str) -> ParsedQSO:
    """Parse a single Cabrillo QSO line and attach any detected parse error."""
    payload = raw_line.strip()[4:].strip()
    parts = payload.split()

    if len(parts) < 10:
        return ParsedQSO(
            line_no=line_no,
            raw_line=raw_line,
            qso_date=None,
            qso_time=None,
            frequency=None,
            band=None,
            mode=None,
            worked_call=None,
            sent_rst=None,
            sent_exchange=None,
            recv_rst=None,
            recv_exchange=None,
            parse_error="QSO line has fewer columns than expected.",
        )

    frequency = parts[0]
    mode = parts[1].upper()
    qso_date = parts[2]
    qso_time = parts[3]

    sent_rst = parts[5]
    sent_exchange = parts[6]
    worked_call = parts[7].upper()
    recv_rst = parts[8]
    recv_exchange = " ".join(parts[9:])

    band = normalize_band(frequency)

    parse_error = None
    if not DATE_RE.match(qso_date):
        parse_error = "Invalid QSO date format. Expected YYYY-MM-DD."
    elif not TIME_RE.match(qso_time):
        parse_error = "Invalid QSO time format. Expected HHMM."
    elif not CALLSIGN_RE.match(worked_call):
        parse_error = "Worked callsign contains invalid characters."

    return ParsedQSO(
        line_no=line_no,
        raw_line=raw_line,
        qso_date=qso_date,
        qso_time=qso_time,
        frequency=frequency,
        band=band,
        mode=mode,
        worked_call=worked_call,
        sent_rst=sent_rst,
        sent_exchange=sent_exchange,
        recv_rst=recv_rst,
        recv_exchange=recv_exchange,
        parse_error=parse_error,
    )


def validate_cabrillo_payload(header: dict[str, str], qsos: list[ParsedQSO]) -> list[str]:
    """Return upload-blocking Cabrillo format errors for parsed content."""
    errors: list[str] = []

    if "START-OF-LOG" not in header:
        errors.append("Invalid Cabrillo format: missing START-OF-LOG header.")
    if "END-OF-LOG" not in header:
        errors.append("Invalid Cabrillo format: missing END-OF-LOG footer.")
    if not (header.get("CALLSIGN") or "").strip():
        errors.append("Invalid Cabrillo format: missing CALLSIGN header.")
    if not qsos:
        errors.append("Invalid Cabrillo format: at least one line starting with QSO: is required.")

    first_qso_error = next((row for row in qsos if row.parse_error), None)
    if first_qso_error is not None:
        errors.append(
            f"Invalid Cabrillo format: malformed QSO at line {first_qso_error.line_no}"
            f" ({first_qso_error.parse_error})."
        )

    return errors


def callsign_prefix(callsign: str | None) -> str:
    """Return the normalized prefix used for prefix-based scoring rules."""
    if not callsign:
        return ""
    candidate = callsign.split("/")[0].upper()
    match = re.match(r"^[A-Z]+\d?", candidate)
    return match.group(0) if match else candidate


def cast_rule_value(raw: str) -> Any:
    """Coerce a stored rule string into a bool, int, list, or raw string."""
    value = raw.strip()
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
        return int(value)
    if "," in value:
        return [item.strip().upper() for item in value.split(",") if item.strip()]
    return value
