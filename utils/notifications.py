"""
Notification Service
Handles: Email (SMTP/Flask-Mail) + PWA Push (Web Push Protocol)
Both channels are optional — system works fine if neither is configured.
"""

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from extensions import db
from models.notification import InAppNotification

logger = logging.getLogger(__name__)


# ── Email notification ────────────────────────────────────────────────────────

def send_email(
    to: str,
    subject: str,
    body_html: str,
    body_text: Optional[str] = None,
) -> dict:
    """
    Send an email via Flask-Mail with dual-port fallback (SMTPS 465 / SMTP 587).
    Requires MAIL_SERVER, MAIL_USERNAME, MAIL_PASSWORD in config/.env
    Returns {"success": True/False, "message": str}
    """
    from flask import current_app
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    mail_user = (current_app.config.get("MAIL_USERNAME") or "").strip()
    mail_pw   = (current_app.config.get("MAIL_PASSWORD") or "").replace(" ", "").strip()
    mail_srv  = (current_app.config.get("MAIL_SERVER") or "smtp.gmail.com").strip()
    sender    = (current_app.config.get("MAIL_DEFAULT_SENDER") or mail_user or "noreply@cse.edu").strip()
    suppress  = current_app.config.get("MAIL_SUPPRESS_SEND", False)

    if suppress or not mail_user or not mail_pw:
        msg = "Email service not configured (MAIL_USERNAME/MAIL_PASSWORD missing or suppressed)."
        logger.warning(f"[EMAIL] {msg}")
        return {"success": False, "message": msg}

    plain_text = body_text or _html_to_text(body_html)

    # 0. HTTPS REST API (Port 443 — works 100% on Render Free Tier where SMTP is blocked)
    resend_key = (current_app.config.get("RESEND_API_KEY") or os.environ.get("RESEND_API_KEY", "")).strip()
    if resend_key:
        try:
            import urllib.request
            payload = json.dumps({
                "from": "EduFusion <onboarding@resend.dev>",
                "to": [to],
                "subject": subject,
                "html": body_html or f"<p>{plain_text}</p>",
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
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status in (200, 201):
                    logger.info(f"[EMAIL] Sent via Resend HTTPS API to {to}")
                    return {"success": True, "message": f"Email sent to {to}"}
        except Exception as e:
            logger.warning(f"[EMAIL] Resend HTTPS send failed ({type(e).__name__}: {e})")
            # In Resend free testing mode (onboarding@resend.dev), only the registered developer
            # account (mail_user) is authorized to receive emails. If an email is requested for
            # an unverified test student address, route it to the verified developer inbox so testing succeeds.
            if mail_user and to.lower() != mail_user.lower():
                try:
                    fwd_payload = json.dumps({
                        "from": "EduFusion <onboarding@resend.dev>",
                        "to": [mail_user],
                        "subject": f"[For: {to}] {subject}",
                        "html": f"<div style='background:#1f242c;padding:10px 16px;border-left:4px solid #f1e05a;color:#e6edf3;font-size:13px;margin-bottom:18px;border-radius:6px'><strong>Testing Mode Notice:</strong> Free Resend test domain routes all test alerts to the registered developer inbox (<strong>{mail_user}</strong>). This notification was generated for <strong>{to}</strong>.</div>" + (body_html or f"<p>{plain_text}</p>"),
                    }).encode("utf-8")
                    req_fwd = urllib.request.Request(
                        "https://api.resend.com/emails",
                        data=fwd_payload,
                        headers={
                            "Authorization": f"Bearer {resend_key}",
                            "Content-Type": "application/json",
                            "User-Agent": "EduFusion/1.0 (Academic Notification System)"
                        }
                    )
                    with urllib.request.urlopen(req_fwd, timeout=3) as resp_fwd:
                        if resp_fwd.status in (200, 201):
                            logger.info(f"[EMAIL] Routed test email for {to} to verified inbox {mail_user}")
                            return {"success": True, "message": f"Email delivered to {mail_user} (test route for {to})"}
                except Exception as ef:
                    logger.warning(f"[EMAIL] Resend test forward failed: {ef}")

    brevo_key = (current_app.config.get("BREVO_API_KEY") or os.environ.get("BREVO_API_KEY", "")).strip()
    if brevo_key:
        try:
            import urllib.request
            payload = json.dumps({
                "sender": {"name": "EduFusion", "email": sender if "@" in sender else "admin@cse.edu"},
                "to": [{"email": to}],
                "subject": subject,
                "htmlContent": body_html or f"<p>{plain_text}</p>",
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
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status in (200, 201):
                    logger.info(f"[EMAIL] Sent via Brevo HTTPS API to {to}")
                    return {"success": True, "message": f"Email sent to {to}"}
        except Exception as e:
            logger.warning(f"[EMAIL] Brevo HTTPS send failed ({type(e).__name__}: {e})")

    # 1. Primary: Flask-Mail (Configured with Port 587 STARTTLS)
    try:
        from extensions import mail, MAIL_AVAILABLE
        if MAIL_AVAILABLE and mail:
            from flask_mail import Message
            msg = Message(
                subject    = subject,
                recipients = [to],
                html       = body_html,
                body       = plain_text,
                sender     = sender,
            )
            mail.send(msg)
            logger.info(f"[EMAIL] Sent via Flask-Mail to {to}: {subject}")
            return {"success": True, "message": f"Email sent to {to}"}
    except Exception as e:
        logger.warning(f"[EMAIL] Flask-Mail to {to} failed ({type(e).__name__}: {e}). Trying direct SMTP...")

    from email.header import Header

    def _build_mime():
        mime = MIMEMultipart("alternative")
        mime["Subject"] = Header(subject, "utf-8")
        mime["From"]    = sender
        mime["To"]      = to
        mime.attach(MIMEText(plain_text, "plain", "utf-8"))
        if body_html:
            mime.attach(MIMEText(body_html, "html", "utf-8"))
        return mime

    # 2. Secondary: Direct STARTTLS (Port 587)
    try:
        mime_msg = _build_mime()
        with smtplib.SMTP(mail_srv, 587, timeout=3) as s:
            s.starttls()
            s.login(mail_user, mail_pw)
            s.send_message(mime_msg)
        logger.info(f"[EMAIL] Sent via direct SMTP port 587 to {to}: {subject}")
        return {"success": True, "message": f"Email sent to {to}"}
    except Exception as e:
        logger.warning(f"[EMAIL] Direct SMTP port 587 failed to {to}: {type(e).__name__}: {e}. Trying port 465...")

    # 3. Tertiary: Direct SMTPS (Port 465 SSL)
    try:
        mime_msg = _build_mime()
        with smtplib.SMTP_SSL(mail_srv, 465, timeout=3) as s:
            s.login(mail_user, mail_pw)
            s.send_message(mime_msg)
        logger.info(f"[EMAIL] Sent via direct SMTPS port 465 to {to}: {subject}")
        return {"success": True, "message": f"Email sent to {to}"}
    except Exception as e:
        logger.warning(f"[EMAIL] SMTPS port 465 to {to} failed: {type(e).__name__}: {e}")


    return {"success": False, "message": "Email delivery failed on all methods."}


def send_bulk_email(recipients: List[dict], subject: str, template_fn) -> dict:
    """
    Send personalised emails to a list of recipients.
    recipients: [{"email": ..., "name": ..., **extra_data}]
    template_fn: callable(recipient_dict) -> html_string
    """
    results = {"sent": 0, "failed": 0, "errors": []}
    for r in recipients:
        html   = template_fn(r)
        result = send_email(r["email"], subject, html)
        if result["success"]:
            results["sent"] += 1
        else:
            results["failed"] += 1
            results["errors"].append({"email": r["email"], "error": result["message"]})
    return results


# ── Email templates ───────────────────────────────────────────────────────────

def email_low_attendance(student_name: str, subject: str,
                          attendance_pct: float, required: int) -> str:
    color = "#f85149" if attendance_pct < 75 else "#d29922"
    return f"""
    <div style="font-family:Segoe UI,Arial,sans-serif;max-width:600px;margin:0 auto;
                background:#0d1117;color:#e6edf3;border-radius:12px;overflow:hidden">
      <div style="background:{color};padding:24px 32px">
        <h2 style="margin:0;color:#fff;font-size:20px">Attendance Alert</h2>
        <p style="margin:4px 0 0;color:rgba(255,255,255,.8);font-size:13px">
          CSE Department — Smart Academic System
        </p>
      </div>
      <div style="padding:28px 32px">
        <p style="font-size:15px;margin-bottom:16px">Dear <strong>{student_name}</strong>,</p>
        <p style="font-size:14px;color:#8b949e;margin-bottom:20px">
          Your attendance in <strong style="color:#e6edf3">{subject}</strong> has dropped below
          the required threshold.
        </p>
        <div style="background:#161b22;border-radius:10px;padding:20px;margin-bottom:20px;
                    border-left:4px solid {color}">
          <div style="font-size:32px;font-weight:700;color:{color}">{attendance_pct}%</div>
          <div style="font-size:13px;color:#8b949e;margin-top:4px">Current attendance</div>
        </div>
        <div style="background:#161b22;border-radius:10px;padding:16px;margin-bottom:24px">
          <p style="margin:0;font-size:13px;color:#8b949e">
            You need to attend
            <strong style="color:#e6edf3;font-size:15px">{required} more consecutive classes</strong>
            to reach the 85% minimum requirement.
          </p>
        </div>
        <p style="font-size:13px;color:#8b949e">
          Please use the <strong>What-If Simulator</strong> on the portal to plan your attendance.
        </p>
      </div>
      <div style="background:#161b22;padding:16px 32px;font-size:11px;color:#8b949e">
        CSE Department · Academic Management System · This is an automated alert.
      </div>
    </div>"""


def email_leave_status(student_name: str, status: str,
                        from_date: str, to_date: str,
                        comment: str = "", role: str = "Mentor") -> str:
    color   = "#3fb950" if status == "approved" else "#f85149"
    heading = f"Leave {status.title()}"
    return f"""
    <div style="font-family:Segoe UI,Arial,sans-serif;max-width:600px;margin:0 auto;
                background:#0d1117;color:#e6edf3;border-radius:12px;overflow:hidden">
      <div style="background:{color};padding:24px 32px">
        <h2 style="margin:0;color:#fff;font-size:20px">{heading}</h2>
        <p style="margin:4px 0 0;color:rgba(255,255,255,.8);font-size:13px">
          CSE Department — Smart Academic System
        </p>
      </div>
      <div style="padding:28px 32px">
        <p style="font-size:15px;margin-bottom:16px">Dear <strong>{student_name}</strong>,</p>
        <p style="font-size:14px;color:#8b949e;margin-bottom:20px">
          Your leave request has been
          <strong style="color:{color}">{status}</strong> by {role}.
        </p>
        <div style="background:#161b22;border-radius:10px;padding:16px;margin-bottom:20px">
          <table style="width:100%;font-size:13px">
            <tr><td style="color:#8b949e;padding:4px 0">From</td>
                <td style="font-weight:600">{from_date}</td></tr>
            <tr><td style="color:#8b949e;padding:4px 0">To</td>
                <td style="font-weight:600">{to_date}</td></tr>
            <tr><td style="color:#8b949e;padding:4px 0">Decision</td>
                <td style="font-weight:600;color:{color}">{status.upper()}</td></tr>
          </table>
        </div>
        {f'<div style="background:#161b22;border-radius:8px;padding:12px;font-size:13px;color:#8b949e;border-left:3px solid {color}"><strong style="color:#e6edf3">{role} comment:</strong> {comment}</div>' if comment else ''}
      </div>
      <div style="background:#161b22;padding:16px 32px;font-size:11px;color:#8b949e">
        CSE Department · Academic Management System · This is an automated notification.
      </div>
    </div>"""


def email_placement_alert(student_name: str, readiness_score: float,
                           is_eligible: bool) -> str:
    color = "#3fb950" if is_eligible else "#d29922"
    return f"""
    <div style="font-family:Segoe UI,Arial,sans-serif;max-width:600px;margin:0 auto;
                background:#0d1117;color:#e6edf3;border-radius:12px;overflow:hidden">
      <div style="background:#58a6ff;padding:24px 32px">
        <h2 style="margin:0;color:#fff;font-size:20px">Placement Update</h2>
        <p style="margin:4px 0 0;color:rgba(255,255,255,.8);font-size:13px">
          CSE Department — Smart Academic System
        </p>
      </div>
      <div style="padding:28px 32px">
        <p style="font-size:15px;margin-bottom:16px">Dear <strong>{student_name}</strong>,</p>
        <div style="background:#161b22;border-radius:10px;padding:20px;margin-bottom:20px;
                    border-left:4px solid {color};text-align:center">
          <div style="font-size:40px;font-weight:700;color:{color}">{readiness_score}%</div>
          <div style="font-size:13px;color:#8b949e;margin-top:4px">Placement Readiness Score</div>
          <div style="margin-top:10px;font-size:13px;font-weight:600;color:{color}">
            {'ELIGIBLE for placement' if is_eligible else 'NOT YET ELIGIBLE'}
          </div>
        </div>
        <p style="font-size:13px;color:#8b949e">
          Log in to the portal to update your profile, add certifications,
          and improve your readiness score.
        </p>
      </div>
      <div style="background:#161b22;padding:16px 32px;font-size:11px;color:#8b949e">
        CSE Department · Academic Management System
      </div>
    </div>"""


def email_risk_alert(student_name: str, risk_level: str, reasons: list,
                      action: str) -> str:
    color = {"HIGH": "#f85149", "MEDIUM": "#d29922", "LOW": "#3fb950"}.get(risk_level, "#58a6ff")
    reasons_html = "".join(
        f'<li style="margin-bottom:4px;color:#8b949e">{r}</li>' for r in reasons
    )
    return f"""
    <div style="font-family:Segoe UI,Arial,sans-serif;max-width:600px;margin:0 auto;
                background:#0d1117;color:#e6edf3;border-radius:12px;overflow:hidden">
      <div style="background:{color};padding:24px 32px">
        <h2 style="margin:0;color:#fff;font-size:20px">Academic Risk Alert — {risk_level} RISK</h2>
        <p style="margin:4px 0 0;color:rgba(255,255,255,.8);font-size:13px">
          CSE Department — Smart Academic System
        </p>
      </div>
      <div style="padding:28px 32px">
        <p style="font-size:15px;margin-bottom:16px">Dear <strong>{student_name}</strong>,</p>
        <p style="font-size:14px;color:#8b949e;margin-bottom:16px">
          Our AI system has flagged your academic profile as
          <strong style="color:{color}">{risk_level} RISK</strong>.
        </p>
        {'<ul style="padding-left:18px;margin-bottom:16px">' + reasons_html + '</ul>' if reasons else ''}
        <div style="background:#161b22;border-radius:8px;padding:14px;
                    border-left:3px solid {color};font-size:13px">
          <strong style="color:#e6edf3">Recommended action:</strong>
          <span style="color:#8b949e;margin-left:6px">{action}</span>
        </div>
      </div>
      <div style="background:#161b22;padding:16px 32px;font-size:11px;color:#8b949e">
        CSE Department · Academic Management System
      </div>
    </div>"""


# ── PWA Push notification ─────────────────────────────────────────────────────

def send_push_notification(
    subscription_info: dict,
    title: str,
    body: str,
    url: str = "/dashboard/",
    icon: str = "/static/icons/icon-192.png",
) -> dict:
    """
    Send a Web Push notification to a subscribed device.
    Requires VAPID keys in config + pywebpush installed.
    subscription_info: the PushSubscription JSON from the browser.
    """
    try:
        from pywebpush import webpush, WebPushException
        from flask import current_app

        vapid_private = current_app.config.get("VAPID_PRIVATE_KEY")
        vapid_claims  = {
            "sub": f"mailto:{current_app.config.get('VAPID_CLAIMS_EMAIL', 'admin@cse.edu')}"
        }

        if not vapid_private:
            return {"success": False, "message": "VAPID_PRIVATE_KEY not configured."}

        vapid_key_param = vapid_private
        try:
            from py_vapid import Vapid
            if isinstance(vapid_private, str) and "BEGIN PRIVATE KEY" in vapid_private:
                vapid_key_param = Vapid.from_pem(vapid_private.encode())
        except Exception:
            pass

        payload = json.dumps({"title": title, "body": body, "url": url, "icon": icon})

        webpush(
            subscription_info = subscription_info,
            data              = payload,
            vapid_private_key = vapid_key_param,
            vapid_claims      = vapid_claims,
        )
        return {"success": True, "message": "Push notification sent."}

    except ImportError:
        return {"success": False, "message": "pywebpush not installed. Run: pip install pywebpush"}
    except Exception as e:
        logger.error(f"[PUSH] Failed: {e}")
        return {"success": False, "message": str(e)}


def send_push_to_all(student_ids: List[int], title: str,
                      body: str, url: str = "/dashboard/") -> dict:
    """Send push notifications to all subscribed students."""
    from models.notification import PushSubscription
    subs    = PushSubscription.query.filter(
        PushSubscription.user_id.in_(student_ids)).all()
    results = {"sent": 0, "failed": 0}
    for sub in subs:
        try:
            info = json.loads(sub.subscription_json)
            r    = send_push_notification(info, title, body, url)
            if r["success"]: results["sent"] += 1
            else:             results["failed"] += 1
        except Exception:
            results["failed"] += 1
    return results


# ── Notification dispatch helpers ─────────────────────────────────────────────

def notify_low_attendance(student, summary):
    """Send email + push + in-app notification for low attendance.
    Dedup: skips in-app notification if one was already sent in the last 24 hours
    for the same student and subject (prevents spam on re-uploads).
    """
    from datetime import timedelta
    results = []

    # In-App Notification — with 24h dedup
    if summary.attendance_pct < 75:
        body = f"Attendance in {summary.subject} is critically low ({summary.attendance_pct}%). You need {summary.required_classes} more classes to reach 85%."
        color = "#f85149"
    else:
        body = f"Attendance in {summary.subject} has dropped to {summary.attendance_pct}%. Please attend regular classes."
        color = "#d29922"

    title = f"Low Attendance: {summary.subject}"

    # Check if we already sent this same notification in the last 24 hours
    from datetime import datetime
    cutoff = datetime.utcnow() - timedelta(hours=24)
    recent = InAppNotification.query.filter(
        InAppNotification.user_id == student.id,
        InAppNotification.title   == title,
        InAppNotification.created_at >= cutoff,
    ).first()

    if not recent:
        in_app = InAppNotification(
            user_id = student.id,
            title   = title,
            body    = body,
            icon    = "bi-exclamation-triangle",
            color   = color,
            ref_url = "/attendance/my"
        )
        db.session.add(in_app)
        db.session.commit()

    # Email
    html = email_low_attendance(
        student.name, summary.subject,
        summary.attendance_pct, summary.required_classes
    )
    results.append(send_email(student.email, f"Low Attendance Alert — {summary.subject}", html))

    # Push
    results.append(send_push_to_all(
        [student.id],
        "Low Attendance Alert",
        body,
        url="/attendance/my"
    ))
    return results


def notify_leave_decision(leave, action: str, comment: str, role: str):
    """Notify student when leave is approved/rejected."""
    from models.user import User
    student = User.query.get(leave.student_id)
    if not student:
        return

    # In-App Notification
    color = "#3fb950" if action == "approve" else "#f85149"
    icon = "bi-check-circle" if action == "approve" else "bi-x-circle"
    status_word = "approved" if action == "approve" else "rejected"
    in_app = InAppNotification(
        user_id = student.id,
        title   = f"Leave {status_word.title()} by {role}",
        body    = f"Your leave from {leave.from_date} to {leave.to_date} was {status_word}. {comment}",
        icon    = icon,
        color   = color,
        ref_url = "/dashboard/"
    )
    db.session.add(in_app)
    db.session.commit()

    html = email_leave_status(
        student.name, f"{action}d",
        str(leave.from_date), str(leave.to_date),
        comment, role
    )
    send_email(student.email, f"Leave Request {action.title()}d — {role}", html)
    send_push_to_all(
        [student.id],
        f"Leave {action.title()}d",
        f"Your leave ({leave.from_date} → {leave.to_date}) has been {action}d by {role}.",
        url="/dashboard/"
    )


def notify_risk_detected(student, risk_level: str, reasons: list, action: str):
    """Notify student + faculty when high/medium risk detected."""
    # In-App Notification
    color = {"HIGH": "#f85149", "MEDIUM": "#d29922", "LOW": "#3fb950"}.get(risk_level, "#58a6ff")
    in_app = InAppNotification(
        user_id = student.id,
        title   = f"Academic Risk Alert — {risk_level}",
        body    = f"Your academic profile has been flagged as {risk_level} RISK. {action}",
        icon    = "bi-cpu",
        color   = color,
        ref_url = f"/ml/analyze/{student.id}"
    )
    db.session.add(in_app)
    db.session.commit()

    html = email_risk_alert(student.name, risk_level, reasons, action)
    send_email(
        student.email,
        f"Academic Risk Alert — {risk_level} RISK Detected",
        html
    )
    if risk_level == "HIGH":
        send_push_to_all(
            [student.id],
            "Academic Risk Alert",
            f"Your academic risk level is HIGH. {action}",
            url=f"/ml/analyze/{student.id}"
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _html_to_text(html: str) -> str:
    """Minimal HTML → plain text strip."""
    import re
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text
