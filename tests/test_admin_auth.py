from __future__ import annotations

from app.extensions import db
from app.models import (
    AdminUser,
    Contest,
    ContestCategory,
    ContestClub,
    ContestEdition,
    ContestRule,
    ContestRuleNote,
)


def test_admin_login_required_redirects(client):
    response = client.get("/admin/", follow_redirects=False)

    assert response.status_code == 302
    assert "/admin/login" in response.headers["Location"]


def test_admin_can_login_and_create_user(client, app_ctx):
    user = AdminUser(username="admin")
    user.set_password("secret123")
    db.session.add(user)
    db.session.commit()

    response = client.post(
        "/admin/login",
        data={"username": "admin", "password": "secret123"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "/admin/" in response.headers["Location"]

    with client:
        client.post(
            "/admin/login",
            data={"username": "admin", "password": "secret123"},
            follow_redirects=True,
        )
        create_response = client.post(
            "/admin/users",
            data={
                "username": "second",
                "password": "password123",
                "confirm_password": "password123",
            },
            follow_redirects=True,
        )

    assert create_response.status_code == 200
    assert "Utworzono konto administratora: second.".encode("utf-8") in create_response.data
    assert AdminUser.query.filter_by(username="second").count() == 1


def test_non_admin_cannot_create_contest(client, app_ctx):
    response = client.post(
        "/contests",
        data={
            "name": "Open Contest",
            "description": "Should not be created",
            "first_edition_year": "2026",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "/admin/login" in response.headers["Location"]
    assert Contest.query.filter_by(name="Open Contest").count() == 0


def test_admin_can_create_contest(client, app_ctx):
    user = AdminUser(username="admin")
    user.set_password("secret123")
    db.session.add(user)
    db.session.commit()

    with client:
        client.post(
            "/admin/login",
            data={"username": "admin", "password": "secret123"},
            follow_redirects=True,
        )
        response = client.post(
            "/contests",
            data={
                "name": "Admin Contest",
                "description": "Created by admin",
                "first_edition_year": "2026",
            },
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert "Utworzono definicję zawodów i pierwszą edycję.".encode("utf-8") in response.data
    created = Contest.query.filter_by(name="Admin Contest").one()
    assert created.is_yearly_recurring is True


def test_non_admin_cannot_add_edition(client, app_ctx):
    contest = Contest(name="Protected Contest")
    db.session.add(contest)
    db.session.flush()
    db.session.add(ContestEdition(contest_id=contest.id, year=2025, status="draft"))
    db.session.commit()

    response = client.post(
        f"/contests/{contest.id}/editions",
        data={"year": "2026", "name": "Forbidden Edition"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "/admin/login" in response.headers["Location"]
    assert ContestEdition.query.filter_by(contest_id=contest.id, year=2026).count() == 0


def test_non_admin_cannot_edit_contest_edition_rules_or_metadata(client, app_ctx):
    contest = Contest(name="Protected Metadata Contest", description="Original contest description")
    db.session.add(contest)
    db.session.flush()
    edition = ContestEdition(
        contest_id=contest.id,
        year=2026,
        name="Original edition",
        description="Original edition description",
        status="draft",
    )
    db.session.add(edition)
    db.session.flush()
    rule = ContestRule(edition_id=edition.id, key="base_qso_points", value="1")
    category = ContestCategory(edition_id=edition.id, name="Single Operator")
    db.session.add_all([rule, category])
    db.session.commit()

    page = client.get(f"/editions/{edition.id}")
    responses = [
        client.post(
            f"/admin/contests/{contest.id}/edit",
            data={"name": contest.name, "description": "Changed anonymously"},
        ),
        client.post(
            f"/admin/editions/{edition.id}/edit",
            data={"name": "Changed", "description": "Changed anonymously", "year": "2026", "status": "draft"},
        ),
        client.post(
            f"/editions/{edition.id}",
            data={"key": "unsafe_rule", "value": "8"},
        ),
        client.post(
            f"/editions/{edition.id}/lifecycle",
            data={"status": "accepting_logs"},
        ),
        client.post(
            f"/editions/{edition.id}/rules/{rule.id}",
            data={"key": rule.key, "value": "9"},
        ),
        client.post(f"/editions/{edition.id}/rules/{rule.id}/delete"),
        client.post(
            f"/editions/{edition.id}/categories",
            data={"name": "Unsafe Category"},
        ),
        client.post(f"/editions/{edition.id}/categories/{category.id}/delete"),
    ]

    assert page.status_code == 200
    assert "Zapisz cykl życia".encode("utf-8") not in page.data
    assert "Dodaj regułę".encode("utf-8") not in page.data
    assert b"Zastosuj" not in page.data
    assert all(response.status_code == 302 for response in responses)
    assert all("/admin/login" in response.headers["Location"] for response in responses)
    db.session.refresh(contest)
    db.session.refresh(edition)
    db.session.refresh(rule)
    db.session.refresh(category)
    assert contest.description == "Original contest description"
    assert edition.description == "Original edition description"
    assert edition.status == "draft"
    assert rule.value == "1"
    assert ContestRule.query.filter_by(edition_id=edition.id, key="unsafe_rule").count() == 0
    assert ContestCategory.query.filter_by(edition_id=edition.id, name="Unsafe Category").count() == 0
    assert ContestCategory.query.filter_by(id=category.id).count() == 1


def test_admin_can_edit_contest_description_and_scoring_rule(client, app_ctx):
    user = AdminUser(username="editor")
    user.set_password("secret123")
    contest = Contest(name="Editable Contest", description="Before")
    db.session.add_all([user, contest])
    db.session.flush()
    edition = ContestEdition(contest_id=contest.id, year=2026, status="draft")
    db.session.add(edition)
    db.session.flush()
    rule = ContestRule(edition_id=edition.id, key="base_qso_points", value="1")
    db.session.add(rule)
    db.session.commit()

    with client:
        client.post("/admin/login", data={"username": "editor", "password": "secret123"})
        contest_response = client.post(
            f"/admin/contests/{contest.id}/edit",
            data={"name": contest.name, "description": "Admin updated description", "is_yearly_recurring": "on"},
        )
        rule_response = client.post(
            f"/editions/{edition.id}/rules/{rule.id}",
            data={"key": rule.key, "value": "3"},
        )

    assert contest_response.status_code == 302
    assert rule_response.status_code == 302
    db.session.refresh(contest)
    db.session.refresh(rule)
    assert contest.description == "Admin updated description"
    assert rule.value == "3"


def test_non_admin_cannot_manage_rule_notes(client, app_ctx):
    contest = Contest(name="Rules Contest")
    db.session.add(contest)
    db.session.flush()
    note = ContestRuleNote(contest_id=contest.id, title="Rule 1", content="Initial note")
    db.session.add(note)
    db.session.commit()

    add_response = client.post(
        f"/contests/{contest.id}/rule-notes",
        data={"title": "Blocked", "content": "Should fail"},
        follow_redirects=False,
    )
    delete_response = client.post(
        f"/contests/{contest.id}/rule-notes/{note.id}/delete",
        follow_redirects=False,
    )

    assert add_response.status_code == 302
    assert "/admin/login" in add_response.headers["Location"]
    assert delete_response.status_code == 302
    assert "/admin/login" in delete_response.headers["Location"]
    assert ContestRuleNote.query.filter_by(contest_id=contest.id, title="Blocked").count() == 0
    assert ContestRuleNote.query.filter_by(id=note.id).count() == 1


def test_admin_can_manage_rule_notes(client, app_ctx):
    user = AdminUser(username="admin")
    user.set_password("secret123")
    db.session.add(user)

    contest = Contest(name="Admin Rules Contest")
    db.session.add(contest)
    db.session.flush()
    note = ContestRuleNote(contest_id=contest.id, title="Rule A", content="Initial")
    db.session.add(note)
    db.session.commit()

    with client:
        client.post(
            "/admin/login",
            data={"username": "admin", "password": "secret123"},
            follow_redirects=True,
        )
        add_response = client.post(
            f"/contests/{contest.id}/rule-notes",
            data={"title": "Rule B", "content": "Added by admin"},
            follow_redirects=True,
        )
        delete_response = client.post(
            f"/contests/{contest.id}/rule-notes/{note.id}/delete",
            follow_redirects=True,
        )

    assert add_response.status_code == 200
    assert "Zapisano notatkę do regulaminu.".encode("utf-8") in add_response.data
    assert delete_response.status_code == 200
    assert "Usunięto notatkę do regulaminu.".encode("utf-8") in delete_response.data
    assert ContestRuleNote.query.filter_by(contest_id=contest.id, title="Rule B").count() == 1
    assert ContestRuleNote.query.filter_by(id=note.id).count() == 0


def test_non_admin_cannot_see_contest_rule_notes_section(client, app_ctx):
    contest = Contest(name="Hidden Notes Contest")
    db.session.add(contest)
    db.session.flush()
    db.session.add(ContestRuleNote(contest_id=contest.id, title="Private", content="Admin only"))
    db.session.commit()

    response = client.get(f"/contests/{contest.id}", follow_redirects=True)

    assert response.status_code == 200
    assert b"Contest Rule Notes" not in response.data
    assert b"Private" not in response.data


def test_user_submitted_club_is_pending_until_admin_approval(client, app_ctx):
    create_response = client.post(
        "/clubs",
        data={"name": "Pending Club", "description": "Submitted by user"},
        follow_redirects=True,
    )

    assert create_response.status_code == 200
    club = ContestClub.query.filter_by(name="Pending Club").one()
    assert club.is_approved is False
    assert club.approved_at is None

    user = AdminUser(username="admin")
    user.set_password("secret123")
    db.session.add(user)
    db.session.commit()

    with client:
        client.post(
            "/admin/login",
            data={"username": "admin", "password": "secret123"},
            follow_redirects=True,
        )
        approve_response = client.post(
            f"/admin/clubs/{club.id}/approve",
            follow_redirects=True,
        )

    assert approve_response.status_code == 200
    db.session.refresh(club)
    assert club.is_approved is True
    assert club.approved_at is not None