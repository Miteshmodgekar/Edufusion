"""
Test suite for EduFusion Email & SMTP configuration and dispatch.
"""
import pytest
from flask import Flask
from config import Config, ProductionConfig, DevelopmentConfig
from extensions import db, mail, MAIL_AVAILABLE
from utils.notifications import send_email, _html_to_text, notify_low_attendance
from models.user import User
from types import SimpleNamespace


def test_config_mail_settings():
    """Verify that email config settings load correctly."""
    assert Config.MAIL_SERVER == "smtp.gmail.com"
    assert Config.MAIL_PORT in (587, 465)
    assert isinstance(Config.MAIL_USE_TLS, bool)
    assert isinstance(Config.MAIL_USE_SSL, bool)
    # When port is 587, TLS must be True and SSL must be False
    if Config.MAIL_PORT == 587:
        assert Config.MAIL_USE_TLS is True
        assert Config.MAIL_USE_SSL is False


def test_html_to_text_stripping():
    """Verify HTML tags are stripped properly for plain-text fallback."""
    html = "<h2>Hello World</h2><p>This is a <strong>test</strong>.</p>"
    plain = _html_to_text(html)
    assert "Hello World" in plain
    assert "This is a test" in plain
    assert "<p>" not in plain
    assert "<strong>" not in plain


def test_send_email_suppressed_when_no_credentials(monkeypatch):
    """Verify send_email gracefully returns False when credentials are missing or suppressed."""
    test_app = Flask(__name__)
    test_app.config["MAIL_SUPPRESS_SEND"] = True
    test_app.config["MAIL_USERNAME"] = ""
    test_app.config["MAIL_PASSWORD"] = ""
    test_app.config["RESEND_API_KEY"] = ""
    test_app.config["BREVO_API_KEY"] = ""

    with test_app.app_context():
        res = send_email("test@example.com", "Test Subject", "<p>Body</p>")
        assert res["success"] is False
        assert "not configured" in res["message"].lower() or "suppressed" in res["message"].lower()


def test_email_unicode_subject_handling():
    """Verify em-dash and special unicode characters do not cause UnicodeEncodeError."""
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.header import Header

    subject = "Low Attendance Alert \u2014 Mathematics"
    mime = MIMEMultipart("alternative")
    mime["Subject"] = Header(subject, "utf-8")
    mime["From"] = "test@cse.edu"
    mime["To"] = "student@cse.edu"
    mime.attach(MIMEText("Plain text body", "plain", "utf-8"))
    
    # Must format to string without throwing UnicodeEncodeError
    raw_str = mime.as_string()
    assert "Mathematics" in raw_str
