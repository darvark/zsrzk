from __future__ import annotations

from io import BytesIO
from pathlib import Path

from app.extensions import db
from app.models import ContestClub, ContestLog


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "cabrillo"


def _load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def _upload_payload(edition_id: int, cabrillo_text: str, declaration_accepted: bool = True) -> dict[str, str]:
    payload = {
        "edition_id": str(edition_id),
        "submitter_email": "op@example.com",
        "location": "Krakow",
        "grid_locator": "JO90",
        "power_category": "LP",
        "operators_count": "1",
        "cabrillo_text": cabrillo_text,
    }
    if declaration_accepted:
        payload["declaration_accepted"] = "on"
    return payload


def test_upload_log_success_persists_scored_qsos(client, app_ctx, accepting_edition):
    response = client.post(
        "/logs/upload",
        data=_upload_payload(accepting_edition.id, _load_fixture("valid_simple.log")),
        follow_redirects=False,
    )

    assert response.status_code == 302

    stored = ContestLog.query.one()
    assert stored.station_call == "SP1AAA"
    assert stored.valid_qso_count == 2
    assert stored.invalid_qso_count == 0
    assert stored.computed_score == 2


def test_upload_log_sends_confirmation_email(client, app, app_ctx, accepting_edition):
    response = client.post(
        "/logs/upload",
        data=_upload_payload(accepting_edition.id, _load_fixture("valid_simple.log")),
        follow_redirects=False,
    )

    assert response.status_code == 302

    outbox = app.extensions["mail_outbox"]
    assert len(outbox) == 1
    assert outbox[0]["to"] == "op@example.com"
    assert "Log received - Test Contest 2026" == outbox[0]["subject"]
    assert "Your log has been received successfully." in outbox[0]["body"]
    assert "Callsign: SP1AAA" in outbox[0]["body"]


def test_upload_duplicate_qso_is_marked_invalid(client, app_ctx, accepting_edition):
    response = client.post(
        "/logs/upload",
        data=_upload_payload(accepting_edition.id, _load_fixture("with_duplicate.log")),
        follow_redirects=False,
    )

    assert response.status_code == 302

    stored = ContestLog.query.one()
    assert stored.valid_qso_count == 5
    assert stored.invalid_qso_count == 1
    assert stored.computed_score == 5


def test_upload_requires_declaration(client, app_ctx, accepting_edition):
    response = client.post(
        "/logs/upload",
        data=_upload_payload(
            accepting_edition.id,
            _load_fixture("valid_simple.log"),
            declaration_accepted=False,
        ),
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Przed wysłaniem musisz zaakceptować oświadczenie.".encode("utf-8") in response.data
    assert db.session.query(ContestLog).count() == 0


def test_upload_rejects_invalid_cabrillo_format(client, app_ctx, accepting_edition):
    response = client.post(
        "/logs/upload",
        data=_upload_payload(accepting_edition.id, _load_fixture("malformed_qso.log")),
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Invalid Cabrillo format: malformed QSO" in response.data
    assert db.session.query(ContestLog).count() == 0


def test_upload_rejects_non_text_file(client, app_ctx, accepting_edition):
    payload = _upload_payload(accepting_edition.id, "")
    payload["cabrillo_file"] = (BytesIO(b"\x89PNG\r\n\x1a\n\x00\x00\x00IHDR"), "log.png")

    response = client.post(
        "/logs/upload",
        data=payload,
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Przesłany log musi być plikiem tekstowym UTF-8.".encode("utf-8") in response.data
    assert db.session.query(ContestLog).count() == 0


def test_upload_rejects_content_without_qso_lines(client, app_ctx, accepting_edition):
    payload = _upload_payload(accepting_edition.id, "")
    payload["cabrillo_file"] = (
        BytesIO(b"START-OF-LOG: 3.0\nCALLSIGN: SP1AAA\nEND-OF-LOG:\n"),
        "no_qso.log",
    )

    response = client.post(
        "/logs/upload",
        data=payload,
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"at least one line starting with QSO: is required" in response.data
    assert db.session.query(ContestLog).count() == 0


def test_upload_rejects_unapproved_club(client, app_ctx, accepting_edition):
    pending_club = ContestClub(name="Pending Upload Club", is_approved=False)
    db.session.add(pending_club)
    db.session.commit()

    payload = _upload_payload(accepting_edition.id, _load_fixture("valid_simple.log"))
    payload["club_id"] = str(pending_club.id)

    response = client.post(
        "/logs/upload",
        data=payload,
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Wybrany klub oczekuje na akceptację i nie może być jeszcze użyty.".encode("utf-8") in response.data
    assert db.session.query(ContestLog).count() == 0
