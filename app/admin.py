from __future__ import annotations

import os
import urllib.parse
from datetime import datetime

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask import Response
from sqlalchemy import func

from .admin_auth import admin_login_required, get_current_admin_user, login_admin_user, logout_admin_user
from .extensions import db
from .models import (
    AdminUser,
    CertificateTemplate,
    Contest,
    ContestClub,
    ContestCategory,
    ContestEdition,
    ContestLog,
    ContestRule,
    QSOEntry,
)
from .services.scoring import compile_rules, score_log

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.before_request
def require_admin_login():
    """Redirect anonymous requests away from protected admin pages."""
    allowed_endpoints = {
        "admin.login",
        "admin.logout",
    }
    if request.endpoint in allowed_endpoints:
        return None

    if get_current_admin_user() is None:
        flash("Zaloguj się, aby uzyskać dostęp do panelu administracyjnego.", "error")
        return redirect(url_for("admin.login", next=request.full_path if request.query_string else None))


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    """Authenticate an admin user and start an admin session."""
    if get_current_admin_user() is not None:
        return redirect(url_for("admin.dashboard"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""

        user = AdminUser.query.filter(func.lower(AdminUser.username) == username).first()
        if not user or not user.check_password(password):
            flash("Nieprawidłowa nazwa użytkownika lub hasło.", "error")
            return render_template("admin/login.html")

        login_admin_user(user)
        next_url = request.args.get("next") or url_for("admin.dashboard")
        return redirect(next_url)

    return render_template("admin/login.html")


@admin_bp.post("/logout")
def logout():
    """Terminate the current admin session."""
    logout_admin_user()
    flash("Wylogowano.", "success")
    return redirect(url_for("admin.login"))


@admin_bp.route("/users", methods=["GET", "POST"])
def users():
    """List admin users and allow creation of additional admin accounts."""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        confirm_password = request.form.get("confirm_password") or ""

        if not username or not password:
            flash("Nazwa użytkownika i hasło są wymagane.", "error")
            return redirect(url_for("admin.users"))

        if password != confirm_password:
            flash("Hasła nie są takie same.", "error")
            return redirect(url_for("admin.users"))

        existing = AdminUser.query.filter(func.lower(AdminUser.username) == username).first()
        if existing:
            flash("Administrator o takiej nazwie użytkownika już istnieje.", "error")
            return redirect(url_for("admin.users"))

        user = AdminUser(username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash(f"Utworzono konto administratora: {username}.", "success")
        return redirect(url_for("admin.users"))

    users_list = AdminUser.query.order_by(AdminUser.created_at.desc()).all()
    return render_template("admin/users.html", users=users_list)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@admin_bp.route("/")
@admin_login_required
def dashboard():
    """Render aggregate administration metrics for contests, logs, and QSOs."""
    contest_count = Contest.query.count()
    edition_count = ContestEdition.query.count()
    log_count = ContestLog.query.count()
    qso_count = QSOEntry.query.count()

    recent_logs = (
        ContestLog.query.order_by(ContestLog.uploaded_at.desc()).limit(10).all()
    )

    edition_status_counts = (
        db.session.query(ContestEdition.status, func.count(ContestEdition.id))
        .group_by(ContestEdition.status)
        .all()
    )

    log_status_counts = (
        db.session.query(ContestLog.submission_status, func.count(ContestLog.id))
        .group_by(ContestLog.submission_status)
        .all()
    )

    return render_template(
        "admin/dashboard.html",
        contest_count=contest_count,
        edition_count=edition_count,
        log_count=log_count,
        qso_count=qso_count,
        recent_logs=recent_logs,
        edition_status_counts=dict(edition_status_counts),
        log_status_counts=dict(log_status_counts),
    )


# ---------------------------------------------------------------------------
# Contests
# ---------------------------------------------------------------------------

@admin_bp.route("/clubs")
@admin_login_required
def clubs():
    """List submitted clubs and their approval status."""
    status = (request.args.get("status") or "pending").strip().lower()
    query = ContestClub.query

    if status == "pending":
        query = query.filter(ContestClub.is_approved.is_(False))
    elif status == "approved":
        query = query.filter(ContestClub.is_approved.is_(True))
    else:
        status = "all"

    clubs_list = query.order_by(ContestClub.is_approved.asc(), ContestClub.created_at.desc()).all()
    pending_count = ContestClub.query.filter(ContestClub.is_approved.is_(False)).count()
    return render_template(
        "admin/clubs.html",
        clubs=clubs_list,
        selected_status=status,
        pending_count=pending_count,
    )


@admin_bp.post("/clubs/<int:club_id>/approve")
@admin_login_required
def approve_club(club_id: int):
    """Approve a submitted club so it becomes selectable in public forms."""
    club = ContestClub.query.get_or_404(club_id)
    if club.is_approved:
        flash("Klub jest już zaakceptowany.", "success")
        return redirect(url_for("admin.clubs"))

    club.is_approved = True
    club.approved_at = datetime.utcnow()
    db.session.commit()
    flash(f"Zaakceptowano klub '{club.name}'.", "success")
    return redirect(url_for("admin.clubs", status="pending"))

@admin_bp.route("/contests")
@admin_login_required
def contests():
    """List all contests in the admin panel."""
    contests_list = (
        Contest.query.order_by(Contest.name.asc()).all()
    )
    return render_template("admin/contests.html", contests=contests_list)


@admin_bp.route("/contests/<int:contest_id>/edit", methods=["GET", "POST"])
@admin_login_required
def edit_contest(contest_id: int):
    """Edit the metadata of an existing contest."""
    contest = Contest.query.get_or_404(contest_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        is_yearly_recurring = request.form.get("is_yearly_recurring") == "on"

        if not name:
            flash("Nazwa zawodów jest wymagana.", "error")
            return redirect(url_for("admin.edit_contest", contest_id=contest.id))

        conflict = Contest.query.filter(
            Contest.name == name, Contest.id != contest.id
        ).first()
        if conflict:
            flash("Inne zawody o tej nazwie już istnieją.", "error")
            return redirect(url_for("admin.edit_contest", contest_id=contest.id))

        contest.name = name
        contest.description = description or None
        contest.is_yearly_recurring = is_yearly_recurring
        db.session.commit()
        flash("Zaktualizowano zawody.", "success")
        return redirect(url_for("admin.contests"))

    return render_template("admin/contest_edit.html", contest=contest)


@admin_bp.post("/contests/<int:contest_id>/delete")
@admin_login_required
def delete_contest(contest_id: int):
    """Delete a contest and all dependent editions and logs."""
    contest = Contest.query.get_or_404(contest_id)
    db.session.delete(contest)
    db.session.commit()
    flash(f"Usunięto zawody '{contest.name}' wraz ze wszystkimi edycjami i logami.", "success")
    return redirect(url_for("admin.contests"))


# ---------------------------------------------------------------------------
# Editions
# ---------------------------------------------------------------------------

@admin_bp.route("/editions")
@admin_login_required
def editions():
    """List contest editions with optional filtering by contest."""
    contest_id = request.args.get("contest_id", type=int)
    query = ContestEdition.query.join(Contest)
    if contest_id:
        query = query.filter(ContestEdition.contest_id == contest_id)
    editions_list = query.order_by(Contest.name.asc(), ContestEdition.year.desc()).all()
    contests_list = Contest.query.order_by(Contest.name.asc()).all()
    return render_template(
        "admin/editions.html",
        editions=editions_list,
        contests=contests_list,
        selected_contest_id=contest_id,
    )


@admin_bp.route("/editions/<int:edition_id>/edit", methods=["GET", "POST"])
@admin_login_required
def edit_edition(edition_id: int):
    """Edit lifecycle dates and metadata for a contest edition."""
    edition = ContestEdition.query.get_or_404(edition_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        year_raw = request.form.get("year", "").strip()
        status = request.form.get("status", edition.status).strip()

        valid_statuses = {
            "draft",
            "accepting_logs",
            "submission_closed",
        }
        if status not in valid_statuses:
            flash("Nieprawidłowy status.", "error")
            return redirect(url_for("admin.edit_edition", edition_id=edition.id))

        try:
            year = int(year_raw)
        except (ValueError, TypeError):
            flash("Nieprawidłowy rok.", "error")
            return redirect(url_for("admin.edit_edition", edition_id=edition.id))

        conflict = ContestEdition.query.filter(
            ContestEdition.contest_id == edition.contest_id,
            ContestEdition.year == year,
            ContestEdition.id != edition.id,
        ).first()
        if conflict:
            flash("Inna edycja tych zawodów dla tego roku już istnieje.", "error")
            return redirect(url_for("admin.edit_edition", edition_id=edition.id))

        edition.name = name or None
        edition.description = description or None
        edition.year = year
        edition.status = status

        def _parse_dt(val: str | None) -> datetime | None:
            """Parse datetime input from the edition edit form."""
            token = (val or "").strip()
            if not token:
                return None
            for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    return datetime.strptime(token, fmt)
                except ValueError:
                    continue
            return None

        edition.submission_open_at = _parse_dt(request.form.get("submission_open_at"))
        edition.submission_deadline = _parse_dt(request.form.get("submission_deadline"))

        db.session.commit()
        flash("Zaktualizowano edycję.", "success")
        return redirect(url_for("admin.editions", contest_id=edition.contest_id))

    return render_template("admin/edition_edit.html", edition=edition)


@admin_bp.post("/editions/<int:edition_id>/delete")
@admin_login_required
def delete_edition(edition_id: int):
    """Delete one edition and its related logs and rules."""
    edition = ContestEdition.query.get_or_404(edition_id)
    contest_id = edition.contest_id
    name = edition.display_name
    db.session.delete(edition)
    db.session.commit()
    flash(f"Usunięto edycję '{name}' wraz ze wszystkimi logami.", "success")
    return redirect(url_for("admin.editions", contest_id=contest_id))


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

@admin_bp.route("/logs")
@admin_login_required
def logs():
    """List uploaded logs with admin filters."""
    edition_id = request.args.get("edition_id", type=int)
    status_filter = (request.args.get("status") or "all").strip()
    callsign = (request.args.get("callsign") or "").strip().upper()

    editions_list = (
        ContestEdition.query.join(Contest)
        .order_by(Contest.name.asc(), ContestEdition.year.desc())
        .all()
    )

    query = ContestLog.query.join(ContestEdition).join(Contest)
    if edition_id:
        query = query.filter(ContestLog.edition_id == edition_id)
    if status_filter != "all":
        query = query.filter(ContestLog.submission_status == status_filter)
    if callsign:
        query = query.filter(func.upper(ContestLog.station_call).contains(callsign))

    logs_list = query.order_by(ContestLog.uploaded_at.desc()).limit(500).all()

    return render_template(
        "admin/logs.html",
        logs=logs_list,
        editions=editions_list,
        selected_edition_id=edition_id,
        selected_status=status_filter,
        callsign=callsign,
    )


@admin_bp.route("/logs/<int:log_id>/edit", methods=["GET", "POST"])
@admin_login_required
def edit_log(log_id: int):
    """Edit submission metadata for a log or trigger a rescore."""
    contest_log = ContestLog.query.get_or_404(log_id)
    categories = ContestCategory.query.filter_by(
        edition_id=contest_log.edition_id
    ).order_by(ContestCategory.name.asc()).all()

    if request.method == "POST":
        action = request.form.get("action", "save")

        if action == "rescore":
            rules = compile_rules(contest_log.edition)
            results = score_log(contest_log, rules)
            contest_log.valid_qso_count = results["valid_qso_count"]
            contest_log.invalid_qso_count = results["invalid_qso_count"]
            contest_log.computed_score = results["computed_score"]
            db.session.commit()
            flash(
                f"Przeliczono log. Poprawne QSO: {contest_log.valid_qso_count}, "
                f"Wynik: {contest_log.computed_score}.",
                "success",
            )
            return redirect(url_for("admin.edit_log", log_id=contest_log.id))

        # save metadata
        new_status = (request.form.get("submission_status") or "accepted").strip()
        valid_statuses = {"accepted", "rejected", "pending", "checklog"}
        if new_status not in valid_statuses:
            flash("Nieprawidłowy status zgłoszenia.", "error")
            return redirect(url_for("admin.edit_log", log_id=contest_log.id))

        contest_log.submission_status = new_status
        contest_log.submission_notes = request.form.get("submission_notes", "").strip() or None
        contest_log.is_checklog = request.form.get("is_checklog") == "on"

        category_id = request.form.get("category_id", type=int)
        if category_id:
            cat = ContestCategory.query.filter_by(
                id=category_id, edition_id=contest_log.edition_id
            ).first()
            contest_log.category_id = cat.id if cat else None
        else:
            contest_log.category_id = None

        db.session.commit()
        flash("Zaktualizowano log.", "success")
        return redirect(url_for("admin.logs", edition_id=contest_log.edition_id))

    return render_template(
        "admin/log_edit.html",
        log=contest_log,
        categories=categories,
    )


@admin_bp.post("/logs/<int:log_id>/delete")
@admin_login_required
def delete_log(log_id: int):
    """Delete an uploaded contest log."""
    contest_log = ContestLog.query.get_or_404(log_id)
    edition_id = contest_log.edition_id
    callsign = contest_log.station_call or str(log_id)
    db.session.delete(contest_log)
    db.session.commit()
    flash(f"Usunięto log '{callsign}'.", "success")
    return redirect(url_for("admin.logs", edition_id=edition_id))


# ---------------------------------------------------------------------------
# Log check summary
# ---------------------------------------------------------------------------

def _build_check_summary(log) -> str:
    """Return a plain-text log check summary for the given ContestLog."""
    invalid_qsos = [q for q in log.qsos if not q.is_valid]

    lines = [
        "Log Check Summary",
        "=================",
        f"Contest : {log.contest.name}",
        f"Edition : {log.edition.display_name}",
        f"Callsign: {log.station_call or 'N/A'}",
        f"Category: {log.category.name if log.category else 'N/A'}",
        f"Status  : {log.submission_status}",
        "",
        "Scoring Results",
        "---------------",
        f"Total QSOs    : {len(log.qsos)}",
        f"Valid QSOs    : {log.valid_qso_count}",
        f"Invalid QSOs  : {log.invalid_qso_count}",
        f"Computed Score: {log.computed_score}",
        f"Claimed Score : {log.claimed_score if log.claimed_score is not None else 'N/A'}",
    ]

    if invalid_qsos:
        lines += [
            "",
            f"Invalid QSO Details ({len(invalid_qsos)} entries)",
            "-----------------------------------",
        ]
        for q in invalid_qsos:
            reason = q.invalid_reason or "unknown reason"
            worked = q.worked_call or "?"
            date_time = f"{q.qso_date or '?'} {q.qso_time or '?'}"
            lines.append(f"  Line {q.line_no:>4}: {date_time} {worked:<14} — {reason}")

    if log.submission_notes:
        lines += [
            "",
            "Admin Notes",
            "-----------",
            log.submission_notes,
        ]

    lines += [
        "",
        f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
    ]

    return "\n".join(lines)


@admin_bp.get("/logs/<int:log_id>/check-summary")
@admin_login_required
def log_check_summary(log_id: int):
    """Render a plain-text check summary preview for one log."""
    log = ContestLog.query.get_or_404(log_id)
    summary_text = _build_check_summary(log)

    recipient = log.submitter_email or ""
    subject = urllib.parse.quote(
        f"Log Check Summary — {log.contest.name} {log.edition.year} — {log.station_call or ''}",
        safe="",
    )
    body = urllib.parse.quote(summary_text, safe="")
    mailto = f"mailto:{recipient}?subject={subject}&body={body}"

    return render_template(
        "admin/log_check_summary.html",
        log=log,
        summary_text=summary_text,
        mailto=mailto,
        has_email=bool(recipient),
    )


@admin_bp.get("/logs/<int:log_id>/check-summary/download")
@admin_login_required
def log_check_summary_download(log_id: int):
    """Download the generated check summary as a text file."""
    log = ContestLog.query.get_or_404(log_id)
    summary_text = _build_check_summary(log)
    filename = f"check_summary_{log.station_call or log_id}_{log.edition.year}.txt"
    return Response(
        summary_text,
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Rules (inline quick-edit from admin logs/edition view)
# ---------------------------------------------------------------------------

@admin_bp.route("/editions/<int:edition_id>/rules")
@admin_login_required
def edition_rules(edition_id: int):
    """Open the focused admin editor for one edition's scoring rules."""
    edition = ContestEdition.query.get_or_404(edition_id)
    return render_template("admin/edition_rules.html", edition=edition)


@admin_bp.post("/editions/<int:edition_id>/rules/<int:rule_id>/update")
@admin_login_required
def update_rule(edition_id: int, rule_id: int):
    """Update the value of an existing scoring rule from the admin panel."""
    edition = ContestEdition.query.get_or_404(edition_id)
    rule = ContestRule.query.filter_by(id=rule_id, edition_id=edition.id).first_or_404()

    value = request.form.get("value", "").strip()
    if not value:
        flash("Wartość reguły nie może być pusta.", "error")
        return redirect(url_for("admin.edition_rules", edition_id=edition.id))

    rule.value = value
    db.session.commit()
    flash(f"Zaktualizowano regułę '{rule.key}'.", "success")
    return redirect(url_for("admin.edition_rules", edition_id=edition.id))


# ---------------------------------------------------------------------------
# Certificate templates
# ---------------------------------------------------------------------------

_CERT_TEMPLATE_DIR = "cert_templates"
_ALLOWED_MIMETYPES = {"image/svg+xml", "text/xml", "application/xml", "text/plain"}


def _cert_template_path(edition_id: int) -> str:
    """Return the on-disk path used to store an edition certificate template."""
    base = os.path.join(current_app.instance_path, _CERT_TEMPLATE_DIR)
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, f"{edition_id}.svg")


@admin_bp.post("/editions/<int:edition_id>/cert-template/upload")
@admin_login_required
def upload_cert_template(edition_id: int):
    """Upload or replace the SVG template used for certificate generation."""
    edition = ContestEdition.query.get_or_404(edition_id)
    f = request.files.get("template_file")
    if not f or not f.filename:
        flash("Nie wybrano pliku.", "error")
        return redirect(url_for("admin.edit_edition", edition_id=edition_id))

    if not f.filename.lower().endswith(".svg"):
        flash("Jako szablon dyplomu można wgrać tylko plik SVG.", "error")
        return redirect(url_for("admin.edit_edition", edition_id=edition_id))

    content = f.read()
    if not content.strip():
        flash("Wgrany plik jest pusty.", "error")
        return redirect(url_for("admin.edit_edition", edition_id=edition_id))

    # Persist the file
    dest = _cert_template_path(edition_id)
    with open(dest, "wb") as fh:
        fh.write(content)

    # Upsert the DB record
    record = edition.certificate_template
    if record is None:
        record = CertificateTemplate(edition_id=edition_id, original_filename=f.filename)
        db.session.add(record)
    else:
        record.original_filename = f.filename
        record.uploaded_at = datetime.utcnow()
    db.session.commit()
    flash("Wgrano szablon dyplomu.", "success")
    return redirect(url_for("admin.edit_edition", edition_id=edition_id))


@admin_bp.post("/editions/<int:edition_id>/cert-template/delete")
@admin_login_required
def delete_cert_template(edition_id: int):
    """Delete the custom certificate template associated with an edition."""
    edition = ContestEdition.query.get_or_404(edition_id)
    record = edition.certificate_template
    if record:
        dest = _cert_template_path(edition_id)
        if os.path.exists(dest):
            os.remove(dest)
        db.session.delete(record)
        db.session.commit()
        flash("Usunięto szablon dyplomu.", "success")
    else:
        flash("Brak szablonu do usunięcia.", "error")
    return redirect(url_for("admin.edit_edition", edition_id=edition_id))
