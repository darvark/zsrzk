# Project Overview

## Purpose

This project is a Flask application for collecting Cabrillo contest logs, applying configurable scoring rules, publishing ranking snapshots, and generating participation certificates.

The application is aimed at contest organizers who need a lightweight workflow for:

- defining contests and yearly editions,
- collecting participant submissions,
- validating and scoring uploaded logs,
- reviewing logs in an admin panel,
- publishing raw and final score tables,
- managing club membership rollups,
- generating TXT or SVG certificates.

## High-Level Architecture

The codebase is organized into four main layers:

- `app/__init__.py`: Flask app factory, blueprint registration, and CLI commands.
- `app/routes.py`: public-facing routes for contests, uploads, scores, certificates, and clubs.
- `app/admin.py`: authenticated administration routes for contests, editions, logs, rules, and certificate templates.
- `app/services/`: parsing and scoring services.

Supporting modules:

- `app/models.py`: SQLAlchemy models for contests, editions, logs, QSOs, clubs, score snapshots, and admin users.
- `app/admin_auth.py`: session-based admin authentication helpers.
- `app/extensions.py`: shared SQLAlchemy extension setup.
- `app/templates/`: Jinja templates for public and admin pages.
- `app/static/`: static frontend assets.

## Core Workflow

### 1. Contest setup

An organizer creates a contest and at least one annual edition.

Each edition stores:

- lifecycle status,
- submission window,
- scoring rules,
- participant categories,
- published raw/final score snapshots,
- optional custom SVG certificate template.

### 2. Log submission

Participants submit a Cabrillo log from the public upload page.

The submission captures both Cabrillo data and metadata entered in the form:

- submitter email,
- location,
- grid locator,
- power category,
- operator count,
- declaration acceptance,
- optional club assignment,
- optional participant category,
- checklog flag.

The upload can arrive as either:

- a file upload, or
- pasted Cabrillo text.

### 3. Parsing and scoring

The application parses all `QSO:` lines from the Cabrillo payload and stores them as `QSOEntry` rows.

Scoring is then applied using compiled rule values. The current rule engine supports:

- base QSO points,
- mode-specific point overrides,
- allowed band and mode filters,
- duplicate handling,
- prefix bonuses and multipliers,
- premium station bonus points,
- optional cross-log reciprocal matching.

Invalid QSOs are retained with a reason instead of being discarded, which keeps the audit trail visible in the UI.

### 4. Review and publication

Admins can:

- review uploaded logs,
- rescore a log after rule changes,
- mark logs as accepted, rejected, pending, or checklog,
- generate check summaries,
- publish raw or final ranking snapshots.

Snapshots materialize the ranking table at publication time, which makes public results stable even if later edits occur.

### 5. Certificate lookup

Once final results exist for an edition, participants can search by callsign and download certificates as:

- `.txt`, or
- `.svg`.

## Data Model Summary

Main entities:

- `Contest`: top-level contest definition.
- `ContestEdition`: yearly edition of a contest.
- `ContestRule`: scoring rule key/value for an edition.
- `ContestRuleNote`: organizer-maintained rule or reference note.
- `ContestCategory`: optional participant category for an edition.
- `ContestLog`: uploaded submission with scoring result and metadata.
- `QSOEntry`: parsed QSO line from a submitted log.
- `EditionScoreSnapshot`: published raw/final result set.
- `EditionScoreSnapshotEntry`: ranked row in a published snapshot.
- `ContestClub`: club definition used for grouping participants.
- `ContestClubMember`: club member eligibility entry.
- `CertificateTemplate`: optional custom SVG template.
- `AdminUser`: local admin account.

## Authentication Model

Only the admin panel is authenticated.

- Authentication is session-based.
- Admin users are stored in the local database.
- There is no public user registration flow.
- Public contest browsing, uploads, scores, and certificate lookup do not require login.

## Running the Project

Typical local run flow:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

Database helpers:

```powershell
flask --app run.py init-db
flask --app run.py recreate-db
flask --app run.py create-admin <username>
```

## Testing

Run the automated test suite with:

```powershell
pytest
```

## Related Documents

- [api.md](api.md)
- [contest-rule-fields.md](contest-rule-fields.md)