"""WSGI entry point for production (Gunicorn)."""
import os
os.environ.setdefault("FLASK_ENV", "production")

from app import create_app
app = create_app()
