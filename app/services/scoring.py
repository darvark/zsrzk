from __future__ import annotations

from collections import defaultdict

from ..extensions import db
from ..models import ContestLog, QSOEntry


def compile_rules(edition) -> dict:
    """Compile an edition's stored rule rows into scorer-friendly values."""
    compiled = {
        "base_qso_points": 1,
        "qso_points_cw": 0,
        "qso_points_ssb": 0,
        "qso_points_digi": 0,
        "digi_modes": ["DIGI", "RTTY", "FT8", "PSK31", "PSK63", "JT65", "FT4"],
        "allowed_bands": [],
        "allowed_modes": [],
        "dupe_scope": "band_mode",  # options: band_mode, band, global
        "bonus_per_unique_prefix": 0,
        "multiplier_per_unique_band": 0,
        "multiplier_per_unique_mode": 0,
        "multiplier_per_unique_prefix": 0,
        "premium_station_points": {},
        "require_cross_log_match": False,
        "cross_log_time_tolerance_min": 0,
        "cross_log_check_exchange": False,
    }

    for rule in edition.rules:
        key = rule.key.strip().lower()
        value = rule.value.strip()

        if key in {
            "base_qso_points",
            "qso_points_cw",
            "qso_points_ssb",
            "qso_points_digi",
            "bonus_per_unique_prefix",
            "multiplier_per_unique_band",
            "multiplier_per_unique_mode",
            "multiplier_per_unique_prefix",
            "cross_log_time_tolerance_min",
        }:
            try:
                compiled[key] = int(value)
            except ValueError:
                continue
        elif key in {"allowed_bands", "allowed_modes", "digi_modes"}:
            compiled[key] = [part.strip().upper() for part in value.split(",") if part.strip()]
        elif key == "dupe_scope" and value in {"band_mode", "band", "global"}:
            compiled[key] = value
        elif key in {"require_cross_log_match", "cross_log_check_exchange"}:
            compiled[key] = value.strip().lower() in {"1", "true", "yes", "y", "on"}
        elif key == "premium_station_points":
            premium_map = {}
            pairs = [part.strip() for part in value.split(",") if part.strip()]
            for pair in pairs:
                if ":" not in pair:
                    continue
                call, points = pair.split(":", 1)
                call_norm = call.strip().upper()
                try:
                    premium_map[call_norm] = int(points.strip())
                except ValueError:
                    continue
            compiled[key] = premium_map

    return compiled


def _dupe_key(qso: QSOEntry, scope: str) -> tuple:
    """Build the duplicate-detection key for the configured dupe scope."""
    call = (qso.worked_call or "").upper()
    band = (qso.band or "").upper()
    mode = (qso.mode or "").upper()

    if scope == "global":
        return (call,)
    if scope == "band":
        return (call, band)
    return (call, band, mode)


def _prefix(call: str | None) -> str:
    """Extract the working prefix token used by prefix multipliers."""
    if not call:
        return ""
    token = call.split("/")[0].upper()
    out = []
    for ch in token:
        if ch.isalnum():
            out.append(ch)
            if ch.isdigit():
                break
        else:
            break
    return "".join(out)


def _norm_token(value: str | None) -> str:
    """Normalize text values before scoring comparisons."""
    return (value or "").strip().upper()


def _time_to_minutes(value: str | None) -> int | None:
    """Convert a HHMM string into minutes after midnight."""
    token = (value or "").strip()
    if len(token) != 4 or not token.isdigit():
        return None
    hh = int(token[:2])
    mm = int(token[2:])
    if hh > 23 or mm > 59:
        return None
    return hh * 60 + mm


def _within_time_tolerance(left: str | None, right: str | None, tolerance_minutes: int) -> bool:
    """Return whether two QSO times fall within the allowed tolerance window."""
    left_min = _time_to_minutes(left)
    right_min = _time_to_minutes(right)
    if left_min is None or right_min is None:
        return False
    return abs(left_min - right_min) <= max(0, tolerance_minutes)


def _build_cross_log_index(log) -> dict[tuple[str, str, str, str, str], list[dict[str, str]]]:
    """Index reciprocal QSOs from sibling logs in the same contest edition."""
    rows = (
        db.session.query(
            ContestLog.station_call,
            QSOEntry.worked_call,
            QSOEntry.qso_date,
            QSOEntry.qso_time,
            QSOEntry.band,
            QSOEntry.mode,
            QSOEntry.sent_exchange,
            QSOEntry.recv_exchange,
        )
        .join(QSOEntry, QSOEntry.log_id == ContestLog.id)
        .filter(ContestLog.edition_id == log.edition_id, ContestLog.id != log.id)
        .all()
    )

    index: dict[tuple[str, str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = (
            _norm_token(row.station_call),
            _norm_token(row.worked_call),
            _norm_token(row.qso_date),
            _norm_token(row.band),
            _norm_token(row.mode),
        )
        index[key].append(
            {
                "qso_time": row.qso_time or "",
                "sent_exchange": _norm_token(row.sent_exchange),
                "recv_exchange": _norm_token(row.recv_exchange),
            }
        )

    return index


def _mode_category(mode: str | None, digi_modes: set[str]) -> str:
    """Collapse a raw mode token into CW, SSB, DIGI, or OTHER buckets."""
    token = _norm_token(mode)
    if token == "CW":
        return "CW"
    if token in {"SSB", "PH", "PHONE", "USB", "LSB", "SSH"}:
        return "SSB"
    if token in digi_modes:
        return "DIGI"
    return "OTHER"


def _qso_base_points_for_mode(mode: str | None, rules: dict) -> int:
    """Resolve the base point value for a QSO after mode-specific overrides."""
    category = _mode_category(mode, set(rules.get("digi_modes", [])))
    if category == "CW" and int(rules.get("qso_points_cw", 0)) > 0:
        return int(rules.get("qso_points_cw", 0))
    if category == "SSB" and int(rules.get("qso_points_ssb", 0)) > 0:
        return int(rules.get("qso_points_ssb", 0))
    if category == "DIGI" and int(rules.get("qso_points_digi", 0)) > 0:
        return int(rules.get("qso_points_digi", 0))
    return int(rules.get("base_qso_points", 1))


def _has_reciprocal_cross_log_match(
    qso: QSOEntry,
    own_station_call: str,
    cross_index: dict[tuple[str, str, str, str, str], list[dict[str, str]]],
    tolerance_minutes: int,
    check_exchange: bool,
) -> bool:
    """Check whether another uploaded log contains the reciprocal QSO."""
    reciprocal_key = (
        _norm_token(qso.worked_call),
        own_station_call,
        _norm_token(qso.qso_date),
        _norm_token(qso.band),
        _norm_token(qso.mode),
    )

    candidates = cross_index.get(reciprocal_key, [])
    if not candidates:
        return False

    for candidate in candidates:
        if not _within_time_tolerance(qso.qso_time, candidate["qso_time"], tolerance_minutes):
            continue

        if check_exchange:
            if _norm_token(qso.sent_exchange) != candidate["recv_exchange"]:
                continue
            if _norm_token(qso.recv_exchange) != candidate["sent_exchange"]:
                continue

        return True

    return False


def score_log(log, rules: dict) -> dict:
    """Evaluate all QSOs in a log and return the computed scoring breakdown."""
    allowed_bands = {b.upper() for b in rules.get("allowed_bands", [])}
    allowed_modes = {m.upper() for m in rules.get("allowed_modes", [])}
    dupe_scope = rules.get("dupe_scope", "band_mode")
    bonus_per_unique_prefix = int(rules.get("bonus_per_unique_prefix", 0))
    multiplier_per_unique_band = int(rules.get("multiplier_per_unique_band", 0))
    multiplier_per_unique_mode = int(rules.get("multiplier_per_unique_mode", 0))
    multiplier_per_unique_prefix = int(rules.get("multiplier_per_unique_prefix", 0))
    premium_station_points = {
        key.upper(): int(value) for key, value in dict(rules.get("premium_station_points", {})).items()
    }
    require_cross_log_match = bool(rules.get("require_cross_log_match", False))
    cross_log_time_tolerance_min = int(rules.get("cross_log_time_tolerance_min", 0))
    cross_log_check_exchange = bool(rules.get("cross_log_check_exchange", False))

    own_station_call = _norm_token(log.station_call)
    cross_index = _build_cross_log_index(log) if require_cross_log_match else {}

    seen = set()
    unique_prefixes = set()
    unique_bands = set()
    unique_modes = set()

    valid_count = 0
    invalid_count = 0
    total_qso_points = 0
    premium_points_total = 0

    mode_breakdown: dict[str, dict[str, int]] = {
        "CW": {"qso_count": 0, "base_points": 0, "premium_points": 0, "total_points": 0},
        "SSB": {"qso_count": 0, "base_points": 0, "premium_points": 0, "total_points": 0},
        "DIGI": {"qso_count": 0, "base_points": 0, "premium_points": 0, "total_points": 0},
        "OTHER": {"qso_count": 0, "base_points": 0, "premium_points": 0, "total_points": 0},
    }
    premium_station_hits: dict[str, dict[str, int]] = defaultdict(
        lambda: {"hit_count": 0, "points_per_qso": 0, "total_points": 0}
    )

    by_reason = defaultdict(int)

    for qso in log.qsos:
        qso.points_awarded = 0

        if qso.invalid_reason:
            qso.is_valid = False
            invalid_count += 1
            by_reason[qso.invalid_reason] += 1
            continue

        if not qso.worked_call:
            qso.is_valid = False
            qso.invalid_reason = "Missing worked callsign."
        elif allowed_bands and (qso.band or "").upper() not in allowed_bands:
            qso.is_valid = False
            qso.invalid_reason = f"Band {(qso.band or 'UNKNOWN')} is not allowed by contest rules."
        elif allowed_modes and (qso.mode or "").upper() not in allowed_modes:
            qso.is_valid = False
            qso.invalid_reason = f"Mode {(qso.mode or 'UNKNOWN')} is not allowed by contest rules."
        elif require_cross_log_match and not own_station_call:
            qso.is_valid = False
            qso.invalid_reason = "Cross-log match required but uploaded log has no CALLSIGN header."
        else:
            candidate = _dupe_key(qso, dupe_scope)
            if candidate in seen:
                qso.is_valid = False
                qso.invalid_reason = "Duplicate QSO under current dupe scope rule."
            else:
                if require_cross_log_match and not _has_reciprocal_cross_log_match(
                    qso,
                    own_station_call,
                    cross_index,
                    cross_log_time_tolerance_min,
                    cross_log_check_exchange,
                ):
                    qso.is_valid = False
                    qso.invalid_reason = "No reciprocal QSO found in other station logs."
                else:
                    seen.add(candidate)
                    qso.is_valid = True
                    qso.invalid_reason = None
                    mode_category = _mode_category(qso.mode, set(rules.get("digi_modes", [])))
                    qso_base_points = _qso_base_points_for_mode(qso.mode, rules)
                    premium_points = premium_station_points.get(_norm_token(qso.worked_call), 0)
                    qso.points_awarded = qso_base_points + premium_points
                    total_qso_points += qso_base_points
                    premium_points_total += premium_points

                    mode_breakdown[mode_category]["qso_count"] += 1
                    mode_breakdown[mode_category]["base_points"] += qso_base_points
                    mode_breakdown[mode_category]["premium_points"] += premium_points
                    mode_breakdown[mode_category]["total_points"] += qso.points_awarded

                    if premium_points > 0:
                        worked_call = _norm_token(qso.worked_call)
                        premium_station_hits[worked_call]["hit_count"] += 1
                        premium_station_hits[worked_call]["points_per_qso"] = premium_points
                        premium_station_hits[worked_call]["total_points"] += premium_points

                    unique_prefixes.add(_prefix(qso.worked_call))
                    unique_bands.add((qso.band or "").upper())
                    unique_modes.add((qso.mode or "").upper())

        if qso.is_valid:
            valid_count += 1
        else:
            invalid_count += 1
            if qso.invalid_reason:
                by_reason[qso.invalid_reason] += 1

    unique_prefix_count = len({p for p in unique_prefixes if p})
    unique_band_count = len({b for b in unique_bands if b})
    unique_mode_count = len({m for m in unique_modes if m})

    additive_prefix_bonus = unique_prefix_count * bonus_per_unique_prefix

    total_multiplier = (
        1
        + unique_band_count * max(0, multiplier_per_unique_band)
        + unique_mode_count * max(0, multiplier_per_unique_mode)
        + unique_prefix_count * max(0, multiplier_per_unique_prefix)
    )

    qso_subtotal = total_qso_points + premium_points_total
    multiplied_score = qso_subtotal * total_multiplier
    total_score = multiplied_score + additive_prefix_bonus

    return {
        "valid_qso_count": valid_count,
        "invalid_qso_count": invalid_count,
        "total_qso_points": total_qso_points,
        "premium_points_total": premium_points_total,
        "qso_subtotal": qso_subtotal,
        "total_multiplier": total_multiplier,
        "multiplied_score": multiplied_score,
        "multiplier_bonus": additive_prefix_bonus,
        "mode_breakdown": mode_breakdown,
        "multiplier_breakdown": {
            "unique_band_count": unique_band_count,
            "multiplier_per_unique_band": multiplier_per_unique_band,
            "band_multiplier_add": unique_band_count * max(0, multiplier_per_unique_band),
            "unique_mode_count": unique_mode_count,
            "multiplier_per_unique_mode": multiplier_per_unique_mode,
            "mode_multiplier_add": unique_mode_count * max(0, multiplier_per_unique_mode),
            "unique_prefix_count": unique_prefix_count,
            "multiplier_per_unique_prefix": multiplier_per_unique_prefix,
            "prefix_multiplier_add": unique_prefix_count * max(0, multiplier_per_unique_prefix),
        },
        "premium_station_hits": dict(sorted(premium_station_hits.items())),
        "computed_score": total_score,
        "invalid_reasons": dict(by_reason),
    }
