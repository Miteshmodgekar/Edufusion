"""
Configuration — CSE Smart Academic Management System
Supports both SQLite (default) and MySQL.
"""

import os
from datetime import timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    # ── Security ────────────────────────────────────────────────────
    _secret = os.environ.get("SECRET_KEY")
    if not _secret:
        import secrets, warnings
        _secret = secrets.token_hex(32)   # random per-process key
        warnings.warn(
            "\n[SECURITY] SECRET_KEY not set in .env! "
            "Sessions will be invalidated on every restart. "
            "Set SECRET_KEY in your .env file.",
            stacklevel=2
        )
    SECRET_KEY = _secret
    SESSION_COOKIE_HTTPONLY   = True
    SESSION_COOKIE_SAMESITE   = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

    # ── Database ───────────────────────────────────────────────────────────
    MYSQL_HOST     = os.environ.get("MYSQL_HOST",     "localhost")
    MYSQL_PORT     = os.environ.get("MYSQL_PORT",     "3306")
    MYSQL_USER     = os.environ.get("MYSQL_USER",     "root")
    MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
    MYSQL_DB       = os.environ.get("MYSQL_DB",       "cse_smart_db")

    SQLALCHEMY_DATABASE_URI = (
        os.environ.get("DATABASE_URL") or
        f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}?charset=utf8mb4"
    )

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_recycle":  280,    # recycle before Railway's 5min timeout
        "pool_pre_ping": True,   # test connection before use
        "pool_size":     10,     # maintain 10 persistent connections
        "max_overflow":  20,     # allow 20 extra under load
        "pool_timeout":  10,     # wait max 10s for a connection
    }

    # ── File Uploads ───────────────────────────────────────────────────────
    UPLOAD_FOLDER      = os.path.join(BASE_DIR, "uploads")
    ALLOWED_EXTENSIONS = {"xlsx", "xls", "csv", "pdf"}
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024

    # ── Attendance Rules ───────────────────────────────────────────────────
    ATTENDANCE_THRESHOLD         = 85.0
    ATTENDANCE_WARNING_THRESHOLD = 75.0

    # ── ML Paths ───────────────────────────────────────────────────────────
    ML_MODEL_PATH  = os.path.join(BASE_DIR, "ml", "model.pkl")
    ML_SCALER_PATH = os.path.join(BASE_DIR, "ml", "scaler.pkl")

    # ── Email ──────────────────────────────────────────────────────────────
    MAIL_SERVER         = os.environ.get("MAIL_SERVER",   "smtp.gmail.com")
    MAIL_PORT           = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS        = True
    MAIL_USERNAME       = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD       = os.environ.get("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "noreply@cse.edu")
    MAIL_SUPPRESS_SEND  = os.environ.get("MAIL_SUPPRESS_SEND", "true").lower() not in ("false", "0", "no")

    # ── AI Career Assistant ─────────────────────────────────────────────────
    # Primary: Google Gemini (1M TPM free — get key at aistudio.google.com)
    GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY", "")
    # Fallback: Groq (8K TPM free)
    GROQ_API_KEY    = os.environ.get("GROQ_API_KEY", "")
    CAREER_AI_MODEL = os.environ.get("CAREER_AI_MODEL", "gemini-2.0-flash")

    # ── PWA / Push Notifications ───────────────────────────────────────────────
    VAPID_PUBLIC_KEY   = os.environ.get("VAPID_PUBLIC_KEY",  "")
    VAPID_CLAIMS_EMAIL = os.environ.get("VAPID_CLAIMS_EMAIL", "admin@cse.edu")

    # Private key stored as base64(PEM) to avoid .env multiline issues
    @staticmethod
    def _load_vapid_private():
        # New format: base64-encoded PEM string
        pem_b64 = os.environ.get("VAPID_PRIVATE_PEM_B64", "")
        if pem_b64:
            import base64
            try:
                return base64.b64decode(pem_b64).decode()
            except Exception:
                pass
        # Legacy fallback: raw PEM or base64 DER
        return os.environ.get("VAPID_PRIVATE_KEY", "")

    VAPID_PRIVATE_KEY = _load_vapid_private.__func__()



class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG                 = False
    SESSION_COOKIE_SECURE = True
    MAIL_SUPPRESS_SEND    = False


class TestingConfig(Config):
    TESTING                = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


config_by_name = {
    "development": DevelopmentConfig,
    "production":  ProductionConfig,
    "testing":     TestingConfig,
}
