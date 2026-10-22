"""WSGI entry point for production (Gunicorn)."""
import os
os.environ.setdefault("FLASK_ENV", "production")

from app import create_app
from config import config_by_name

# Always use ProductionConfig in WSGI (Gunicorn) context so that
# SESSION_COOKIE_SECURE, DEBUG=False, and MAIL_SUPPRESS_SEND=False
# are guaranteed, regardless of what FLASK_ENV is set to.
app = create_app(config_by_name["production"])
