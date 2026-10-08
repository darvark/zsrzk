import click
import os

from flask import Flask, g, request, session, url_for
from sqlalchemy import func, inspect, text

from .extensions import db
from .admin_auth import get_current_admin_user


UI_LANGUAGES = {"pl"}
UI_THEMES = {"light", "dark"}

EN_LABELS = {
    "nav_contests": "Contests",
    "nav_upload_log": "Upload Log",
    "nav_logs": "Logs",
    "nav_certificates": "Certificates",
    "nav_clubs": "Clubs",
    "nav_admin": "Admin",
    "nav_admin_login": "Admin Login",
    "theme": "Theme",
    "theme_light": "Light",
    "theme_dark": "Dark",
    "lang": "Language",
    "lang_en": "EN",
    "lang_pl": "PL",
    "site_footer": "Amateur Radio Contest Settlement System",
    "clubs_title": "Contest Clubs",
    "clubs_intro": "New clubs can be submitted by users and become active after admin approval.",
    "clubs_create": "Create Club",
    "club_name": "Club Name",
    "description": "Description",
    "optional": "Optional",
    "status": "Status",
    "members": "Members",
    "logs": "Logs",
    "details": "Details",
    "pending_approval": "Pending Approval",
    "approved": "Approved",
    "open": "Open",
    "no_clubs": "No clubs created yet.",
    "upload_title": "Upload Cabrillo Log",
    "contest_edition": "Contest Edition",
    "participant_category": "Participant Category",
    "club": "Club",
    "club_approved_only": "Only admin-approved clubs are available.",
    "no_club": "No club",
    "submitter_email": "Submitter E-mail",
    "location": "Location During Contest",
    "grid_locator": "Station Grid Locator",
    "max_power": "Maximum Power",
    "operators_count": "Number of Operators",
    "checklog_label": "Submit as checklog (non-competitive)",
    "cabrillo_file": "Cabrillo File",
    "or_paste_cabrillo": "Or paste Cabrillo text directly",
    "declaration": "I certify this submission is accurate and complies with contest and licensing rules.",
    "upload_score": "Upload & Score Log",
}

TRANSLATIONS = {
    "pl": {
        "nav_contests": "Zawody",
        "nav_calendar": "Kalendarz",
        "nav_upload_log": "Wgraj log",
        "nav_logs": "Logi",
        "nav_certificates": "Dyplomy",
        "nav_clubs": "Kluby",
        "nav_admin": "Admin",
        "nav_admin_login": "Logowanie admin",
        "theme": "Motyw",
        "theme_light": "Jasny",
        "theme_dark": "Ciemny",
        "lang": "Język",
        "lang_en": "EN",
        "lang_pl": "PL",
        "site_footer": "System rozliczania zawodów krótkofalarskich",
        "clubs_title": "Kluby zawodów",
        "clubs_intro": "Nowe kluby mogą zgłaszać użytkownicy. Aktywne stają się po akceptacji administratora.",
        "clubs_create": "Utwórz klub",
        "club_name": "Nazwa klubu",
        "description": "Opis",
        "optional": "Opcjonalnie",
        "status": "Status",
        "members": "Członkowie",
        "logs": "Logi",
        "details": "Szczegóły",
        "pending_approval": "Oczekuje na akceptację",
        "approved": "Zaakceptowany",
        "open": "Otwórz",
        "no_clubs": "Brak klubów.",
        "upload_title": "Wgraj log Cabrillo",
        "contest_edition": "Edycja zawodów",
        "participant_category": "Kategoria uczestnika",
        "club": "Klub",
        "club_approved_only": "Dostępne są tylko kluby zaakceptowane przez administratora.",
        "no_club": "Bez klubu",
        "submitter_email": "E-mail zgłaszającego",
        "location": "Lokalizacja podczas zawodów",
        "grid_locator": "Lokator stacji",
        "max_power": "Maksymalna moc",
        "operators_count": "Liczba operatorów",
        "checklog_label": "Wyślij jako checklog (poza klasyfikacją)",
        "cabrillo_file": "Plik Cabrillo",
        "or_paste_cabrillo": "Lub wklej tekst Cabrillo",
        "declaration": "Oświadczam, że zgłoszenie jest poprawne i zgodne z regulaminem oraz przepisami.",
        "upload_score": "Wgraj i przelicz log",
    }
}


def _apply_schema_compatibility_upgrades(app: Flask) -> None:
    """Apply additive upgrades and safe cleanup for schema compatibility."""
    inspector = inspect(db.engine)

    with db.engine.begin() as conn:
        tables = set(inspector.get_table_names())

        if "contest" in tables:
            contest_columns = {col["name"] for col in inspector.get_columns("contest")}
            if "is_yearly_recurring" not in contest_columns:
                conn.execute(
                    text(
                        "ALTER TABLE contest "
                        "ADD COLUMN is_yearly_recurring BOOLEAN NOT NULL DEFAULT 1"
                    )
                )
                app.logger.info("Applied schema upgrade: contest.is_yearly_recurring")

        if "contest_club" in tables:
            club_columns = {col["name"] for col in inspector.get_columns("contest_club")}
            if "is_approved" not in club_columns:
                conn.execute(
                    text(
                        "ALTER TABLE contest_club "
                        "ADD COLUMN is_approved BOOLEAN NOT NULL DEFAULT 0"
                    )
                )
                app.logger.info("Applied schema upgrade: contest_club.is_approved")

            if "approved_at" not in club_columns:
                conn.execute(text("ALTER TABLE contest_club ADD COLUMN approved_at DATETIME"))
                app.logger.info("Applied schema upgrade: contest_club.approved_at")

        if "contest_edition" in tables:
            # Normalize old lifecycle states that depended on removed snapshot publication.
            conn.execute(
                text(
                    "UPDATE contest_edition "
                    "SET status = 'submission_closed' "
                    "WHERE status IN ('raw_scores_published', 'final_results_published')"
                )
            )

            edition_columns = {col["name"] for col in inspector.get_columns("contest_edition")}
            for stale_column in ("raw_scores_published_at", "final_results_published_at"):
                if stale_column in edition_columns:
                    try:
                        conn.execute(text(f"ALTER TABLE contest_edition DROP COLUMN {stale_column}"))
                        app.logger.info("Applied schema cleanup: contest_edition.%s", stale_column)
                    except Exception:
                        # Older SQLite versions do not support DROP COLUMN; keep legacy column if needed.
                        app.logger.warning(
                            "Skipped schema cleanup for contest_edition.%s (DROP COLUMN unsupported)",
                            stale_column,
                        )

        # Drop obsolete snapshot tables that are no longer mapped in ORM.
        if "edition_score_snapshot_entry" in tables:
            conn.execute(text("DROP TABLE edition_score_snapshot_entry"))
            app.logger.info("Applied schema cleanup: dropped edition_score_snapshot_entry")

        if "edition_score_snapshot" in tables:
            conn.execute(text("DROP TABLE edition_score_snapshot"))
            app.logger.info("Applied schema cleanup: dropped edition_score_snapshot")


def create_app(test_config: dict | None = None) -> Flask:
    """Create and configure the Flask application instance."""
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-change-me")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL", "sqlite:///contest_settlement.db"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = int(
        os.environ.get("MAX_CONTENT_LENGTH", str(2 * 1024 * 1024))
    )
    app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_DEFAULT_SENDER", "")
    app.config["MAIL_SUPPRESS_SEND"] = os.environ.get("MAIL_SUPPRESS_SEND", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    app.config["SMTP_HOST"] = os.environ.get("SMTP_HOST", "")
    app.config["SMTP_PORT"] = int(os.environ.get("SMTP_PORT", "587"))
    app.config["SMTP_TIMEOUT"] = int(os.environ.get("SMTP_TIMEOUT", "10"))
    app.config["SMTP_USERNAME"] = os.environ.get("SMTP_USERNAME", "")
    app.config["SMTP_PASSWORD"] = os.environ.get("SMTP_PASSWORD", "")
    app.config["SMTP_USE_TLS"] = os.environ.get("SMTP_USE_TLS", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    app.config["SMTP_USE_SSL"] = os.environ.get("SMTP_USE_SSL", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    if test_config:
        app.config.update(test_config)

    app.extensions.setdefault("mail_outbox", [])

    db.init_app(app)

    from .services.logsp_import import import_logsp_contest_definition

    from .routes import bp as main_bp
    from .admin import admin_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp)

    @app.before_request
    def apply_ui_preferences() -> None:
        """Persist UI language and theme preferences in session."""
        lang = (request.args.get("lang") or "").strip().lower()
        theme = (request.args.get("theme") or "").strip().lower()

        if lang in UI_LANGUAGES:
            session["ui_lang"] = lang
        if theme in UI_THEMES:
            session["ui_theme"] = theme

        g.ui_lang = session.get("ui_lang", "pl")
        if g.ui_lang not in UI_LANGUAGES:
            g.ui_lang = "pl"
            session["ui_lang"] = "pl"
        g.ui_theme = session.get("ui_theme", "light")

    def _t(key: str) -> str:
        """Translate a UI key according to the current language selection."""
        lang = getattr(g, "ui_lang", "pl")
        return TRANSLATIONS.get(lang, {}).get(key, key)

    def _status_pl(value: str | None) -> str:
        """Return a Polish label for known status tokens."""
        labels = {
            "accepted": "zaakceptowany",
            "rejected": "odrzucony",
            "pending": "oczekujący",
            "checklog": "checklog",
            "draft": "szkic",
            "accepting_logs": "przyjmowanie logów",
            "submission_closed": "nabór zamknięty",
        }
        token = (value or "").strip().lower()
        return labels.get(token, token.replace("_", " "))

    def _ui_url(**updates) -> str:
        """Build the current page URL with updated query parameters."""
        endpoint = request.endpoint or "main.index"
        view_args = dict(request.view_args or {})
        args = dict(request.args)

        for key, value in updates.items():
            if value is None:
                args.pop(key, None)
            else:
                args[key] = value

        for key in view_args:
            args.pop(key, None)

        try:
            return url_for(endpoint, **view_args, **args)
        except Exception:
            return url_for("main.index", **args)

    @app.context_processor
    def inject_admin_user():
        """Expose commonly used UI state and helpers to all templates."""
        return {
            "current_admin_user": get_current_admin_user(),
            "ui_lang": getattr(g, "ui_lang", "pl"),
            "ui_theme": getattr(g, "ui_theme", "light"),
            "t": _t,
            "ui_url": _ui_url,
            "status_pl": _status_pl,
        }

    @app.cli.command("import-logsp")
    @click.argument("source_url")
    @click.option("--commit/--dry-run", default=False, help="Persist imported contest data into the database.")
    def import_logsp_command(source_url: str, commit: bool) -> None:
        """Import contest metadata from a logSP contest page."""
        result = import_logsp_contest_definition(source_url, commit=commit)
        action = "Imported" if commit else "Parsed"
        print(f"{action} contest page: {result.contest_name} {result.year}")
        print(f"Organizer: {result.organizer or 'n/a'}")
        print(f"Categories: {', '.join(result.categories) if result.categories else 'n/a'}")
        print(f"Rules PDF: {result.rules_url or 'n/a'}")
        print(f"Source: {result.source_url}")

    @app.cli.command("create-admin")
    @click.argument("username")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def create_admin_command(username: str, password: str) -> None:
        """Create a local admin account for the web administration panel."""
        from .models import AdminUser

        normalized_username = username.strip().lower()
        if not normalized_username:
            raise click.ClickException("Username is required.")

        existing = AdminUser.query.filter(func.lower(AdminUser.username) == normalized_username).first()
        if existing:
            raise click.ClickException("An admin user with that username already exists.")

        user = AdminUser(username=normalized_username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print(f"Created admin user: {normalized_username}")

    @app.cli.command("init-db")
    def init_db_command() -> None:
        """Create all configured database tables."""
        db.create_all()
        print("Database initialized.")

    @app.cli.command("recreate-db")
    def recreate_db_command() -> None:
        """Drop and recreate all database tables."""
        db.drop_all()
        db.create_all()
        print("Database recreated.")

    with app.app_context():
        _apply_schema_compatibility_upgrades(app)
        db.create_all()

    return app
