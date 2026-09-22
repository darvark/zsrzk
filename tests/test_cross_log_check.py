from __future__ import annotations

from app.extensions import db
from app.models import ContestLog, ContestRule, QSOEntry
from app.services.scoring import compile_rules, score_log


def _add_rule(edition, key: str, value: str) -> None:
    db.session.add(ContestRule(edition_id=edition.id, key=key, value=value))


def _create_log_with_single_qso(
    edition,
    *,
    station_call: str | None,
    worked_call: str,
    qso_date: str = "20260101",
    qso_time: str = "1200",
    band: str = "20M",
    mode: str = "CW",
    sent_exchange: str = "001",
    recv_exchange: str = "002",
) -> ContestLog:
    log = ContestLog(
        edition_id=edition.id,
        station_call=station_call,
        raw_text="START-OF-LOG: 3.0",
        submission_status="accepted",
        declaration_accepted=True,
    )
    db.session.add(log)
    db.session.flush()

    db.session.add(
        QSOEntry(
            log_id=log.id,
            line_no=1,
            raw_line="QSO: TEST",
            qso_date=qso_date,
            qso_time=qso_time,
            band=band,
            mode=mode,
            worked_call=worked_call,
            sent_exchange=sent_exchange,
            recv_exchange=recv_exchange,
            is_valid=True,
            invalid_reason=None,
            points_awarded=0,
        )
    )
    db.session.flush()
    return log


def test_cross_log_match_accepts_reciprocal_qso_within_tolerance(app_ctx, accepting_edition):
    _add_rule(accepting_edition, "require_cross_log_match", "true")
    _add_rule(accepting_edition, "cross_log_time_tolerance_min", "3")

    _create_log_with_single_qso(
        accepting_edition,
        station_call="SP2BBB",
        worked_call="SP1AAA",
        qso_time="1202",
        sent_exchange="002",
        recv_exchange="001",
    )
    target = _create_log_with_single_qso(
        accepting_edition,
        station_call="SP1AAA",
        worked_call="SP2BBB",
        qso_time="1200",
        sent_exchange="001",
        recv_exchange="002",
    )

    results = score_log(target, compile_rules(accepting_edition))

    assert results["valid_qso_count"] == 1
    assert results["invalid_qso_count"] == 0
    assert results["computed_score"] == 1
    assert target.qsos[0].is_valid is True
    assert target.qsos[0].invalid_reason is None


def test_cross_log_match_rejects_qso_without_reciprocal_log(app_ctx, accepting_edition):
    _add_rule(accepting_edition, "require_cross_log_match", "true")

    target = _create_log_with_single_qso(
        accepting_edition,
        station_call="SP1AAA",
        worked_call="SP2BBB",
    )

    results = score_log(target, compile_rules(accepting_edition))

    assert results["valid_qso_count"] == 0
    assert results["invalid_qso_count"] == 1
    assert results["computed_score"] == 0
    assert target.qsos[0].is_valid is False
    assert target.qsos[0].invalid_reason == "No reciprocal QSO found in other station logs."


def test_cross_log_match_requires_station_callsign_header(app_ctx, accepting_edition):
    _add_rule(accepting_edition, "require_cross_log_match", "true")

    target = _create_log_with_single_qso(
        accepting_edition,
        station_call=None,
        worked_call="SP2BBB",
    )

    results = score_log(target, compile_rules(accepting_edition))

    assert results["valid_qso_count"] == 0
    assert results["invalid_qso_count"] == 1
    assert results["computed_score"] == 0
    assert target.qsos[0].is_valid is False
    assert target.qsos[0].invalid_reason == "Cross-log match required but uploaded log has no CALLSIGN header."


def test_cross_log_exchange_check_rejects_mismatched_exchanges(app_ctx, accepting_edition):
    _add_rule(accepting_edition, "require_cross_log_match", "true")
    _add_rule(accepting_edition, "cross_log_check_exchange", "true")

    _create_log_with_single_qso(
        accepting_edition,
        station_call="SP2BBB",
        worked_call="SP1AAA",
        sent_exchange="999",
        recv_exchange="888",
    )
    target = _create_log_with_single_qso(
        accepting_edition,
        station_call="SP1AAA",
        worked_call="SP2BBB",
        sent_exchange="001",
        recv_exchange="002",
    )

    results = score_log(target, compile_rules(accepting_edition))

    assert results["valid_qso_count"] == 0
    assert results["invalid_qso_count"] == 1
    assert results["computed_score"] == 0
    assert target.qsos[0].is_valid is False
    assert target.qsos[0].invalid_reason == "No reciprocal QSO found in other station logs."
