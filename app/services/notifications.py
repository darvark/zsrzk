from __future__ import annotations

import smtplib
from email.message import EmailMessage

from flask import current_app


def send_log_upload_confirmation(log, detail_url: str | None = None) -> bool:
    """Send a confirmation email after a log upload succeeds.

    Returns True when a message was sent or captured in the in-memory outbox.
    Returns False when email sending is not configured.
    """
    recipient = (log.submitter_email or "").strip()
    sender = (current_app.config.get("MAIL_DEFAULT_SENDER") or "").strip()

    if not recipient or not sender:
        return False

    subject = f"Log received - {log.contest.name} {log.edition.year}"
    lines = [
        "Your log has been received successfully.",
        "",
        f"Contest: {log.contest.name}",
        f"Edition: {log.edition.display_name}",
        f"Callsign: {log.station_call or 'N/A'}",
        f"Valid QSOs: {log.valid_qso_count}",
        f"Invalid QSOs: {log.invalid_qso_count}",
        f"Computed score: {log.computed_score}",
    ]
    if detail_url:
        lines += ["", f"Log details: {detail_url}"]

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content("\n".join(lines))

    if current_app.config.get("MAIL_SUPPRESS_SEND", False):
        current_app.extensions.setdefault("mail_outbox", []).append(
            {
                "to": recipient,
                "from": sender,
                "subject": subject,
                "body": message.get_content(),
            }
        )
        return True

    smtp_host = (current_app.config.get("SMTP_HOST") or "").strip()
    if not smtp_host:
        return False

    smtp_port = int(current_app.config.get("SMTP_PORT", 587))
    smtp_timeout = int(current_app.config.get("SMTP_TIMEOUT", 10))
    smtp_username = current_app.config.get("SMTP_USERNAME") or None
    smtp_password = current_app.config.get("SMTP_PASSWORD") or None
    smtp_use_tls = bool(current_app.config.get("SMTP_USE_TLS", True))
    smtp_use_ssl = bool(current_app.config.get("SMTP_USE_SSL", False))

    smtp_class = smtplib.SMTP_SSL if smtp_use_ssl else smtplib.SMTP
    with smtp_class(smtp_host, smtp_port, timeout=smtp_timeout) as server:
        if smtp_use_tls and not smtp_use_ssl:
            server.starttls()
        if smtp_username:
            server.login(smtp_username, smtp_password or "")
        server.send_message(message)

    return True