"""Email a single summary report for incidents already saved in SQLite."""

import argparse
from email.message import EmailMessage
import os
from pathlib import Path
import smtplib
import sqlite3
import ssl
import sys


ALERT_SENDER = "greenideatechlimited@gmail.com"
ALERT_RECIPIENT = "smmethun2006@gmail.com"


def load_password():
    """Read GMAIL_APP_PASSWORD from process environment or the private project .env."""
    if os.environ.get("GMAIL_APP_PASSWORD"):
        return os.environ["GMAIL_APP_PASSWORD"].replace(" ", "").strip()
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return ""
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            key, separator, value = line.partition("=")
            if not separator or key.strip() != "GMAIL_APP_PASSWORD":
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            return value.replace(" ", "").strip()
    except OSError:
        return ""
    return ""


def safe_image_path(media_dir, filename):
    if not filename or Path(filename).name != filename or "/" in filename or "\\" in filename:
        return None
    root = Path(media_dir).resolve()
    path = (root / filename).resolve()
    if path.parent != root or not path.is_file():
        return None
    return path


def send_smtp(message, password):
    """Use Gmail SSL, then STARTTLS if the first connection drops before submission."""
    context = ssl.create_default_context()
    for port in (465, 587):
        stage = "connect"
        try:
            if port == 465:
                with smtplib.SMTP_SSL("smtp.gmail.com", port, context=context, timeout=25) as server:
                    stage = "authenticate"
                    server.login(ALERT_SENDER, password)
                    stage = "message submission"
                    server.send_message(message)
            else:
                with smtplib.SMTP("smtp.gmail.com", port, timeout=25) as server:
                    server.ehlo()
                    stage = "STARTTLS handshake"
                    server.starttls(context=context)
                    server.ehlo()
                    stage = "authenticate"
                    server.login(ALERT_SENDER, password)
                    stage = "message submission"
                    server.send_message(message)
            if port == 587:
                print("Gmail SMTP used STARTTLS on port 587.")
            return
        except smtplib.SMTPAuthenticationError:
            raise RuntimeError(
                "Gmail rejected the App Password; check .env and use a newly generated password"
            ) from None
        except (OSError, ssl.SSLError, smtplib.SMTPServerDisconnected,
                smtplib.SMTPConnectError) as exc:
            if port == 465 and stage != "message submission":
                print("Gmail SMTP port 465 failed before submission; trying STARTTLS on port 587.")
                continue
            if stage == "authenticate":
                raise RuntimeError(
                    "Gmail disconnected during SMTP authentication. Verify .env has a fresh App Password for "
                    "the sender account, 2-Step Verification is enabled, and the account allows App Passwords."
                ) from None
            raise RuntimeError(
                "SMTP port {} failed during {} ({})".format(port, stage, type(exc).__name__)
            ) from None
        except smtplib.SMTPException as exc:
            raise RuntimeError(
                "SMTP port {} failed during {} ({})".format(port, stage, type(exc).__name__)
            ) from None


def main():
    parser = argparse.ArgumentParser(
        description="Send one email containing the saved incident summary and evidence images"
    )
    parser.add_argument("--database", default="reports/garbage1_run.sqlite3")
    parser.add_argument("--media-dir", default="alerts/garbage1_run")
    parser.add_argument("--incident-id", type=int,
                        help="Send only this saved incident (use after checking it was not already delivered)")
    args = parser.parse_args()

    password = load_password()
    if not password:
        raise SystemExit("No GMAIL_APP_PASSWORD found. Add a newly generated app password to .env.")

    database_path = Path(args.database)
    if not database_path.is_file():
        raise SystemExit("Incident database not found: {}".format(database_path))
    try:
        with sqlite3.connect(database_path) as connection:
            connection.row_factory = sqlite3.Row
            if args.incident_id is None:
                incidents = connection.execute(
                    "SELECT * FROM incidents ORDER BY id"
                ).fetchall()
            else:
                incidents = connection.execute(
                    "SELECT * FROM incidents WHERE id = ?", (args.incident_id,)
                ).fetchall()
    except sqlite3.Error:
        raise SystemExit("Could not read the incident database.")
    if not incidents:
        raise SystemExit("The incident database contains no saved reports.")

    lines = [
        "Smart City Detector incident report",
        "",
        "This report contains {} detector alert(s). Please review the attached evidence; detections are not independently verified.".format(len(incidents)),
        "",
    ]
    message = EmailMessage()
    subject = (
        "Smart City Detector incident #{}".format(incidents[0]["id"])
        if args.incident_id is not None
        else "Smart City Detector report - {} alert(s)".format(len(incidents))
    )
    message["Subject"] = subject
    message["From"] = ALERT_SENDER
    message["To"] = ALERT_RECIPIENT
    attached = 0
    missing = 0
    media_dir = Path(args.media_dir)
    image_attachments = []

    for incident in incidents:
        lines.extend([
            "Incident #{}: {}".format(incident["id"], incident["issue_type"].replace("_", " ").title()),
            "Detected: {}".format(incident["detected_at"]),
            "Label: {}".format(incident["label"]),
            "Confidence: {:.0%}".format(incident["confidence"]),
            "Source: {}".format(incident["source"]),
            "Status: {}".format(incident["status"]),
        ])
        if incident["latitude"] is not None and incident["longitude"] is not None:
            latitude, longitude = incident["latitude"], incident["longitude"]
            lines.append("Location: {} ({:.6f}, {:.6f})".format(
                incident["location_source"], latitude, longitude
            ))
            lines.append("Map: https://maps.google.com/?q={:.6f},{:.6f}".format(latitude, longitude))
            if incident["accuracy_m"] is not None:
                lines.append("Reported location accuracy: ±{:.0f} m".format(incident["accuracy_m"]))
        else:
            lines.append("Location: unavailable for this run ({})".format(incident["location_source"]))
        lines.append("")

        for column in ("snapshot_path", "crop_path"):
            image_path = safe_image_path(media_dir, incident[column])
            if image_path is None:
                missing += 1
                continue
            image_bytes = image_path.read_bytes()
            image_attachments.append((incident["id"], image_path, image_bytes))
            attached += 1

    if attached == 0:
        raise SystemExit("No incident images were found; report email was not sent.")
    if missing:
        lines.extend(["Note: {} expected evidence image(s) were unavailable.".format(missing), ""])
    message.set_content("\n".join(lines))
    for incident_id, image_path, image_bytes in image_attachments:
        message.add_attachment(
            image_bytes,
            maintype="image",
            subtype="jpeg",
            filename="incident_{}_{}".format(incident_id, image_path.name),
        )

    try:
        send_smtp(message, password)
    except (OSError, RuntimeError, smtplib.SMTPException) as exc:
        print("Email sending failed: {}".format(exc), file=sys.stderr)
        raise SystemExit(1)
    finally:
        password = ""

    print("Incident report emailed to {} ({} incidents, {} images).".format(
        ALERT_RECIPIENT, len(incidents), attached
    ))


if __name__ == "__main__":
    main()
