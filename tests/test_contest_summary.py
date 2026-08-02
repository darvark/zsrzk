from __future__ import annotations

from app.extensions import db
from app.models import Contest, ContestCategory, ContestEdition, ContestLog


def test_contest_summary_shows_category_classification_for_accepted_logs_only(client, app_ctx):
    contest = Contest(name="Summary Contest")
    db.session.add(contest)
    db.session.flush()

    edition = ContestEdition(contest_id=contest.id, year=2026, status="submission_closed")
    db.session.add(edition)
    db.session.flush()

    category_single = ContestCategory(edition_id=edition.id, name="Single Operator")
    category_multi = ContestCategory(edition_id=edition.id, name="Multi Operator")
    db.session.add_all([category_single, category_multi])
    db.session.flush()

    db.session.add_all(
        [
            ContestLog(
                edition_id=edition.id,
                category_id=category_single.id,
                station_call="SP1AAA",
                operator="Alpha",
                raw_text="START-OF-LOG: 3.0",
                submission_status="accepted",
                declaration_accepted=True,
                computed_score=120,
                valid_qso_count=12,
                invalid_qso_count=0,
            ),
            ContestLog(
                edition_id=edition.id,
                category_id=category_single.id,
                station_call="SP2BBB",
                operator="Bravo",
                raw_text="START-OF-LOG: 3.0",
                submission_status="accepted",
                declaration_accepted=True,
                computed_score=80,
                valid_qso_count=8,
                invalid_qso_count=1,
            ),
            ContestLog(
                edition_id=edition.id,
                category_id=category_multi.id,
                station_call="SP3CCC",
                operator="Charlie",
                raw_text="START-OF-LOG: 3.0",
                submission_status="accepted",
                declaration_accepted=True,
                computed_score=200,
                valid_qso_count=20,
                invalid_qso_count=0,
            ),
            ContestLog(
                edition_id=edition.id,
                category_id=category_single.id,
                station_call="SP9REJ",
                operator="Rejected",
                raw_text="START-OF-LOG: 3.0",
                submission_status="rejected",
                declaration_accepted=True,
                computed_score=999,
                valid_qso_count=99,
                invalid_qso_count=0,
            ),
            ContestLog(
                edition_id=edition.id,
                category_id=category_single.id,
                station_call="SP8CHK",
                operator="Checklog",
                raw_text="START-OF-LOG: 3.0",
                submission_status="accepted",
                declaration_accepted=True,
                is_checklog=True,
                computed_score=500,
                valid_qso_count=50,
                invalid_qso_count=0,
            ),
        ]
    )
    db.session.commit()

    response = client.get(f"/contests/{contest.id}/summary?category=Single+Operator")

    assert response.status_code == 200
    assert "Klasyfikacja wg kategorii".encode("utf-8") in response.data
    assert b"SP1AAA" in response.data
    assert b"SP2BBB" in response.data
    assert b"SP3CCC" not in response.data
    assert b"SP9REJ" not in response.data
    assert b"SP8CHK" not in response.data
    assert "Łącznie sklasyfikowanych logów:</strong> 2".encode("utf-8") in response.data
    assert response.data.find(b"SP1AAA") < response.data.find(b"SP2BBB")