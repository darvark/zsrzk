from __future__ import annotations

from datetime import datetime

from app.extensions import db
from app.models import Contest, ContestEdition


def test_contest_calendar_lists_scheduled_editions_in_month_order(client, app_ctx):
    contests = [Contest(name=name) for name in ("March Cup", "January Sprint", "Draft Cup")]
    db.session.add_all(contests)
    db.session.flush()

    db.session.add_all(
        [
            ContestEdition(
                contest_id=contests[0].id,
                year=2027,
                status="accepting_logs",
                submission_open_at=datetime(2027, 3, 10),
            ),
            ContestEdition(
                contest_id=contests[1].id,
                year=2027,
                status="submission_closed",
                submission_open_at=datetime(2027, 1, 20),
            ),
            ContestEdition(
                contest_id=contests[2].id,
                year=2027,
                status="draft",
                submission_open_at=datetime(2027, 2, 1),
            ),
        ]
    )
    db.session.commit()

    response = client.get("/kalendarz?year=2027")

    assert response.status_code == 200
    assert response.data.find(b"January Sprint") < response.data.find(b"March Cup")
    assert b"Draft Cup" not in response.data
    assert "Zaplanowane".encode("utf-8") in response.data
    assert "Zamknięte".encode("utf-8") in response.data
    assert b"contest-search" in response.data
    assert b"month-order" in response.data

    january_response = client.get("/kalendarz?year=2027&month=1")

    assert january_response.status_code == 200
    assert b"January Sprint" in january_response.data
    assert b"March Cup" not in january_response.data
    assert b'value="1" selected' in january_response.data


def test_contest_calendar_does_not_show_other_years(client, app_ctx):
    contest = Contest(name="Next Year Contest")
    db.session.add(contest)
    db.session.flush()
    db.session.add(
        ContestEdition(
            contest_id=contest.id,
            year=2028,
            status="accepting_logs",
            submission_open_at=datetime(2028, 5, 1),
        )
    )
    db.session.commit()

    response = client.get("/kalendarz?year=2027")

    assert response.status_code == 200
    assert b"Next Year Contest" not in response.data
    assert "Brak zaplanowanych zawodów na 2027 rok.".encode("utf-8") in response.data