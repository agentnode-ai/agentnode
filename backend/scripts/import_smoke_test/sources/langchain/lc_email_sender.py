"""
Email sender tool using smtplib.
Commonly seen in automation agent stacks — credentials often hardcoded or pulled from env.
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from langchain.tools import tool


SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")


@tool
def send_email(to: str, subject: str, body: str) -> dict:
    """
    Send an email via SMTP.

    Reads SMTP credentials from environment variables:
      SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS

    Args:
        to: Recipient email address
        subject: Email subject line
        body: Plain-text email body

    Returns:
        dict with success status and message
    """
    if not SMTP_USER or not SMTP_PASS:
        return {
            "success": False,
            "error": "SMTP credentials not configured",
            "to": to,
        }

    msg = MIMEMultipart()
    msg["From"] = SMTP_USER
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, to, msg.as_string())
        return {
            "success": True,
            "to": to,
            "subject": subject,
            "error": None,
        }
    except smtplib.SMTPAuthenticationError:
        return {"success": False, "error": "SMTP authentication failed", "to": to}
    except Exception as e:
        return {"success": False, "error": str(e), "to": to}
