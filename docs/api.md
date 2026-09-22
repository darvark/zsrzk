# HTTP Interface and API Usage

## Scope

This project does not expose a dedicated JSON REST API.

Its usable application interface consists of:

- public HTML routes,
- form POST endpoints,
- file download endpoints,
- authenticated admin routes,
- CLI commands.

This document describes that interface as the project's API.

## Base URL

Local development default:

```text
http://127.0.0.1:5000
```

## Response Types

The application returns one of the following:

- rendered HTML pages,
- redirects after form submissions,
- plain text responses,
- markdown text responses,
- SVG or TXT file downloads.

## Public Routes

### `GET /`

Home page with recent contests, recent logs, and recently published snapshots.

### `GET /contests`

List all contests.

### `POST /contests`

Create a new contest and its first annual edition.

Form fields:

- `name`: required string.
- `description`: optional string.
- `first_edition_year`: optional integer, defaults to the current year.

### `GET /contests/<contest_id>`

Show one contest and summary statistics for its editions.

### `POST /contests/<contest_id>/editions`

Create an additional edition for a contest.

Form fields:

- `year`: required integer.
- `name`: optional edition display name.
- `description`: optional description.
- `copy_rules_from`: optional edition id to clone rules from.

### `POST /contests/<contest_id>/rule-notes`

Create a stored rule note for a contest.

Form fields:

- `title`: required string.
- `content`: required text.

### `POST /contests/<contest_id>/rule-notes/<note_id>/delete`

Delete a contest rule note.

### `GET /editions/<edition_id>`

Show one edition with its rule table, categories, and management actions.

### `POST /editions/<edition_id>`

Add a new scoring rule to an edition.

Form fields:

- `key`: required string.
- `value`: required string.
- `description`: optional string.

### `POST /editions/<edition_id>/lifecycle`

Update edition lifecycle and submission dates.

Form fields:

- `status`: one of `draft`, `accepting_logs`, `submission_closed`, `raw_scores_published`, `final_results_published`.
- `submission_open_at`: optional datetime in `YYYY-MM-DDTHH:MM` or compatible format.
- `submission_deadline`: optional datetime in `YYYY-MM-DDTHH:MM` or compatible format.

### `POST /editions/<edition_id>/publish/raw`

Publish a raw score snapshot for the edition.

### `POST /editions/<edition_id>/publish/final`

Publish a final score snapshot for the edition.

Shared form fields:

- `note`: optional publication note.

### `POST /editions/<edition_id>/rules/<rule_id>`

Update an existing scoring rule.

Form fields:

- `key`: required string.
- `value`: required string.
- `description`: optional string.

### `POST /editions/<edition_id>/rules/<rule_id>/delete`

Delete one scoring rule.

### `POST /editions/<edition_id>/categories`

Create a participant category.

Form fields:

- `name`: required string.
- `description`: optional string.

### `POST /editions/<edition_id>/categories/<category_id>/delete`

Delete a participant category if it is not already used by logs.

### `GET /contests/<contest_id>/summary`

Show aggregated results across editions.

Query parameters:

- `category`: optional category filter. Use `all` or `__uncategorized__` for special cases.

### `GET /logs/received`

Public log listing with filters.

Query parameters:

- `contest_id`: optional integer.
- `edition_id`: optional integer.
- `status`: optional status filter, defaults to `all`.
- `callsign`: optional partial callsign match.

### `GET /logs/upload`

Render the public upload form.

Query parameters:

- `edition_id`: optional preselected edition id.
- `category_id`: optional preselected category id.

### `POST /logs/upload`

Upload, parse, validate, and score a Cabrillo log.

Accepted content styles:

- multipart file upload via `cabrillo_file`, or
- pasted text via `cabrillo_text`.

Required form fields:

- `edition_id`
- `submitter_email`
- `location`
- `grid_locator`
- `power_category`
- `operators_count`
- `declaration_accepted=on`

Optional form fields:

- `category_id`
- `club_id`
- `is_checklog=on`
- `cabrillo_file`
- `cabrillo_text`

Behavior:

- the edition must be in an upload-accepting state,
- either a file or pasted text must be supplied,
- Cabrillo header fields such as `CALLSIGN`, `OPERATORS`, and `CLAIMED-SCORE` are read when present,
- each `QSO:` row is stored,
- scoring rules are applied immediately,
- the request redirects to the created log detail page on success.

Example multipart upload with `curl`:

```bash
curl -X POST http://127.0.0.1:5000/logs/upload \
  -F "edition_id=1" \
  -F "submitter_email=operator@example.com" \
  -F "location=Krakow" \
  -F "grid_locator=JO90XA" \
  -F "power_category=LOW" \
  -F "operators_count=1" \
  -F "declaration_accepted=on" \
  -F "cabrillo_file=@sample.log"
```

### `GET /logs/<log_id>`

Show the stored log, QSO details, and scoring breakdown.

### `GET /snapshots/<snapshot_id>`

Show the ranking rows inside a published snapshot.

### `GET /scores`

Lookup published scores and ranked entries.

Query parameters:

- `edition_id`: optional edition filter.
- `callsign`: optional exact callsign filter.

### `GET /help/contest-rule-fields`

Serve the rule-field markdown help document as `text/markdown`.

### `GET /certificates`

Lookup certificate-eligible logs for a callsign.

Query parameters:

- `callsign`: optional exact callsign.

### `GET /certificates/<log_id>/txt`

Download a plain-text participation certificate.

### `GET /certificates/<log_id>/svg`

Download an SVG certificate using either:

- a custom admin-uploaded template, or
- the built-in fallback template.

Eligibility requirements:

- log status must be `accepted`,
- the log must not be marked as checklog,
- final results must already be published for the edition.

### `GET /clubs`

List contest clubs.

### `POST /clubs`

Create a club.

Form fields:

- `name`: required string.
- `description`: optional string.

### `GET /clubs/<club_id>`

Show a club, its members, and its aggregated score rows.

### `POST /clubs/<club_id>/members/import`

Bulk import club members from a text area.

Form field:

- `member_list`: newline-separated entries. Each line uses `CALLSIGN` or `CALLSIGN,Operator Name`.

### `POST /clubs/<club_id>/members/<member_id>/delete`

Remove a club member.

## Admin Routes

All `/admin/*` routes require an authenticated admin session except the login page itself.

### `GET /admin/login`

Render the login form.

### `POST /admin/login`

Authenticate an admin user.

Form fields:

- `username`
- `password`

### `POST /admin/logout`

Sign out the current admin session.

### `GET /admin/`

Admin dashboard with aggregate counts and recent activity.

### `GET|POST /admin/users`

List admin users and create new admin accounts.

Creation form fields:

- `username`
- `password`
- `confirm_password`

### `GET /admin/contests`

List contests for administration.

### `GET|POST /admin/contests/<contest_id>/edit`

Edit contest metadata.

### `POST /admin/contests/<contest_id>/delete`

Delete a contest and all of its child data.

### `GET /admin/editions`

List editions with optional `contest_id` filter.

### `GET|POST /admin/editions/<edition_id>/edit`

Edit edition metadata and dates.

### `POST /admin/editions/<edition_id>/delete`

Delete an edition and all dependent data.

### `GET /admin/logs`

List logs with optional filters:

- `edition_id`
- `status`
- `callsign`

### `GET|POST /admin/logs/<log_id>/edit`

Edit log metadata or rescore a log.

Update form fields:

- `submission_status`
- `submission_notes`
- `is_checklog`
- `category_id`

Rescore action:

- `action=rescore`

### `POST /admin/logs/<log_id>/delete`

Delete a log.

### `GET /admin/logs/<log_id>/check-summary`

Render a text check summary and `mailto:` helper.

### `GET /admin/logs/<log_id>/check-summary/download`

Download the check summary as a text file.

### `GET /admin/editions/<edition_id>/rules`

Open the focused admin rule editor.

### `POST /admin/editions/<edition_id>/rules/<rule_id>/update`

Update only the `value` of an existing rule.

### `POST /admin/editions/<edition_id>/cert-template/upload`

Upload or replace an edition-specific SVG certificate template.

Form field:

- `template_file`: required `.svg` file.

### `POST /admin/editions/<edition_id>/cert-template/delete`

Delete the custom certificate template for an edition.

## CLI Commands

These commands are registered through the Flask application factory.

### `flask --app run.py init-db`

Create database tables.

### `flask --app run.py recreate-db`

Drop and recreate all tables.

### `flask --app run.py create-admin <username>`

Create an admin user and prompt for password input.

### `flask --app run.py import-logsp <source_url> [--commit|--dry-run]`

Parse a logSP contest definition page and optionally persist it.

## Error Handling Notes

Most mutating endpoints follow the same pattern:

- invalid input triggers a flash message,
- the request redirects back to a form page or re-renders the form,
- successful writes commit to SQLite and redirect to the relevant detail page.

This means browser automation or external integration should expect redirects rather than JSON error payloads.