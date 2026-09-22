from __future__ import annotations

from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


class AdminUser(db.Model):
    """Administrative user allowed to access the protected back office."""

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_login_at = db.Column(db.DateTime, nullable=True)

    def set_password(self, password: str) -> None:
        """Hash and store a plaintext password for the admin account."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """Validate a plaintext password against the stored hash."""
        return check_password_hash(self.password_hash, password)


class Contest(db.Model):
    """Contest definition shared across annual editions."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)
    is_yearly_recurring = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    editions = db.relationship(
        "ContestEdition",
        back_populates="contest",
        cascade="all, delete-orphan",
        order_by="ContestEdition.year.desc()",
    )
    rule_notes = db.relationship(
        "ContestRuleNote",
        back_populates="contest",
        cascade="all, delete-orphan",
        order_by="ContestRuleNote.created_at.desc()",
    )


class ContestEdition(db.Model):
    """Single yearly contest edition with its own rules and logs."""

    id = db.Column(db.Integer, primary_key=True)
    contest_id = db.Column(db.Integer, db.ForeignKey("contest.id"), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(120), nullable=True)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(32), default="draft", nullable=False)
    submission_open_at = db.Column(db.DateTime, nullable=True)
    submission_deadline = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    contest = db.relationship("Contest", back_populates="editions")
    rules = db.relationship(
        "ContestRule",
        back_populates="edition",
        cascade="all, delete-orphan",
        order_by="ContestRule.key",
    )
    logs = db.relationship(
        "ContestLog",
        back_populates="edition",
        cascade="all, delete-orphan",
        order_by="ContestLog.uploaded_at.desc()",
    )
    categories = db.relationship(
        "ContestCategory",
        back_populates="edition",
        cascade="all, delete-orphan",
        order_by="ContestCategory.name",
    )
    certificate_template = db.relationship(
        "CertificateTemplate",
        back_populates="edition",
        uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (db.UniqueConstraint("contest_id", "year", name="uq_contest_edition_year"),)

    @property
    def display_name(self) -> str:
        """Return the human-friendly edition label shown in the UI."""
        if self.name:
            return f"{self.name} ({self.year})"
        return f"{self.contest.name} {self.year}"

    @property
    def status_label(self) -> str:
        """Return a presentable label for the stored lifecycle status."""
        labels = {
            "draft": "Szkic",
            "accepting_logs": "Przyjmowanie logów",
            "submission_closed": "Nabór zamknięty",
        }
        return labels.get(self.status, self.status.replace("_", " ").title())

    @property
    def can_accept_logs(self) -> bool:
        """Return whether uploads are allowed at the current time."""
        now = datetime.utcnow()
        if self.status != "accepting_logs":
            return False
        if self.submission_open_at and now < self.submission_open_at:
            return False
        if self.submission_deadline and now > self.submission_deadline:
            return False
        return True


class ContestRule(db.Model):
    """Key-value scoring rule attached to a specific contest edition."""

    id = db.Column(db.Integer, primary_key=True)
    edition_id = db.Column(db.Integer, db.ForeignKey("contest_edition.id"), nullable=False)
    key = db.Column(db.String(80), nullable=False)
    value = db.Column(db.String(255), nullable=False)
    description = db.Column(db.String(255), nullable=True)

    edition = db.relationship("ContestEdition", back_populates="rules")

    __table_args__ = (db.UniqueConstraint("edition_id", "key", name="uq_edition_rule_key"),)


class ContestRuleNote(db.Model):
    """Free-form official rule note stored alongside a contest."""

    id = db.Column(db.Integer, primary_key=True)
    contest_id = db.Column(db.Integer, db.ForeignKey("contest.id"), nullable=False)
    title = db.Column(db.String(160), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    contest = db.relationship("Contest", back_populates="rule_notes")


class ContestCategory(db.Model):
    """Participant category available for one contest edition."""

    id = db.Column(db.Integer, primary_key=True)
    edition_id = db.Column(db.Integer, db.ForeignKey("contest_edition.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    edition = db.relationship("ContestEdition", back_populates="categories")
    logs = db.relationship("ContestLog", back_populates="category")

    __table_args__ = (db.UniqueConstraint("edition_id", "name", name="uq_edition_category_name"),)


class ContestLog(db.Model):
    """Uploaded Cabrillo submission and its computed scoring metadata."""

    id = db.Column(db.Integer, primary_key=True)
    edition_id = db.Column(db.Integer, db.ForeignKey("contest_edition.id"), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("contest_category.id"), nullable=True)
    club_id = db.Column(db.Integer, db.ForeignKey("contest_club.id"), nullable=True)

    submitter_email = db.Column(db.String(255), nullable=True)
    location = db.Column(db.String(120), nullable=True)
    grid_locator = db.Column(db.String(16), nullable=True)
    power_category = db.Column(db.String(32), nullable=True)
    operators_count = db.Column(db.Integer, nullable=True)
    declaration_accepted = db.Column(db.Boolean, default=False, nullable=False)
    is_checklog = db.Column(db.Boolean, default=False, nullable=False)
    submission_status = db.Column(db.String(32), default="accepted", nullable=False)
    submission_notes = db.Column(db.String(255), nullable=True)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    station_call = db.Column(db.String(32), nullable=True)
    operator = db.Column(db.String(120), nullable=True)
    claimed_score = db.Column(db.Integer, nullable=True)
    raw_text = db.Column(db.Text, nullable=False)

    valid_qso_count = db.Column(db.Integer, default=0, nullable=False)
    invalid_qso_count = db.Column(db.Integer, default=0, nullable=False)
    computed_score = db.Column(db.Integer, default=0, nullable=False)

    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    edition = db.relationship("ContestEdition", back_populates="logs")
    category = db.relationship("ContestCategory", back_populates="logs")
    club = db.relationship("ContestClub", back_populates="logs")
    qsos = db.relationship(
        "QSOEntry",
        back_populates="log",
        cascade="all, delete-orphan",
        order_by="QSOEntry.line_no",
    )

    @property
    def contest(self):
        """Shortcut to the contest associated with this log's edition."""
        return self.edition.contest


class QSOEntry(db.Model):
    """Single parsed QSO row extracted from an uploaded Cabrillo log."""

    id = db.Column(db.Integer, primary_key=True)
    log_id = db.Column(db.Integer, db.ForeignKey("contest_log.id"), nullable=False)

    line_no = db.Column(db.Integer, nullable=False)
    raw_line = db.Column(db.Text, nullable=False)

    qso_date = db.Column(db.String(10), nullable=True)
    qso_time = db.Column(db.String(4), nullable=True)
    frequency = db.Column(db.String(16), nullable=True)
    band = db.Column(db.String(16), nullable=True)
    mode = db.Column(db.String(16), nullable=True)

    worked_call = db.Column(db.String(32), nullable=True)
    sent_rst = db.Column(db.String(8), nullable=True)
    sent_exchange = db.Column(db.String(64), nullable=True)
    recv_rst = db.Column(db.String(8), nullable=True)
    recv_exchange = db.Column(db.String(64), nullable=True)

    is_valid = db.Column(db.Boolean, default=True, nullable=False)
    invalid_reason = db.Column(db.String(255), nullable=True)
    points_awarded = db.Column(db.Integer, default=0, nullable=False)

    log = db.relationship("ContestLog", back_populates="qsos")


class ContestClub(db.Model):
    """Contest club used to group member callsigns and aggregate scores."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False, unique=True)
    description = db.Column(db.String(255), nullable=True)
    is_approved = db.Column(db.Boolean, default=False, nullable=False)
    approved_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    members = db.relationship(
        "ContestClubMember",
        back_populates="club",
        cascade="all, delete-orphan",
        order_by="ContestClubMember.callsign",
    )
    logs = db.relationship("ContestLog", back_populates="club")


class ContestClubMember(db.Model):
    """Eligible callsign entry belonging to a contest club."""

    id = db.Column(db.Integer, primary_key=True)
    club_id = db.Column(db.Integer, db.ForeignKey("contest_club.id"), nullable=False)
    callsign = db.Column(db.String(32), nullable=False)
    operator_name = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    club = db.relationship("ContestClub", back_populates="members")

    __table_args__ = (db.UniqueConstraint("club_id", "callsign", name="uq_club_member_callsign"),)


class CertificateTemplate(db.Model):
    """SVG template file uploaded by an admin for a specific edition."""

    id = db.Column(db.Integer, primary_key=True)
    edition_id = db.Column(
        db.Integer, db.ForeignKey("contest_edition.id"), nullable=False, unique=True
    )
    original_filename = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    edition = db.relationship("ContestEdition", back_populates="certificate_template")
