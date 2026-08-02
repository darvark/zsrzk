from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
import re
import ssl
from urllib.error import URLError
from urllib.request import Request, urlopen

from ..extensions import db
from ..models import Contest, ContestCategory, ContestEdition, ContestRuleNote


@dataclass(slots=True)
class LogSPContestImport:
    """Structured metadata extracted from a logSP contest page."""

    contest_name: str
    year: int
    organizer: str | None
    start_at: datetime | None
    end_at: datetime | None
    submission_deadline: datetime | None
    email: str | None
    bands: str | None
    emissions: str | None
    categories: list[str]
    tag: str | None
    rules_url: str | None
    source_url: str


class _VisibleTextParser(HTMLParser):
    """HTML parser that collects only visible text blocks from a page."""

    _block_tags = {
        "article",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "p",
        "section",
        "table",
        "td",
        "th",
        "tr",
    }

    def __init__(self) -> None:
        super().__init__()
        self.fragments: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        """Track blocks and ignore hidden content sections."""
        if tag in {"script", "style", "noscript"}:
            self.skip_depth += 1
            return
        if tag in self._block_tags:
            self.fragments.append("\n")

    def handle_endtag(self, tag: str) -> None:
        """Close ignored sections and preserve visible block boundaries."""
        if tag in {"script", "style", "noscript"}:
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if tag in self._block_tags:
            self.fragments.append("\n")

    def handle_data(self, data: str) -> None:
        """Collect visible text content from the HTML stream."""
        if self.skip_depth:
            return
        self.fragments.append(data)


def _fetch_html(url: str) -> str:
    """Download a contest page, retrying with a relaxed TLS context if needed."""
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=30, context=ssl.create_default_context()) as response:
            return response.read().decode("utf-8", errors="replace")
    except URLError:
        with urlopen(request, timeout=30, context=ssl._create_unverified_context()) as response:
            return response.read().decode("utf-8", errors="replace")


def _extract_visible_text(html_source: str) -> str:
    """Convert HTML into normalized visible text for regex-based parsing."""
    parser = _VisibleTextParser()
    parser.feed(unescape(html_source))
    parser.close()

    raw_text = "".join(parser.fragments).replace("\r", "\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in raw_text.splitlines()]
    return "\n".join(line for line in lines if line)


def _parse_datetime(token: str | None) -> datetime | None:
    """Parse one of the timestamp formats emitted by logSP pages."""
    value = (token or "").strip()
    if not value:
        return None
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            continue
    return None


def _clean_contest_name(raw_title: str, year: int) -> str:
    """Remove the trailing year suffix from an imported contest title."""
    title = raw_title.strip()
    year_suffix = f" {year}"
    if title.endswith(year_suffix):
        return title[: -len(year_suffix)].strip()
    return title


def parse_logsp_contest_definition(source_url: str) -> LogSPContestImport:
    """Parse a logSP contest page into local contest metadata."""
    html_source = _fetch_html(source_url)
    visible_text = _extract_visible_text(html_source)
    compact_text = re.sub(r"\s+", " ", visible_text).strip()

    title_match = re.search(r"(Zawody\s+.*?\d{4})\s*organizator:", compact_text, flags=re.IGNORECASE)
    if title_match:
        raw_title = title_match.group(1).strip()
    else:
        title_prefix = compact_text.split("organizator:", 1)[0].strip()
        raw_title = re.sub(r"\s+", " ", title_prefix)

    year_match = re.search(r"(19|20)\d{2}", raw_title) or re.search(r"(19|20)\d{2}", compact_text)
    if not year_match:
        raise ValueError(f"Could not determine contest year from {source_url}")
    year = int(year_match.group(0))

    organizer = re.search(r"organizator:\s*(.*?)\s+rozpoczęcie:", compact_text, flags=re.IGNORECASE)
    start_at = re.search(r"rozpoczęcie:\s*([0-9:-]{16,19})\s+pasma:", compact_text, flags=re.IGNORECASE)
    bands = re.search(r"pasma:\s*(.*?)\s+menedżer:", compact_text, flags=re.IGNORECASE)
    end_at = re.search(r"zakończenie:\s*([0-9:-]{16,19})\s+emisje:", compact_text, flags=re.IGNORECASE)
    emissions = re.search(r"emisje:\s*(.*?)\s+email:", compact_text, flags=re.IGNORECASE)
    email = re.search(r"email:\s*(.*?)\s+termin logów:", compact_text, flags=re.IGNORECASE)
    deadline = re.search(r"termin logów:\s*([0-9:-]{16,19})\s+kategorie:", compact_text, flags=re.IGNORECASE)
    categories = re.search(r"kategorie:\s*(.*?)\s+tag:", compact_text, flags=re.IGNORECASE)
    tag = re.search(r"tag:\s*(.*?)\s+regulamin", compact_text, flags=re.IGNORECASE)

    rule_links = re.findall(r"https?://[^\s\)\]]+?\.pdf", html_source, flags=re.IGNORECASE)
    rules_url = rule_links[0] if rule_links else None

    category_names = []
    if categories:
        category_names = [item.strip() for item in categories.group(1).split(",") if item.strip()]

    return LogSPContestImport(
        contest_name=_clean_contest_name(raw_title, year),
        year=year,
        organizer=organizer.group(1).strip() if organizer else None,
        start_at=_parse_datetime(start_at.group(1) if start_at else None),
        end_at=_parse_datetime(end_at.group(1) if end_at else None),
        submission_deadline=_parse_datetime(deadline.group(1) if deadline else None),
        email=email.group(1).strip() if email else None,
        bands=bands.group(1).strip() if bands else None,
        emissions=emissions.group(1).strip() if emissions else None,
        categories=category_names,
        tag=tag.group(1).strip() if tag else None,
        rules_url=rules_url,
        source_url=source_url,
    )


def import_logsp_contest_definition(source_url: str, *, commit: bool = False) -> LogSPContestImport:
    """Parse and optionally persist a logSP contest definition into the database."""
    imported = parse_logsp_contest_definition(source_url)

    if not commit:
        return imported

    contest = Contest.query.filter_by(name=imported.contest_name).first()
    if contest is None:
        contest = Contest(name=imported.contest_name, description=None)
        db.session.add(contest)
        db.session.flush()

    edition = ContestEdition.query.filter_by(contest_id=contest.id, year=imported.year).first()
    if edition is None:
        edition = ContestEdition(contest_id=contest.id, year=imported.year, status="draft")
        db.session.add(edition)
        db.session.flush()

    description_lines = [
        f"Imported from {source_url}",
        f"Organizer: {imported.organizer or 'n/a'}",
        f"Bands: {imported.bands or 'n/a'}",
        f"Emissions: {imported.emissions or 'n/a'}",
        f"Email: {imported.email or 'n/a'}",
        f"Tag: {imported.tag or 'n/a'}",
        f"Rules: {imported.rules_url or 'n/a'}",
        f"Start: {imported.start_at.strftime('%Y-%m-%d %H:%M') if imported.start_at else 'n/a'}",
        f"End: {imported.end_at.strftime('%Y-%m-%d %H:%M') if imported.end_at else 'n/a'}",
        f"Log deadline: {imported.submission_deadline.strftime('%Y-%m-%d %H:%M') if imported.submission_deadline else 'n/a'}",
    ]

    contest.description = "\n".join(description_lines)
    edition.description = "\n".join(description_lines)
    edition.submission_open_at = imported.start_at
    edition.submission_deadline = imported.submission_deadline

    existing_categories = {category.name for category in edition.categories}
    for category_name in imported.categories:
        if category_name not in existing_categories:
            db.session.add(ContestCategory(edition_id=edition.id, name=category_name))

    if not any(note.title == "Imported from logSP" for note in contest.rule_notes):
        db.session.add(ContestRuleNote(contest_id=contest.id, title="Imported from logSP", content="\n".join(description_lines)))

    db.session.commit()
    return imported
