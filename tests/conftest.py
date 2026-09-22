from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app import create_app
from app.extensions import db
from app.models import Contest, ContestEdition


@pytest.fixture()
def app(tmp_path):
    db_path = tmp_path / "test.db"
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
            "SECRET_KEY": "test-secret",
            "MAIL_SUPPRESS_SEND": True,
            "MAIL_DEFAULT_SENDER": "noreply@example.com",
        }
    )

    with app.app_context():
        db.drop_all()
        db.create_all()

    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def app_ctx(app):
    with app.app_context():
        yield


@pytest.fixture()
def accepting_edition(app_ctx):
    contest = Contest(name="Test Contest", description="Fixture contest")
    db.session.add(contest)
    db.session.flush()

    now = datetime.utcnow()
    edition = ContestEdition(
        contest_id=contest.id,
        year=2026,
        name="Fixture Edition",
        status="accepting_logs",
        submission_open_at=now - timedelta(days=1),
        submission_deadline=now + timedelta(days=1),
    )
    db.session.add(edition)
    db.session.commit()
    return edition
