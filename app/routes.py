from __future__ import annotations

import os
from datetime import datetime
from io import BytesIO
from pathlib import Path

from flask import Blueprint, Response, current_app, flash, redirect, render_template, request, send_file, url_for
from sqlalchemy import and_, case, func
from sqlalchemy.orm import joinedload

from .admin_auth import get_current_admin_user
from .extensions import db
from .models import (
    CertificateTemplate,
    Contest,
    ContestCategory,
    ContestClub,
    ContestClubMember,
    ContestEdition,
    ContestLog,
    ContestRule,
    ContestRuleNote,
    QSOEntry,
)
from .services.cabrillo import parse_cabrillo, validate_cabrillo_payload
from .services.notifications import send_log_upload_confirmation
from .services.scoring import compile_rules, score_log


bp = Blueprint("main", __name__)


def _default_rule_definitions() -> list[tuple[str, str, str]]:
    """Return the built-in scoring rule set used for new contest editions."""
    return [
        ("base_qso_points", "1", "Points awarded for each valid QSO."),
        ("qso_points_cw", "0", "If >0, overrides base points for CW QSOs."),
        ("qso_points_ssb", "0", "If >0, overrides base points for SSB/PHONE QSOs."),
        ("qso_points_digi", "0", "If >0, overrides base points for digital QSOs."),
        (
            "digi_modes",
            "DIGI,RTTY,FT8,PSK31,PSK63,JT65,FT4",
            "Comma-separated mode tokens treated as digital.",
        ),
        ("allowed_bands", "", "Comma-separated list, e.g. 20M,40M"),
        ("allowed_modes", "", "Comma-separated list, e.g. CW,SSB"),
        ("dupe_scope", "band_mode", "band_mode, band, or global"),
        ("bonus_per_unique_prefix", "0", "Bonus for each unique callsign prefix."),
        ("multiplier_per_unique_band", "0", "Multiplier increment per unique worked band."),
        ("multiplier_per_unique_mode", "0", "Multiplier increment per unique worked mode."),
        ("multiplier_per_unique_prefix", "0", "Multiplier increment per unique callsign prefix."),
        (
            "premium_station_points",
            "",
            "Comma-separated CALL:POINTS list, e.g. SP9ABC:5,K1TTT:10",
        ),
        (
            "require_cross_log_match",
            "false",
            "If true, QSO is valid only when reciprocal QSO exists in another station log.",
        ),
        (
            "cross_log_time_tolerance_min",
            "3",
            "Allowed time difference in minutes for reciprocal cross-log match.",
        ),
        (
            "cross_log_check_exchange",
            "false",
            "If true, sent and received exchanges must also match reciprocally.",
        ),
    ]


def _create_default_rules_for_edition(edition: ContestEdition) -> None:
    """Attach the default scoring rules to a newly created edition."""
    for key, value, rule_description in _default_rule_definitions():
        db.session.add(
            ContestRule(
                edition_id=edition.id,
                key=key,
                value=value,
                description=rule_description,
            )
        )


def _parse_datetime_local(value: str | None) -> datetime | None:
    """Parse a local datetime value from HTML form submissions."""
    token = (value or "").strip()
    if not token:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(token, fmt)
        except ValueError:
            continue
    return None


def _parse_int(value: str | None) -> int | None:
    """Convert an optional form field into an integer."""
    token = (value or "").strip()
    if not token:
        return None
    try:
        return int(token)
    except ValueError:
        return None


def _decode_uploaded_text_file(file_storage) -> str | None:
    """Return decoded text content for plain-text uploads, otherwise None."""
    raw_bytes = file_storage.read()
    file_storage.stream.seek(0)

    if not raw_bytes:
        return ""

    if b"\x00" in raw_bytes:
        return None

    try:
        return raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


@bp.route("/")
def index():
    """Render the public home page with recent activity."""
    contests = Contest.query.order_by(Contest.created_at.desc()).all()
    recent_logs = ContestLog.query.order_by(ContestLog.submitted_at.desc()).limit(10).all()
    return render_template(
        "index.html",
        contests=contests,
        recent_logs=recent_logs,
    )


@bp.route("/contests", methods=["GET", "POST"])
def contests():
    """List contests and allow admins to create a new contest with its first edition."""
    can_manage = get_current_admin_user() is not None

    if request.method == "POST":
        if not can_manage:
            flash("Tylko administratorzy mogą tworzyć zawody.", "error")
            return redirect(url_for("admin.login", next=url_for("main.contests")))

        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        is_yearly_recurring = request.form.get("is_yearly_recurring", "on") == "on"
        first_edition_year = request.form.get("first_edition_year", type=int) or datetime.utcnow().year

        if not name:
            flash("Nazwa zawodów jest wymagana.", "error")
            return redirect(url_for("main.contests"))

        existing = Contest.query.filter_by(name=name).first()
        if existing:
            flash("Zawody o tej nazwie już istnieją.", "error")
            return redirect(url_for("main.contests"))

        contest = Contest(
            name=name,
            description=description or None,
            is_yearly_recurring=is_yearly_recurring,
        )
        db.session.add(contest)
        db.session.flush()

        edition = ContestEdition(contest_id=contest.id, year=first_edition_year, status="draft")
        db.session.add(edition)
        db.session.flush()

        _create_default_rules_for_edition(edition)

        db.session.commit()
        flash("Utworzono definicję zawodów i pierwszą edycję.", "success")
        return redirect(url_for("main.contest_detail", contest_id=contest.id))

    contests_list = Contest.query.order_by(Contest.name.asc()).all()
    return render_template("contests.html", contests=contests_list, can_manage=can_manage)


@bp.route("/contests/<int:contest_id>")
def contest_detail(contest_id: int):
    """Show one contest and the summary metrics for its editions."""
    contest = Contest.query.get_or_404(contest_id)
    can_manage = get_current_admin_user() is not None

    edition_stats = (
        db.session.query(
            ContestEdition,
            func.count(ContestLog.id).label("log_count"),
            func.coalesce(func.sum(ContestLog.computed_score), 0).label("score_sum"),
        )
        .outerjoin(ContestLog, ContestLog.edition_id == ContestEdition.id)
        .filter(ContestEdition.contest_id == contest.id)
        .group_by(ContestEdition.id)
        .order_by(ContestEdition.year.desc())
        .all()
    )

    return render_template(
        "contest_detail.html",
        contest=contest,
        edition_stats=edition_stats,
        can_manage=can_manage,
    )


@bp.post("/contests/<int:contest_id>/editions")
def add_edition(contest_id: int):
    """Create another annual edition for an existing contest."""
    contest = Contest.query.get_or_404(contest_id)

    if get_current_admin_user() is None:
        flash("Tylko administratorzy mogą tworzyć edycje.", "error")
        return redirect(url_for("admin.login", next=url_for("main.contest_detail", contest_id=contest.id)))

    year = request.form.get("year", type=int)
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()

    if not year:
        flash("Rok edycji jest wymagany.", "error")
        return redirect(url_for("main.contest_detail", contest_id=contest.id))

    existing = ContestEdition.query.filter_by(contest_id=contest.id, year=year).first()
    if existing:
        flash("Te zawody mają już edycję dla tego roku.", "error")
        return redirect(url_for("main.contest_detail", contest_id=contest.id))

    edition = ContestEdition(
        contest_id=contest.id,
        year=year,
        name=name or None,
        description=description or None,
        status="draft",
    )
    db.session.add(edition)
    db.session.flush()

    source_edition_id = request.form.get("copy_rules_from", type=int)
    if source_edition_id:
        source_edition = ContestEdition.query.filter_by(id=source_edition_id, contest_id=contest.id).first()
        if source_edition and source_edition.rules:
            for source_rule in source_edition.rules:
                db.session.add(
                    ContestRule(
                        edition_id=edition.id,
                        key=source_rule.key,
                        value=source_rule.value,
                        description=source_rule.description,
                    )
                )
        else:
            _create_default_rules_for_edition(edition)
    else:
        _create_default_rules_for_edition(edition)

    db.session.commit()
    flash("Utworzono edycję.", "success")
    return redirect(url_for("main.contest_detail", contest_id=contest.id))


@bp.post("/contests/<int:contest_id>/rule-notes")
def add_rule_note(contest_id: int):
    """Store a free-form official rule note for a contest (admin only)."""
    contest = Contest.query.get_or_404(contest_id)

    if get_current_admin_user() is None:
        flash("Tylko administratorzy mogą zarządzać notatkami do regulaminu.", "error")
        return redirect(url_for("admin.login", next=url_for("main.contest_detail", contest_id=contest.id)))

    title = request.form.get("title", "").strip()
    content = request.form.get("content", "").strip()

    if not title or not content:
        flash("Tytuł i treść notatki do regulaminu są wymagane.", "error")
        return redirect(url_for("main.contest_detail", contest_id=contest.id))

    db.session.add(ContestRuleNote(contest_id=contest.id, title=title, content=content))
    db.session.commit()
    flash("Zapisano notatkę do regulaminu.", "success")
    return redirect(url_for("main.contest_detail", contest_id=contest.id))


@bp.post("/contests/<int:contest_id>/rule-notes/<int:note_id>/delete")
def delete_rule_note(contest_id: int, note_id: int):
    """Delete a stored official rule note from a contest (admin only)."""
    contest = Contest.query.get_or_404(contest_id)

    if get_current_admin_user() is None:
        flash("Tylko administratorzy mogą zarządzać notatkami do regulaminu.", "error")
        return redirect(url_for("admin.login", next=url_for("main.contest_detail", contest_id=contest.id)))

    note = ContestRuleNote.query.filter_by(id=note_id, contest_id=contest.id).first_or_404()
    db.session.delete(note)
    db.session.commit()
    flash("Usunięto notatkę do regulaminu.", "success")
    return redirect(url_for("main.contest_detail", contest_id=contest.id))


@bp.route("/editions/<int:edition_id>", methods=["GET", "POST"])
def edition_detail(edition_id: int):
    """Display one edition and allow rule creation from the public editor."""
    edition = ContestEdition.query.get_or_404(edition_id)

    if request.method == "POST":
        key = request.form.get("key", "").strip()
        value = request.form.get("value", "").strip()
        description = request.form.get("description", "").strip()

        if not key or not value:
            flash("Klucz i wartość reguły są wymagane.", "error")
            return redirect(url_for("main.edition_detail", edition_id=edition.id))

        existing = ContestRule.query.filter_by(edition_id=edition.id, key=key).first()
        if existing:
            flash("Klucz reguły już istnieje. Użyj opcji Edytuj przy tej regule w tabeli.", "error")
            return redirect(url_for("main.edition_detail", edition_id=edition.id))

        db.session.add(
            ContestRule(
                edition_id=edition.id,
                key=key,
                value=value,
                description=description or None,
            )
        )
        flash("Dodano regułę.", "success")

        db.session.commit()
        return redirect(url_for("main.edition_detail", edition_id=edition.id))

    return render_template("edition_detail.html", edition=edition)


@bp.post("/editions/<int:edition_id>/lifecycle")
def update_edition_lifecycle(edition_id: int):
    """Update the lifecycle status and submission window for an edition."""
    edition = ContestEdition.query.get_or_404(edition_id)
    new_status = (request.form.get("status") or "draft").strip()
    valid_statuses = {
        "draft",
        "accepting_logs",
        "submission_closed",
    }
    if new_status not in valid_statuses:
        flash("Nieprawidłowy status cyklu życia.", "error")
        return redirect(url_for("main.edition_detail", edition_id=edition.id))

    open_at = _parse_datetime_local(request.form.get("submission_open_at"))
    deadline = _parse_datetime_local(request.form.get("submission_deadline"))

    if open_at and deadline and open_at > deadline:
        flash("Data rozpoczęcia naboru nie może być późniejsza niż termin końcowy.", "error")
        return redirect(url_for("main.edition_detail", edition_id=edition.id))

    edition.status = new_status
    edition.submission_open_at = open_at
    edition.submission_deadline = deadline

    db.session.commit()
    flash("Zaktualizowano cykl życia edycji.", "success")
    return redirect(url_for("main.edition_detail", edition_id=edition.id))


@bp.post("/editions/<int:edition_id>/rules/<int:rule_id>/delete")
def delete_rule(edition_id: int, rule_id: int):
    """Delete one scoring rule from an edition."""
    edition = ContestEdition.query.get_or_404(edition_id)
    rule = ContestRule.query.filter_by(id=rule_id, edition_id=edition.id).first_or_404()
    db.session.delete(rule)
    db.session.commit()
    flash("Usunięto regułę.", "success")
    return redirect(url_for("main.edition_detail", edition_id=edition.id))


@bp.post("/editions/<int:edition_id>/rules/<int:rule_id>")
def update_rule(edition_id: int, rule_id: int):
    """Update an existing scoring rule for an edition."""
    edition = ContestEdition.query.get_or_404(edition_id)
    rule = ContestRule.query.filter_by(id=rule_id, edition_id=edition.id).first_or_404()

    key = request.form.get("key", "").strip()
    value = request.form.get("value", "").strip()
    description = request.form.get("description", "").strip()

    if not key or not value:
        flash("Klucz i wartość reguły są wymagane.", "error")
        return redirect(url_for("main.edition_detail", edition_id=edition.id))

    key_taken = ContestRule.query.filter(
        ContestRule.edition_id == edition.id,
        ContestRule.key == key,
        ContestRule.id != rule.id,
    ).first()
    if key_taken:
        flash("Inna reguła już używa tego klucza.", "error")
        return redirect(url_for("main.edition_detail", edition_id=edition.id))

    rule.key = key
    rule.value = value
    rule.description = description or None

    db.session.commit()
    flash("Zaktualizowano regułę.", "success")
    return redirect(url_for("main.edition_detail", edition_id=edition.id))


@bp.post("/editions/<int:edition_id>/categories")
def add_category(edition_id: int):
    """Create a participant category for the selected edition."""
    edition = ContestEdition.query.get_or_404(edition_id)
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()

    if not name:
        flash("Nazwa kategorii jest wymagana.", "error")
        return redirect(url_for("main.edition_detail", edition_id=edition.id))

    exists = ContestCategory.query.filter_by(edition_id=edition.id, name=name).first()
    if exists:
        flash("Kategoria o tej nazwie już istnieje w tej edycji.", "error")
        return redirect(url_for("main.edition_detail", edition_id=edition.id))

    db.session.add(
        ContestCategory(
            edition_id=edition.id,
            name=name,
            description=description or None,
        )
    )
    db.session.commit()
    flash("Dodano kategorię uczestnika.", "success")
    return redirect(url_for("main.edition_detail", edition_id=edition.id))


@bp.post("/editions/<int:edition_id>/categories/<int:category_id>/delete")
def delete_category(edition_id: int, category_id: int):
    """Delete a participant category when it is not referenced by logs."""
    edition = ContestEdition.query.get_or_404(edition_id)
    category = ContestCategory.query.filter_by(id=category_id, edition_id=edition.id).first_or_404()

    if category.logs:
        flash("Nie można usunąć kategorii użytej już w przesłanych logach.", "error")
        return redirect(url_for("main.edition_detail", edition_id=edition.id))

    db.session.delete(category)
    db.session.commit()
    flash("Usunięto kategorię uczestnika.", "success")
    return redirect(url_for("main.edition_detail", edition_id=edition.id))


@bp.route("/contests/<int:contest_id>/summary")
def contest_summary(contest_id: int):
    """Render cross-edition scoring aggregates for a contest."""
    contest = Contest.query.get_or_404(contest_id)

    selected_category_filter = (request.args.get("category") or "all").strip()
    category_names = [
        row.name
        for row in (
            db.session.query(ContestCategory.name)
            .join(ContestEdition, ContestEdition.id == ContestCategory.edition_id)
            .filter(ContestEdition.contest_id == contest.id)
            .distinct()
            .order_by(ContestCategory.name.asc())
            .all()
        )
    ]

    if selected_category_filter not in {"all", "__uncategorized__", *category_names}:
        selected_category_filter = "all"

    eligible_result_condition = and_(
        ContestLog.id.is_not(None),
        ContestLog.submission_status == "accepted",
        ContestLog.is_checklog.is_(False),
    )

    if selected_category_filter == "__uncategorized__":
        match_condition = and_(eligible_result_condition, ContestLog.category_id.is_(None))
    elif selected_category_filter != "all":
        match_condition = and_(eligible_result_condition, ContestCategory.name == selected_category_filter)
    else:
        match_condition = eligible_result_condition

    matched_log_count = func.sum(case((match_condition, 1), else_=0))
    matched_score_sum = func.sum(case((match_condition, ContestLog.computed_score), else_=0))
    matched_valid_qsos = func.sum(case((match_condition, ContestLog.valid_qso_count), else_=0))
    matched_invalid_qsos = func.sum(case((match_condition, ContestLog.invalid_qso_count), else_=0))
    matched_score_avg = case(
        (matched_log_count > 0, matched_score_sum * 1.0 / matched_log_count),
        else_=0.0,
    )

    edition_rows = (
        db.session.query(
            ContestEdition,
            func.coalesce(matched_log_count, 0).label("log_count"),
            func.coalesce(matched_score_sum, 0).label("score_sum"),
            func.coalesce(matched_score_avg, 0).label("score_avg"),
            func.coalesce(matched_valid_qsos, 0).label("valid_qsos"),
            func.coalesce(matched_invalid_qsos, 0).label("invalid_qsos"),
        )
        .outerjoin(ContestLog, ContestLog.edition_id == ContestEdition.id)
        .outerjoin(ContestCategory, ContestCategory.id == ContestLog.category_id)
        .filter(ContestEdition.contest_id == contest.id)
        .group_by(ContestEdition.id)
        .order_by(ContestEdition.year.desc())
        .all()
    )

    overall = {
        "logs": sum(int(row.log_count or 0) for row in edition_rows),
        "score_sum": sum(int(row.score_sum or 0) for row in edition_rows),
        "valid_qsos": sum(int(row.valid_qsos or 0) for row in edition_rows),
        "invalid_qsos": sum(int(row.invalid_qsos or 0) for row in edition_rows),
    }

    ranking_query = (
        ContestLog.query.options(
            joinedload(ContestLog.edition),
            joinedload(ContestLog.category),
        )
        .join(ContestEdition, ContestEdition.id == ContestLog.edition_id)
        .outerjoin(ContestCategory, ContestCategory.id == ContestLog.category_id)
        .filter(
            ContestEdition.contest_id == contest.id,
            ContestLog.submission_status == "accepted",
            ContestLog.is_checklog.is_(False),
        )
    )

    if selected_category_filter == "__uncategorized__":
        ranking_query = ranking_query.filter(ContestLog.category_id.is_(None))
    elif selected_category_filter != "all":
        ranking_query = ranking_query.filter(ContestCategory.name == selected_category_filter)

    ranked_logs = (
        ranking_query
        .order_by(
            ContestEdition.year.desc(),
            ContestEdition.id.desc(),
            func.coalesce(ContestCategory.name, "Bez kategorii").asc(),
            ContestLog.computed_score.desc(),
            func.coalesce(func.upper(ContestLog.station_call), "").asc(),
            ContestLog.uploaded_at.asc(),
        )
        .all()
    )

    classification_groups = []
    groups_by_key = {}
    for log in ranked_logs:
        category_label = log.category.name if log.category else "Bez kategorii"
        group_key = (log.edition_id, category_label)

        if group_key not in groups_by_key:
            group_data = {
                "edition": log.edition,
                "category_label": category_label,
                "rows": [],
            }
            groups_by_key[group_key] = group_data
            classification_groups.append(group_data)

        rows = groups_by_key[group_key]["rows"]
        rows.append({
            "rank": len(rows) + 1,
            "log": log,
        })

    return render_template(
        "contest_summary.html",
        contest=contest,
        edition_rows=edition_rows,
        overall=overall,
        category_names=category_names,
        selected_category_filter=selected_category_filter,
        classification_groups=classification_groups,
    )


@bp.route("/logs/received")
def logs_received():
    """List received submissions with public filtering by contest, edition, and status."""
    selected_contest_id = request.args.get("contest_id", type=int)
    selected_edition_id = request.args.get("edition_id", type=int)
    selected_status = (request.args.get("status") or "all").strip()
    callsign = (request.args.get("callsign") or "").strip().upper()

    contests = Contest.query.order_by(Contest.name.asc()).all()
    editions = ContestEdition.query.join(Contest).order_by(Contest.name.asc(), ContestEdition.year.desc()).all()

    query = ContestLog.query.join(ContestEdition).join(Contest)
    if selected_edition_id:
        query = query.filter(ContestLog.edition_id == selected_edition_id)
    elif selected_contest_id:
        query = query.filter(ContestEdition.contest_id == selected_contest_id)
    if selected_status != "all":
        query = query.filter(ContestLog.submission_status == selected_status)
    if callsign:
        query = query.filter(func.upper(ContestLog.station_call).contains(callsign))

    logs = query.order_by(ContestLog.submitted_at.desc()).limit(1000).all()

    status_counts = (
        db.session.query(ContestLog.submission_status, func.count(ContestLog.id))
        .group_by(ContestLog.submission_status)
        .all()
    )

    return render_template(
        "logs_received.html",
        contests=contests,
        editions=editions,
        logs=logs,
        selected_contest_id=selected_contest_id,
        selected_edition_id=selected_edition_id,
        selected_status=selected_status,
        callsign=callsign,
        status_counts=status_counts,
    )


@bp.route("/logs/upload", methods=["GET", "POST"])
def upload_log():
    """Accept Cabrillo uploads, parse them, and persist the scored submission."""
    editions = ContestEdition.query.join(Contest).order_by(Contest.name.asc(), ContestEdition.year.desc()).all()
    clubs = ContestClub.query.filter_by(is_approved=True).order_by(ContestClub.name.asc()).all()

    selected_edition_id = request.args.get("edition_id", type=int)
    selected_category_id = request.args.get("category_id", type=int)

    categories = ContestCategory.query.join(ContestEdition).join(Contest).order_by(
        Contest.name.asc(), ContestEdition.year.desc(), ContestCategory.name.asc()
    ).all()

    if request.method == "POST":
        edition_id = request.form.get("edition_id", type=int)
        selected_edition = ContestEdition.query.get(edition_id) if edition_id else None
        category_id = request.form.get("category_id", type=int)
        selected_category = ContestCategory.query.get(category_id) if category_id else None
        club_id = request.form.get("club_id", type=int)
        selected_club = ContestClub.query.get(club_id) if club_id else None

        if not selected_edition:
            flash("Najpierw wybierz edycję zawodów.", "error")
            return render_template(
                "upload_log.html",
                editions=editions,
                categories=categories,
                clubs=clubs,
                selected_edition_id=selected_edition_id,
                selected_category_id=selected_category_id,
            )

        if not selected_edition.can_accept_logs:
            flash("Ta edycja nie przyjmuje obecnie logów.", "error")
            return redirect(url_for("main.edition_detail", edition_id=selected_edition.id))

        if selected_category and selected_category.edition_id != selected_edition.id:
            flash("Wybrana kategoria uczestnika nie należy do wybranej edycji zawodów.", "error")
            return render_template(
                "upload_log.html",
                editions=editions,
                categories=categories,
                clubs=clubs,
                selected_edition_id=selected_edition.id,
                selected_category_id=selected_category.id if selected_category else None,
            )

        if selected_club and not selected_club.is_approved:
            flash("Wybrany klub oczekuje na akceptację i nie może być jeszcze użyty.", "error")
            return render_template(
                "upload_log.html",
                editions=editions,
                categories=categories,
                clubs=clubs,
                selected_edition_id=selected_edition.id,
                selected_category_id=selected_category.id if selected_category else None,
            )

        submitter_email = request.form.get("submitter_email", "").strip()
        location = request.form.get("location", "").strip()
        grid_locator = request.form.get("grid_locator", "").strip()
        power_category = request.form.get("power_category", "").strip()
        operators_count = _parse_int(request.form.get("operators_count"))
        declaration_accepted = request.form.get("declaration_accepted") == "on"
        is_checklog = request.form.get("is_checklog") == "on"

        if not submitter_email or "@" not in submitter_email:
            flash("Wymagany jest poprawny adres e-mail zgłaszającego.", "error")
            return render_template(
                "upload_log.html",
                editions=editions,
                categories=categories,
                clubs=clubs,
                selected_edition_id=selected_edition.id,
                selected_category_id=selected_category.id if selected_category else None,
            )

        if not location or not grid_locator or not power_category or not operators_count:
            flash("Lokalizacja, lokator, klasa mocy i liczba operatorów są wymagane.", "error")
            return render_template(
                "upload_log.html",
                editions=editions,
                categories=categories,
                clubs=clubs,
                selected_edition_id=selected_edition.id,
                selected_category_id=selected_category.id if selected_category else None,
            )

        if not declaration_accepted:
            flash("Przed wysłaniem musisz zaakceptować oświadczenie.", "error")
            return render_template(
                "upload_log.html",
                editions=editions,
                categories=categories,
                clubs=clubs,
                selected_edition_id=selected_edition.id,
                selected_category_id=selected_category.id if selected_category else None,
            )

        file = request.files.get("cabrillo_file")
        pasted_text = request.form.get("cabrillo_text", "").strip()
        content = ""

        if file and file.filename:
            decoded_content = _decode_uploaded_text_file(file)
            if decoded_content is None:
                flash("Przesłany log musi być plikiem tekstowym UTF-8.", "error")
                return render_template(
                    "upload_log.html",
                    editions=editions,
                    categories=categories,
                    clubs=clubs,
                    selected_edition_id=selected_edition.id,
                    selected_category_id=selected_category.id if selected_category else None,
                )

            content = decoded_content
        elif pasted_text:
            content = pasted_text
        else:
            flash("Podaj plik Cabrillo albo wklej tekst Cabrillo.", "error")
            return render_template(
                "upload_log.html",
                editions=editions,
                categories=categories,
                clubs=clubs,
                selected_edition_id=selected_edition.id,
                selected_category_id=selected_category.id if selected_category else None,
            )

        header, parsed_qsos = parse_cabrillo(content)

        format_errors = validate_cabrillo_payload(header, parsed_qsos)
        if format_errors:
            for error in format_errors:
                flash(error, "error")
            return render_template(
                "upload_log.html",
                editions=editions,
                categories=categories,
                clubs=clubs,
                selected_edition_id=selected_edition.id,
                selected_category_id=selected_category.id if selected_category else None,
            )

        contest_log = ContestLog(
            edition_id=selected_edition.id,
            category_id=selected_category.id if selected_category else None,
            club_id=selected_club.id if selected_club else None,
            submitter_email=submitter_email,
            location=location,
            grid_locator=grid_locator,
            power_category=power_category,
            operators_count=operators_count,
            declaration_accepted=declaration_accepted,
            is_checklog=is_checklog,
            station_call=header.get("CALLSIGN"),
            operator=header.get("OPERATORS"),
            raw_text=content,
            submission_status="accepted",
        )

        claimed_score = header.get("CLAIMED-SCORE")
        if claimed_score and claimed_score.isdigit():
            contest_log.claimed_score = int(claimed_score)

        db.session.add(contest_log)
        db.session.flush()

        for row in parsed_qsos:
            qso = QSOEntry(
                log_id=contest_log.id,
                line_no=row.line_no,
                raw_line=row.raw_line,
                qso_date=row.qso_date,
                qso_time=row.qso_time,
                frequency=row.frequency,
                band=row.band,
                mode=row.mode,
                worked_call=row.worked_call,
                sent_rst=row.sent_rst,
                sent_exchange=row.sent_exchange,
                recv_rst=row.recv_rst,
                recv_exchange=row.recv_exchange,
                is_valid=row.parse_error is None,
                invalid_reason=row.parse_error,
                points_awarded=0,
            )
            db.session.add(qso)

        db.session.flush()

        rules = compile_rules(selected_edition)
        results = score_log(contest_log, rules)

        contest_log.valid_qso_count = results["valid_qso_count"]
        contest_log.invalid_qso_count = results["invalid_qso_count"]
        contest_log.computed_score = results["computed_score"]

        db.session.commit()

        try:
            confirmation_sent = send_log_upload_confirmation(
                contest_log,
                detail_url=url_for("main.log_detail", log_id=contest_log.id, _external=True),
            )
        except Exception:
            current_app.logger.exception(
                "Failed to send log upload confirmation for log %s", contest_log.id
            )
            confirmation_sent = None

        success_message = (
            f"Przetworzono log. Poprawne QSO: {contest_log.valid_qso_count}, "
            f"Niepoprawne QSO: {contest_log.invalid_qso_count}"
        )
        if confirmation_sent:
            success_message += f". Wysłano e-mail potwierdzający na adres {contest_log.submitter_email}"
        flash(success_message, "success")
        if confirmation_sent is None:
            flash("Log został zapisany, ale nie udało się wysłać e-maila potwierdzającego.", "error")
        return redirect(url_for("main.log_detail", log_id=contest_log.id))

    return render_template(
        "upload_log.html",
        editions=editions,
        categories=categories,
        clubs=clubs,
        selected_edition_id=selected_edition_id,
        selected_category_id=selected_category_id,
    )


@bp.route("/logs/<int:log_id>")
def log_detail(log_id: int):
    """Show the stored log and a fresh scoring breakdown for auditability."""
    contest_log = ContestLog.query.get_or_404(log_id)
    rules = compile_rules(contest_log.edition)
    score_details = score_log(contest_log, rules)
    results = {
        "valid_qso_count": contest_log.valid_qso_count,
        "invalid_qso_count": contest_log.invalid_qso_count,
        "computed_score": contest_log.computed_score,
        "rules": rules,
        "mode_breakdown": score_details.get("mode_breakdown", {}),
        "multiplier_breakdown": score_details.get("multiplier_breakdown", {}),
        "premium_station_hits": score_details.get("premium_station_hits", {}),
        "qso_subtotal": score_details.get("qso_subtotal", 0),
        "premium_points_total": score_details.get("premium_points_total", 0),
        "total_multiplier": score_details.get("total_multiplier", 1),
        "multiplied_score": score_details.get("multiplied_score", contest_log.computed_score),
        "multiplier_bonus": score_details.get("multiplier_bonus", 0),
    }
    return render_template("log_detail.html", log=contest_log, results=results)


@bp.route("/help/contest-rule-fields")
def contest_rule_fields_help():
    """Serve the markdown reference describing supported rule keys."""
    help_path = Path(__file__).resolve().parents[1] / "docs" / "contest-rule-fields.md"
    if not help_path.exists():
        return Response("Help document not found.", mimetype="text/plain", status=404)
    return Response(help_path.read_text(encoding="utf-8"), mimetype="text/markdown")


@bp.route("/certificates")
def certificates_lookup():
    """Find accepted logs eligible for certificate download by callsign."""
    callsign = (request.args.get("callsign") or "").strip().upper()
    eligible_logs: list[ContestLog] = []

    if callsign:
        eligible_logs = (
            ContestLog.query.join(ContestEdition)
            .filter(
                func.upper(ContestLog.station_call) == callsign,
                ContestLog.submission_status == "accepted",
                ContestLog.is_checklog.is_(False),
            )
            .order_by(ContestEdition.year.desc(), ContestLog.computed_score.desc())
            .all()
        )

    log_ranks = {log.id: None for log in eligible_logs}

    return render_template(
        "certificates.html",
        callsign=callsign,
        eligible_logs=eligible_logs,
        log_ranks=log_ranks,
    )


@bp.route("/certificates/<int:log_id>/<string:fmt>")
def certificate_download(log_id: int, fmt: str):
    """Generate a TXT or SVG certificate for an eligible accepted log."""
    contest_log = ContestLog.query.get_or_404(log_id)
    if contest_log.submission_status != "accepted" or contest_log.is_checklog:
        flash("Dyplom nie jest dostępny dla tego logu.", "error")
        return redirect(url_for("main.certificates_lookup"))

    fmt = fmt.lower()
    station = contest_log.station_call or "UNKNOWN"
    category = contest_log.category.name if contest_log.category else "Bez kategorii"
    edition_year = contest_log.edition.year
    contest_name = contest_log.contest.name
    edition_name = contest_log.edition.display_name
    score = contest_log.computed_score
    issued = datetime.utcnow().strftime("%Y-%m-%d")
    rank_str = "N/A"

    text_payload = (
        f"Certificate of Participation\n"
        f"Contest: {contest_name}\n"
        f"Edition: {edition_name}\n"
        f"Callsign: {station}\n"
        f"Category: {category}\n"
        f"Score: {score}\n"
        f"Place: {rank_str}\n"
        f"Issued: {issued} UTC\n"
    )

    if fmt == "txt":
        return Response(
            text_payload,
            mimetype="text/plain",
            headers={"Content-Disposition": f"attachment; filename=certificate_{station}_{edition_year}.txt"},
        )

    if fmt == "svg":
        # Try to load a custom SVG template for this edition
        template_record: CertificateTemplate | None = contest_log.edition.certificate_template
        if template_record:
            template_path = os.path.join(
                current_app.instance_path, "cert_templates", f"{contest_log.edition_id}.svg"
            )
            if os.path.exists(template_path):
                with open(template_path, "r", encoding="utf-8") as fh:
                    svg = fh.read()
                # Substitute placeholders
                svg = (
                    svg
                    .replace("{{CALLSIGN}}", station)
                    .replace("{{SCORE}}", str(score))
                    .replace("{{RANK}}", rank_str)
                    .replace("{{CONTEST}}", contest_name)
                    .replace("{{EDITION}}", edition_name)
                    .replace("{{YEAR}}", str(edition_year))
                    .replace("{{CATEGORY}}", category)
                    .replace("{{ISSUED}}", issued)
                )
                return Response(
                    svg,
                    mimetype="image/svg+xml",
                    headers={"Content-Disposition": f"attachment; filename=certificate_{station}_{edition_year}.svg"},
                )

        # Built-in fallback SVG
        svg = (
            "<svg xmlns='http://www.w3.org/2000/svg' width='1100' height='780'>"
            "<rect width='100%' height='100%' fill='#fefcf7'/>"
            "<rect x='30' y='30' width='1040' height='720' fill='none' stroke='#005f73' stroke-width='4'/>"
            "<text x='550' y='150' text-anchor='middle' font-size='52' font-family='Georgia'>Certificate</text>"
            "<text x='550' y='205' text-anchor='middle' font-size='28' font-family='Georgia'>of Contest Participation</text>"
            f"<text x='550' y='310' text-anchor='middle' font-size='44' font-family='Georgia'>{station}</text>"
            f"<text x='550' y='375' text-anchor='middle' font-size='28' font-family='Georgia'>{contest_name} {edition_year}</text>"
            f"<text x='550' y='430' text-anchor='middle' font-size='24' font-family='Georgia'>Category: {category}</text>"
            f"<text x='550' y='485' text-anchor='middle' font-size='24' font-family='Georgia'>Score: {score}</text>"
            f"<text x='550' y='540' text-anchor='middle' font-size='24' font-family='Georgia'>Place: {rank_str}</text>"
            f"<text x='550' y='640' text-anchor='middle' font-size='20' font-family='Georgia'>Issued {issued}</text>"
            "</svg>"
        )
        return Response(
            svg,
            mimetype="image/svg+xml",
            headers={"Content-Disposition": f"attachment; filename=certificate_{station}_{edition_year}.svg"},
        )

    flash("Nieobsługiwany format dyplomu.", "error")
    return redirect(url_for("main.certificates_lookup", callsign=station))


@bp.route("/clubs", methods=["GET", "POST"])
def clubs_home():
    """List clubs and allow creating a new club record."""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        if not name:
            flash("Nazwa klubu jest wymagana.", "error")
            return redirect(url_for("main.clubs_home"))

        exists = ContestClub.query.filter(func.lower(ContestClub.name) == name.lower()).first()
        if exists:
            flash("Klub o tej nazwie już istnieje.", "error")
            return redirect(url_for("main.clubs_home"))

        db.session.add(ContestClub(name=name, description=description or None, is_approved=False))
        db.session.commit()
        flash("Zgłoszono klub. Oczekuje na akceptację administratora.", "success")
        return redirect(url_for("main.clubs_home"))

    clubs = ContestClub.query.order_by(ContestClub.is_approved.desc(), ContestClub.name.asc()).all()
    return render_template("clubs.html", clubs=clubs)


@bp.route("/clubs/<int:club_id>")
def club_detail(club_id: int):
    """Show one club, its members, and its per-edition score totals."""
    club = ContestClub.query.get_or_404(club_id)

    score_rows = (
        db.session.query(
            ContestEdition,
            func.count(ContestLog.id).label("log_count"),
            func.coalesce(func.sum(ContestLog.computed_score), 0).label("score_sum"),
        )
        .join(ContestLog, ContestLog.edition_id == ContestEdition.id)
        .filter(ContestLog.club_id == club.id)
        .group_by(ContestEdition.id)
        .order_by(ContestEdition.year.desc())
        .all()
    )

    return render_template("club_detail.html", club=club, score_rows=score_rows)


@bp.post("/clubs/<int:club_id>/members/import")
def import_club_members(club_id: int):
    """Bulk import club members from one callsign-per-line text payload."""
    club = ContestClub.query.get_or_404(club_id)
    payload = request.form.get("member_list", "")
    lines = [line.strip() for line in payload.splitlines() if line.strip()]

    created = 0
    skipped = 0
    for line in lines:
        parts = [part.strip() for part in line.split(",")]
        callsign = parts[0].upper() if parts else ""
        operator_name = parts[1] if len(parts) > 1 else None

        if not callsign:
            skipped += 1
            continue

        exists = ContestClubMember.query.filter_by(club_id=club.id, callsign=callsign).first()
        if exists:
            skipped += 1
            continue

        db.session.add(ContestClubMember(club_id=club.id, callsign=callsign, operator_name=operator_name or None))
        created += 1

    db.session.commit()
    flash(f"Import członków zakończony. Dodano: {created}, pominięto: {skipped}.", "success")
    return redirect(url_for("main.club_detail", club_id=club.id))


@bp.post("/clubs/<int:club_id>/members/<int:member_id>/delete")
def delete_club_member(club_id: int, member_id: int):
    """Remove a single member from a club."""
    club = ContestClub.query.get_or_404(club_id)
    member = ContestClubMember.query.filter_by(id=member_id, club_id=club.id).first_or_404()
    db.session.delete(member)
    db.session.commit()
    flash("Usunięto członka klubu.", "success")
    return redirect(url_for("main.club_detail", club_id=club.id))
