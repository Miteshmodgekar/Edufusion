"""
Configuration — CSE Smart Academic Management System
Database: MySQL (default). SQLite is used for testing only.
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
    # Secure flag: on in production (HTTPS), off in local dev (HTTP).
    # Override in ProductionConfig below, or set FORCE_HTTPS=1 in .env.
    SESSION_COOKIE_SECURE     = os.environ.get("FORCE_HTTPS", "0").strip() not in ("0", "", "false")
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

    # ── Auto-enable SSL for Aiven MySQL ──────────────────────────────────────
    # Aiven MySQL requires SSL with its self-signed certificate.
    # PyMySQL requires an SSLContext passed as {"ssl": ctx} in connect_args.
    _db_url  = os.environ.get("DATABASE_URL", "")
    _ssl_args = {}
    if "aivencloud.com" in _db_url:
        import ssl as _ssl
        _ctx = _ssl.create_default_context()
        _ctx.check_hostname = False
        _ctx.verify_mode = _ssl.CERT_NONE
        _ssl_args = {"ssl": _ctx}

    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_recycle":  120,    # Aiven closes idle connections; recycle well before that
        "pool_pre_ping": True,   # test connection liveness before checkout
        "pool_size":     5,      # keep low for Render free tier
        "max_overflow":  10,
        "pool_timeout":  30,
        "connect_args":  _ssl_args,
    }

    # ── File Uploads ───────────────────────────────────────────────────────
    UPLOAD_FOLDER      = os.path.join(BASE_DIR, "uploads")
    ALLOWED_EXTENSIONS = {"xlsx", "xls", "csv", "pdf"}
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024

    # ── Attendance Rules ───────────────────────────────────────────────────
    ATTENDANCE_THRESHOLD         = 85.0
    ATTENDANCE_WARNING_THRESHOLD = 75.0

    # ── Placement Eligibility ──────────────────────────────────────────────
    MIN_PLACEMENT_CGPA = 6.0   # Minimum CGPA required for placement eligibility

    # ── ML Paths ───────────────────────────────────────────────────────────
    ML_MODEL_PATH  = os.path.join(BASE_DIR, "ml", "model.pkl")
    ML_SCALER_PATH = os.path.join(BASE_DIR, "ml", "scaler.pkl")

    # ── Email ──────────────────────────────────────────────────────────────
    MAIL_SERVER         = os.environ.get("MAIL_SERVER",   "smtp.gmail.com").strip()
    MAIL_PORT           = int(os.environ.get("MAIL_PORT", 465 if os.environ.get("MAIL_USE_SSL", "").lower() in ("true", "1") else 587))
    MAIL_USE_TLS        = os.environ.get("MAIL_USE_TLS", "false" if MAIL_PORT == 465 else "true").lower() in ("true", "1")
    MAIL_USE_SSL        = os.environ.get("MAIL_USE_SSL", "true" if MAIL_PORT == 465 else "false").lower() in ("true", "1")
    _raw_mail_user      = os.environ.get("MAIL_USERNAME", "")
    MAIL_USERNAME       = _raw_mail_user.strip() if _raw_mail_user else ""
    _raw_mail_pw        = os.environ.get("MAIL_PASSWORD", "")
    MAIL_PASSWORD       = _raw_mail_pw.replace(" ", "").strip() if _raw_mail_pw else ""
    MAIL_DEFAULT_SENDER = (
        os.environ.get("MAIL_DEFAULT_SENDER", "").strip() or
        MAIL_USERNAME or
        "noreply@cse.edu"
    )
    _default_suppress   = "false" if MAIL_PASSWORD else "true"
    MAIL_SUPPRESS_SEND  = os.environ.get("MAIL_SUPPRESS_SEND", _default_suppress).lower() not in ("false", "0", "no")

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
        pem_b64 = os.environ.get("VAPID_PRIVATE_PEM_B64", "").strip()
        if pem_b64:
            # Handle accidental duplicate key names (e.g., VAPID_PRIVATE_PEM_B64=VAPID_PRIVATE_PEM_B64=...)
            while pem_b64.startswith("VAPID_PRIVATE_PEM_B64="):
                pem_b64 = pem_b64[len("VAPID_PRIVATE_PEM_B64="):].strip()
            pem_b64 = pem_b64.strip('"\'')
            import base64
            try:
                decoded = base64.b64decode(pem_b64).decode()
                if "BEGIN PRIVATE KEY" in decoded:
                    return decoded
            except Exception:
                pass
        # Legacy fallback: raw PEM or base64 DER
        raw = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
        while raw.startswith("VAPID_PRIVATE_KEY="):
            raw = raw[len("VAPID_PRIVATE_KEY="):].strip()
        return raw.strip('"\'')

    VAPID_PRIVATE_KEY = _load_vapid_private.__func__()



class DevelopmentConfig(Config):
    DEBUG                = True
    TEMPLATES_AUTO_RELOAD = True   # hot-reload Jinja templates in dev
    SESSION_COOKIE_SECURE = False  # HTTP is fine locally


class ProductionConfig(Config):
    DEBUG                 = False
    TEMPLATES_AUTO_RELOAD = False  # no dev feature in prod
    SESSION_COOKIE_SECURE = True   # must be HTTPS in production
    MAIL_SUPPRESS_SEND    = False


class TestingConfig(Config):
    TESTING                = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


config_by_name = {
    "development": DevelopmentConfig,
    "production":  ProductionConfig,
    "testing":     TestingConfig,
}
