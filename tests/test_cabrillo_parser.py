from __future__ import annotations

from pathlib import Path

from app.services.cabrillo import parse_cabrillo, validate_cabrillo_payload


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "cabrillo"


def _load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def test_parse_valid_log_extracts_headers_and_qsos():
    header, qsos = parse_cabrillo(_load_fixture("valid_simple.log"))

    assert header["CALLSIGN"] == "SP1AAA"
    assert header["CLAIMED-SCORE"] == "999"
    assert len(qsos) == 2
    assert qsos[0].parse_error is None
    assert qsos[0].band == "20M"
    assert qsos[0].worked_call == "K1ABC"


def test_parse_malformed_qso_sets_error():
    _, qsos = parse_cabrillo(_load_fixture("malformed_qso.log"))

    assert len(qsos) == 2
    assert qsos[0].parse_error == "QSO line has fewer columns than expected."
    assert qsos[1].parse_error is None


def test_validate_cabrillo_payload_reports_malformed_qso():
    header, qsos = parse_cabrillo(_load_fixture("malformed_qso.log"))

    errors = validate_cabrillo_payload(header, qsos)

    assert any("malformed QSO" in error for error in errors)


def test_validate_cabrillo_payload_reports_missing_structure():
    header, qsos = parse_cabrillo("CALLSIGN: SP1AAA\n")

    errors = validate_cabrillo_payload(header, qsos)

    assert "Invalid Cabrillo format: missing START-OF-LOG header." in errors
    assert "Invalid Cabrillo format: missing END-OF-LOG footer." in errors
    assert "Invalid Cabrillo format: at least one line starting with QSO: is required." in errors
