# Amateur Radio Contest Settlement (Flask)

Flask application for settling amateur radio contests with Cabrillo logs.

## Features

- Multiple contest definitions
- Multiple annual editions per contest
- Per-edition scoring rules
- Stored contest rule notes (official text/links kept in the app)
- Participant categories per edition (informational, no scoring impact)
- Edition lifecycle workflow: draft, accepting logs, closed, raw published, final published
- Submission window enforcement (open time + deadline)
- Cabrillo log upload from file or pasted text
- Submission metadata capture: email, location, grid, power class, operator count, declaration
- Optional upload confirmation email to the submitter
- Club assignment per log and checklog support
- Automatic points calculation
- QSO validity report (valid and invalid with reason)
- Logs Received page with filters and status counts
- Raw/final score snapshot publishing and lookup
- Certificate lookup by callsign with downloadable TXT/SVG artifacts
- Contest clubs with eligibility list import and club score rollups
- Score summary per contest and per edition
- SQLite database backend

## Quick Start (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

Open the app at `http://127.0.0.1:5000`.

## Quick Start With Docker

Build and start the application with Docker Compose:

```powershell
docker compose up --build -d
```

Then open `http://127.0.0.1:5000`.

Notes:

- The SQLite database and uploaded certificate templates are persisted in the mounted `./instance` directory.
- Override `SECRET_KEY`, `DATABASE_URL`, `MAX_CONTENT_LENGTH`, or SMTP mail settings through environment variables if needed.
- Create the first admin user inside the running container with `docker compose exec web flask --app run.py create-admin <username>`.

Mail settings for upload confirmations:

- `MAIL_DEFAULT_SENDER`: From address used for confirmation emails.
- `SMTP_HOST`: SMTP server hostname. If unset, confirmation emails are skipped.
- `SMTP_PORT`: SMTP server port, default `587`.
- `SMTP_USERNAME`: Optional SMTP login username.
- `SMTP_PASSWORD`: Optional SMTP login password.
- `SMTP_USE_TLS`: `true/false`, default `true`.
- `SMTP_USE_SSL`: `true/false`, default `false`.
- `SMTP_TIMEOUT`: SMTP timeout in seconds, default `10`.
- `MAIL_SUPPRESS_SEND`: `true/false`; disables SMTP delivery and stores messages in the in-memory test outbox.

## Documentation

- [docs/project-overview.md](docs/project-overview.md) for architecture, workflow, and data model overview.
- [docs/api.md](docs/api.md) for the HTTP interface, form contracts, admin routes, and CLI commands.
- [docs/contest-rule-fields.md](docs/contest-rule-fields.md) for the rule key reference.

## Run Tests

```powershell
pip install -r requirements.txt
pytest
```

To stop the containerized deployment:

```powershell
docker compose down
```

If your database was created with an older schema, recreate it (required after this update):

```powershell
flask --app run.py recreate-db
```

Then open `Contests`, create or edit an edition, set lifecycle to `Accepting Logs`, and define submission window dates.

## Built-In Rule Keys

- `base_qso_points`: integer points per valid QSO
- `qso_points_cw`: integer mode-specific points for CW (0 means disabled)
- `qso_points_ssb`: integer mode-specific points for SSB/PHONE (0 means disabled)
- `qso_points_digi`: integer mode-specific points for digital modes (0 means disabled)
- `digi_modes`: comma-separated mode list treated as digital (e.g. `DIGI,RTTY,FT8`)
- `allowed_bands`: comma-separated list like `20M,40M`
- `allowed_modes`: comma-separated list like `CW,SSB`
- `dupe_scope`: `band_mode`, `band`, or `global`
- `bonus_per_unique_prefix`: integer bonus for each unique callsign prefix
- `multiplier_per_unique_band`: integer multiplier increment for each unique worked band
- `multiplier_per_unique_mode`: integer multiplier increment for each unique worked mode
- `multiplier_per_unique_prefix`: integer multiplier increment for each unique callsign prefix
- `premium_station_points`: comma-separated `CALL:POINTS` pairs, e.g. `SP9ABC:5,K1TTT:10`
- `require_cross_log_match`: `true/false`; require reciprocal QSO in another station log
- `cross_log_time_tolerance_min`: integer minute tolerance for reciprocal time matching
- `cross_log_check_exchange`: `true/false`; require reciprocal exchange match (sent <-> received)

See [docs/contest-rule-fields.md](docs/contest-rule-fields.md) for the full field reference and validation notes.

## Admin Login

Admin access is managed through local application accounts only.

- Create the first admin account with `flask --app run.py create-admin <username>`.
- Sign in at `/admin/login`.
- Additional admin accounts can be created from the Admin `Users` page.
- Admin accounts are stored locally with hashed passwords; there is no public user registration flow.

## HTTP Interface Notes

- The application does not currently expose a versioned JSON REST API.
- Its callable interface is the Flask route surface documented in [docs/api.md](docs/api.md): HTML pages, form POST endpoints, file downloads, and CLI commands.
- The main integration point is Cabrillo submission via `/logs/upload`, which accepts multipart form uploads or pasted Cabrillo text.

## Validation Flow

- The upload form validates submission metadata first: email, location, grid, power class, operator count, declaration, and edition availability.
- Cabrillo parsing then records each QSO line and marks parse errors immediately.
- Contest rules are applied during scoring through the shared rule compiler and scorer.
- That means the submission is accepted first, and rule-based invalid QSOs are reported in the scoring result rather than blocking the upload itself.

## Notes

- Parser expects Cabrillo `QSO:` lines with standard token order.
- Invalid QSOs are kept and displayed with reason for auditability.
- UI is intentionally simple and easy to extend.
- Cross-log matching is performed within the same annual edition.
