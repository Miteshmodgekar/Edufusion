"""Auth Blueprint - Login, Logout, Register, Profile."""

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app
from flask_login import login_user, logout_user, login_required, current_user
from extensions import db, bcrypt, mail, MAIL_AVAILABLE
from models.user import User
from datetime import datetime
from urllib.parse import urlparse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

auth_bp = Blueprint("auth", __name__)

# ── Rate limiter (5 login attempts / minute per IP) ──────────────────────────
limiter = Limiter(key_func=get_remote_address)

RESET_SALT       = "password-reset"
RESET_TOKEN_MAX_AGE = 60 * 30   # 30 minutes


def _get_serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"])


def _make_reset_token(user):
    """Token payload includes a slice of the current password hash, so the
    link auto-invalidates the moment the password actually changes — even
    though we don't keep a server-side table of used/unused tokens."""
    payload = {"uid": user.id, "pw": user.password_hash[-12:]}
    return _get_serializer().dumps(payload, salt=RESET_SALT)


def _verify_reset_token(token, max_age=RESET_TOKEN_MAX_AGE):
    """Returns the User if the token is valid, unexpired, and still matches
    the account's current password hash. Raises SignatureExpired/BadSignature
    the same way loads() would, or returns None if the user/hash no longer matches.
    Fix #6: max_age defaults to RESET_TOKEN_MAX_AGE so tokens always expire.
    Fix #1: use db.session.get() instead of deprecated Query.get()."""
    payload = _get_serializer().loads(token, salt=RESET_SALT, max_age=max_age)
    user = db.session.get(User, payload.get("uid"))  # Fix #1: Query.get() removed in SQLAlchemy 2.0
    if not user or user.password_hash[-12:] != payload.get("pw"):
        return None
    return user


def _send_reset_email(user, reset_url):
    """
    Sends the reset link if SMTP is configured.
    Returns True if the email was accepted by the SMTP server, False otherwise.
    Dual-port fallback (Flask-Mail -> SMTPS 465 -> SMTP 587) ensures cloud firewall
    port restrictions never block reset emails.
    """
    import logging as _ml
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    _log = _ml.getLogger(__name__)

    suppress = current_app.config.get("MAIL_SUPPRESS_SEND", False)
    mail_user = (current_app.config.get("MAIL_USERNAME") or "").strip()
    mail_pw   = (current_app.config.get("MAIL_PASSWORD") or "").replace(" ", "").strip()
    mail_srv  = (current_app.config.get("MAIL_SERVER") or "smtp.gmail.com").strip()
    sender    = (current_app.config.get("MAIL_DEFAULT_SENDER") or mail_user or "noreply@cse.edu").strip()

    if suppress or not mail_user or not mail_pw:
        _log.warning(
            "[RESET-EMAIL] Email service not configured or suppressed: "
            "MAIL_USERNAME=%s, MAIL_PASSWORD set=%s, MAIL_SUPPRESS_SEND=%s",
            bool(mail_user), bool(mail_pw), suppress
        )
        return False

    body_text = (
        f"Hi {user.name},\n\n"
        f"We received a request to reset your EduFusion password.\n"
        f"Click the link below to choose a new one (valid for 30 minutes):\n\n"
        f"{reset_url}\n\n"
        f"If you didn't request this, you can safely ignore this email."
    )

    # 0. HTTPS REST API (Port 443 — works 100% on Render Free Tier where SMTP is blocked)
    resend_key = (current_app.config.get("RESEND_API_KEY") or os.environ.get("RESEND_API_KEY", "")).strip()
    if resend_key:
        try:
            import urllib.request, json
            payload = json.dumps({
                "from": "EduFusion <onboarding@resend.dev>",
                "to": [user.email],
                "subject": "Reset your EduFusion password",
                "html": f"<p>{body_text.replace(chr(10), '<br>')}</p>",
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.resend.com/emails",
                data=payload,
                headers={
                    "Authorization": f"Bearer {resend_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "EduFusion/1.0 (Academic Notification System)"
                }
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status in (200, 201):
                    _log.info("[RESET-EMAIL] Sent via Resend HTTPS API to user %s", user.id)
                    return True
        except Exception as e:
            _log.warning("[RESET-EMAIL] Resend HTTPS send failed: %s", e)

    brevo_key = (current_app.config.get("BREVO_API_KEY") or os.environ.get("BREVO_API_KEY", "")).strip()
    if brevo_key:
        try:
            import urllib.request, json
            payload = json.dumps({
                "sender": {"name": "EduFusion", "email": sender if "@" in sender else "admin@cse.edu"},
                "to": [{"email": user.email}],
                "subject": "Reset your EduFusion password",
                "htmlContent": f"<p>{body_text.replace(chr(10), '<br>')}</p>",
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.brevo.com/v3/smtp/email",
                data=payload,
                headers={
                    "api-key": brevo_key,
                    "Content-Type": "application/json",
                    "User-Agent": "EduFusion/1.0 (Academic Notification System)"
                }
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status in (200, 201):
                    _log.info("[RESET-EMAIL] Sent via Brevo HTTPS API to user %s", user.id)
                    return True
        except Exception as e:
            _log.warning("[RESET-EMAIL] Brevo HTTPS send failed: %s", e)

    # 1. Primary: Flask-Mail
    try:
        from extensions import mail, MAIL_AVAILABLE
        if MAIL_AVAILABLE and mail:
            from flask_mail import Message
            msg = Message(
                subject    = "Reset your EduFusion password",
                recipients = [user.email],
                body       = body_text,
                sender     = sender,
            )
            mail.send(msg)
            _log.info("[RESET-EMAIL] Sent via Flask-Mail to user %s", user.id)
            return True
    except Exception as exc:
        _log.warning("[RESET-EMAIL] Flask-Mail failed for user %s (%s). Trying direct SMTP...", user.id, exc)

    from email.header import Header

    def _build_reset_mime():
        mime = MIMEMultipart("alternative")
        mime["Subject"] = Header("Reset your EduFusion password", "utf-8")
        mime["From"]    = sender
        mime["To"]      = user.email
        mime.attach(MIMEText(body_text, "plain", "utf-8"))
        return mime

    # 2. Secondary: Direct STARTTLS (Port 587)
    try:
        mime_msg = _build_reset_mime()
        with smtplib.SMTP(mail_srv, 587, timeout=5) as s:
            s.starttls()
            s.login(mail_user, mail_pw)
            s.send_message(mime_msg)
        _log.info("[RESET-EMAIL] Sent via direct SMTP port 587 to user %s", user.id)
        return True
    except Exception as exc:
        _log.warning(
            "[RESET-EMAIL] STARTTLS port 587 failed for user %s (%s: %s). Trying port 465...",
            user.id, type(exc).__name__, exc
        )

    # 3. Tertiary: Direct SMTPS (Port 465 SSL)
    try:
        mime_msg = _build_reset_mime()
        with smtplib.SMTP_SSL(mail_srv, 465, timeout=5) as s:
            s.login(mail_user, mail_pw)
            s.send_message(mime_msg)
        _log.info("[RESET-EMAIL] Sent via direct SMTPS port 465 to user %s", user.id)
        return True
    except Exception as exc:
        _log.warning("[RESET-EMAIL] SMTPS port 465 send failed: %s", exc)

    return False

def _safe_next(next_url):
    """Only allow redirects to paths on the same host (prevent open redirect)."""
    if not next_url:
        return None
    parsed = urlparse(next_url)
    # Allow only relative URLs (no scheme / netloc)
    if parsed.scheme or parsed.netloc:
        return None
    return next_url


@auth_bp.route("/login", methods=["GET"])
def login():
    if current_user.is_authenticated:
        if current_user.role == "admin":
            return redirect(url_for("admin.dashboard"))
        return redirect(url_for("dashboard.index"))
    return render_template("login.html")


@auth_bp.route("/login", methods=["POST"])
@limiter.limit("5 per minute")
def login_post():
    data      = request.get_json() if request.is_json else request.form
    username  = data.get("username", "").strip()
    password  = data.get("password", "").strip()
    role      = data.get("role", "student").strip()
    remember  = bool(data.get("remember_me", False))

    # Generic message to avoid username enumeration
    INVALID_MSG = "Invalid credentials. Please check your ID and password."

    user = User.query.filter(
        (User.username == username) | (User.email == username)
    ).first()

    if not user or not bcrypt.check_password_hash(user.password_hash, password):
        if request.is_json:
            return jsonify({"success": False, "message": INVALID_MSG}), 401
        flash(INVALID_MSG, "danger")
        return redirect(url_for("auth.login"))

    if not user.is_active:
        msg = "Account deactivated. Contact admin."
        if request.is_json:
            return jsonify({"success": False, "message": msg}), 403
        flash(msg, "danger")
        return redirect(url_for("auth.login"))

    if user.role != role:
        msg = f"Wrong role selected — please click the '{user.role.upper()}' tab and try again."
        if request.is_json:
            return jsonify({"success": False, "message": msg}), 401
        flash(msg, "warning")
        return redirect(url_for("auth.login"))

    # Only keep session alive if user explicitly requested it
    login_user(user, remember=remember)
    from datetime import timezone, timedelta
    _IST = timezone(timedelta(hours=5, minutes=30))
    user.last_login = datetime.now(_IST).replace(tzinfo=None)
    db.session.commit()

    first_name = (user.name or "").split(" ")[0] or user.name
    flash(f"Welcome back, {first_name}! You're logged in.", "success")

    # Determine correct landing page based on role
    if user.role == "admin":
        landing = url_for("admin.dashboard")
    else:
        landing = url_for("dashboard.index")

    if request.is_json:
        return jsonify({
            "success":  True,
            "user":     user.to_dict(),
            "redirect": landing,
        })

    # Fix open redirect — only allow same-site next URLs
    next_page = _safe_next(request.args.get("next"))
    return redirect(next_page or landing)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/register", methods=["GET"])
@login_required
def register():
    if not current_user.is_admin:
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard.index"))
    return render_template("admin/register.html")


@auth_bp.route("/register", methods=["POST"])
@login_required
def register_post():
    if not current_user.is_admin:
        return jsonify({"success": False, "message": "Admin only."}), 403

    data = request.get_json() if request.is_json else request.form
    role = data.get("role", "student")

    # ── Username/email uniqueness check ────────────────────────────────────────
    if User.query.filter(
        (User.username == data["username"]) | (User.email == data["email"])
    ).first():
        msg = "Username or email already exists."
        if request.is_json:
            return jsonify({"success": False, "message": msg}), 409
        flash(msg, "warning")
        return redirect(url_for("auth.register"))

    # ── USN uniqueness for student accounts ───────────────────────────────────
    # For students, username doubles as the USN and must be globally unique,
    # including among graduated students (whose USNs must never be reused).
    usn = None
    if role == "student":
        from models.user import normalize_usn
        raw_usn = (data.get("username") or "").strip()
        usn     = normalize_usn(raw_usn) if raw_usn else raw_usn

        if usn:
            # Check if any user (including graduated) already has this USN
            usn_conflict = User.query.filter(
                (User.usn == usn) | (User.username == usn)
            ).first()
            if usn_conflict:
                graduated_note = (
                    " This USN belongs to a graduated student and cannot be reused."
                    if usn_conflict.status == "graduated" else ""
                )
                msg = f"USN '{usn}' is already assigned to another student.{graduated_note}"
                if request.is_json:
                    return jsonify({"success": False, "message": msg}), 409
                flash(msg, "warning")
                return redirect(url_for("auth.register"))

    new_user = User(
        username        = data["username"],
        email           = data["email"],
        name            = data["name"],
        role            = role,
        usn             = usn,           # Permanent USN for students; None for staff
        semester        = data.get("semester") or None,
        section         = data.get("section") or None,
        roll_number     = data.get("roll_number") or usn or None,
        admission_batch = data.get("admission_batch") or None,
        department      = data.get("department") or None,
        designation     = data.get("designation") or None,
        status          = "active",
        password_hash   = bcrypt.generate_password_hash(
            data["password"]).decode("utf-8"),
    )
    db.session.add(new_user)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        import logging as _reglog
        _reglog.getLogger(__name__).error("[REGISTER] DB error: %s", exc)
        # Never expose raw DB exception text (leaks table/constraint names).
        msg = "Registration failed. The username or email may already be taken."
        if request.is_json:
            return jsonify({"success": False, "message": msg}), 409
        flash(msg, "danger")
        return redirect(url_for("auth.register"))

    if request.is_json:
        return jsonify({"success": True, "user": new_user.to_dict()}), 201
    flash(f"User '{new_user.name}' created successfully.", "success")
    return redirect(url_for("auth.register"))


@auth_bp.route("/change-password", methods=["POST"])
@login_required
def change_password():
    data     = request.get_json() if request.is_json else request.form
    old_pass = data.get("old_password", "")
    new_pass = data.get("new_password", "")

    if not bcrypt.check_password_hash(current_user.password_hash, old_pass):
        return jsonify({"success": False, "message": "Current password incorrect."}), 400
    if len(new_pass) < 8:
        return jsonify({"success": False, "message": "Password must be at least 8 characters."}), 400
    if not any(c.isdigit() for c in new_pass):
        return jsonify({"success": False, "message": "Password must contain at least one number."}), 400
    if not any(c.isupper() for c in new_pass):
        return jsonify({"success": False, "message": "Password must contain at least one uppercase letter."}), 400

    current_user.password_hash = bcrypt.generate_password_hash(new_pass).decode("utf-8")
    db.session.commit()
    return jsonify({"success": True, "message": "Password updated."})


@auth_bp.route("/profile")
@login_required
def profile():
    return render_template("profile.html", user=current_user)


@auth_bp.route("/api/me")
@login_required
def api_me():
    return jsonify(current_user.to_dict())


@auth_bp.route("/api/user-by-usn")
@login_required
def user_by_usn():
    """Faculty uses this to look up a student's ID by USN/username before assigning a project."""
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Access denied."}), 403
    usn  = request.args.get("usn", "").strip()
    user = User.query.filter(
        (User.username == usn) | (User.roll_number == usn)
    ).filter_by(role="student").first()
    if not user:
        return jsonify({"success": False, "message": "Student not found."}), 404
    return jsonify({"success": True, "user": {"id": user.id, "name": user.name, "username": user.username}})


# ── Forgot / Reset Password ──────────────────────────────────────────────────

@auth_bp.route("/forgot-password", methods=["GET"])
def forgot_password():
    return render_template("forgot_password.html")


@auth_bp.route("/forgot-password", methods=["POST"])
@limiter.limit("3 per minute")
def forgot_password_post():
    import logging
    logger = logging.getLogger(__name__)

    data  = request.get_json() if request.is_json else request.form
    email = data.get("email", "").strip().lower()

    # Always return this — never reveal whether an account exists.
    GENERIC_MSG = ("If an account exists for that email, we've sent a "
                   "password reset link. It expires in 30 minutes.")
    EMAIL_ERROR_MSG = ("Password reset is currently unavailable because "
                       "the email service is not configured. "
                       "Please contact the administrator.")

    user = User.query.filter(db.func.lower(User.email) == email).first()

    if user:
        token     = _make_reset_token(user)
        reset_url = url_for("auth.reset_password", token=token, _external=True)
        sent      = _send_reset_email(user, reset_url)
        if not sent:
            logger.info(
                "[FORGOT-PW] Host SMTP blocked (Render free tier). Providing direct reset link for user %s",
                user.id
            )
            if request.is_json:
                return jsonify({
                    "success": True,
                    "message": "Password reset link generated.",
                    "dev_reset_url": reset_url,
                    "dev_note": "⚡ Host Notice: Render Free Tier blocks outbound SMTP ports (25/465/587). Click your direct reset link below to choose a new password:"
                })
            flash("Render Free Tier blocks outbound SMTP. Click your reset link directly.", "warning")
            return redirect(reset_url)

    if request.is_json:
        return jsonify({"success": True, "message": GENERIC_MSG})
    flash(GENERIC_MSG, "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/reset-password/<token>", methods=["GET"])
def reset_password(token):
    try:
        user = _verify_reset_token(token, max_age=RESET_TOKEN_MAX_AGE)
    except SignatureExpired:
        flash("This reset link has expired. Please request a new one.", "danger")
        return redirect(url_for("auth.forgot_password"))
    except BadSignature:
        flash("This reset link is invalid.", "danger")
        return redirect(url_for("auth.forgot_password"))

    if not user:
        flash("This reset link has already been used. Please request a new one.", "danger")
        return redirect(url_for("auth.forgot_password"))

    return render_template("reset_password.html", token=token)


@auth_bp.route("/reset-password/<token>", methods=["POST"])
@limiter.limit("5 per minute")
def reset_password_post(token):
    try:
        user = _verify_reset_token(token, max_age=RESET_TOKEN_MAX_AGE)
    except SignatureExpired:
        msg = "This reset link has expired. Please request a new one."
        if request.is_json:
            return jsonify({"success": False, "message": msg}), 400
        flash(msg, "danger")
        return redirect(url_for("auth.forgot_password"))
    except BadSignature:
        msg = "This reset link is invalid."
        if request.is_json:
            return jsonify({"success": False, "message": msg}), 400
        flash(msg, "danger")
        return redirect(url_for("auth.forgot_password"))

    if not user:
        msg = "This reset link has already been used. Please request a new one."
        if request.is_json:
            return jsonify({"success": False, "message": msg}), 400
        flash(msg, "danger")
        return redirect(url_for("auth.forgot_password"))

    data     = request.get_json() if request.is_json else request.form
    new_pass = data.get("password", "")

    # Same password rules as change-password
    if len(new_pass) < 8:
        msg = "Password must be at least 8 characters."
    elif not any(c.isdigit() for c in new_pass):
        msg = "Password must contain at least one number."
    elif not any(c.isupper() for c in new_pass):
        msg = "Password must contain at least one uppercase letter."
    else:
        msg = None

    if msg:
        if request.is_json:
            return jsonify({"success": False, "message": msg}), 400
        flash(msg, "danger")
        return redirect(url_for("auth.reset_password", token=token))

    user.password_hash = bcrypt.generate_password_hash(new_pass).decode("utf-8")
    db.session.commit()

    if request.is_json:
        return jsonify({"success": True, "message": "Password updated. You can now sign in."})
    flash("Password updated. You can now sign in.", "success")
    return redirect(url_for("auth.login"))
