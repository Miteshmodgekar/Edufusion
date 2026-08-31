"""Alert & Notification Blueprint."""

import json
from flask import Blueprint, request, jsonify, render_template, current_app
from flask_login import login_required, current_user
from extensions import db
from models.attendance import AttendanceSummary
from models.notification import PushSubscription, NotificationLog, InAppNotification
from models.user import User
from config import Config
from utils.live_attendance import (
    get_live_defaulters, get_avg_attendance_for_student, get_live_attendance_path
)

alert_bp = Blueprint("alert", __name__)


# ── Student: own alerts ───────────────────────────────────────────────────────

@alert_bp.route("/my")
@login_required
def my_alerts():
    alerts = []
    if current_user.is_student:
        from utils.live_attendance import read_live_attendance_for_student
        live_path = get_live_attendance_path(current_app.config)
        live = read_live_attendance_for_student(
            live_path, current_user, threshold=Config.ATTENDANCE_THRESHOLD
        )

        if live["success"] and live["subjects"]:
            rows = live["subjects"]
        else:
            db_summaries = AttendanceSummary.query.filter_by(
                student_id=current_user.id).all()
            rows = [s.to_dict() for s in db_summaries]

        for s in rows:
            pct = s["attendance_pct"]
            if pct < 75:
                alerts.append({
                    "type":    "danger",
                    "icon":    "exclamation-triangle",
                    "title":   "Critical attendance",
                    "message": f"{s['subject']}: {pct}% — Need {s['required_classes']} more classes.",
                })
            elif pct < 85:
                alerts.append({
                    "type":    "warning",
                    "icon":    "exclamation-circle",
                    "title":   "Low attendance",
                    "message": f"{s['subject']}: {pct}% — Below 85% threshold.",
                })

    logs = (NotificationLog.query
            .filter_by(user_id=current_user.id)
            .order_by(NotificationLog.created_at.desc())
            .limit(10).all())

    return jsonify({
        "success": True,
        "alerts":  alerts,
        "count":   len(alerts),
        "logs":    [l.to_dict() for l in logs],
    })


# ── Notification centre page ──────────────────────────────────────────────────

@alert_bp.route("/centre")
@login_required
def centre():
    return render_template("notifications/centre.html")


# ── Broadcast ─────────────────────────────────────────────────────────────────

@alert_bp.route("/broadcast", methods=["POST"])
@login_required
def broadcast():
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Access denied."}), 403

    data    = request.get_json() or {}
    title   = data.get("title", "Notification from Department")
    body    = data.get("body",  "Please check the portal for updates.")
    targets = data.get("targets", "all")

    if targets == "defaulters":
        sids = list({s.student_id for s in
                     AttendanceSummary.query.filter_by(is_defaulter=True).all()})
    else:
        sids = [u.id for u in User.query.filter_by(role="student", is_active=True).all()]

    # Log notification + create in-app bell notification for each student
    for sid in sids:
        NotificationLog.log(sid, "push", "broadcast", title, body, status="sent")
        db.session.add(InAppNotification(
            user_id    = sid,
            title      = title,
            body       = body,
            icon       = "bi-megaphone-fill",
            color      = "#58a6ff",
            notif_type = "general",
        ))
    db.session.commit()

    # Try push notifications (won't crash if pywebpush not installed)
    push_sent = _try_push_bulk(sids, title, body)

    return jsonify({
        "success": True,
        "message": f"Broadcast sent to {len(sids)} students. Push: {push_sent}",
    })


# ── Targeted notification triggers ───────────────────────────────────────────

@alert_bp.route("/send/attendance", methods=["POST"])
@login_required
def send_attendance_alerts():
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Access denied."}), 403

    from types import SimpleNamespace

    dept = None if current_user.is_admin else current_user.department
    live_path = get_live_attendance_path(current_app.config)
    live_defaulters, live_available = get_live_defaulters(
        live_path, threshold=Config.ATTENDANCE_THRESHOLD, department=dept
    )

    sent, skipped_unmatched = 0, 0

    if live_available:
        for d in live_defaulters:
            if not d["student_id"]:
                skipped_unmatched += 1   # USN in the sheet doesn't match any account
                continue
            student = User.query.get(d["student_id"])
            if not student:
                continue
            summary = SimpleNamespace(
                subject=d["subject"], attendance_pct=d["attendance_pct"],
                required_classes=d["required_classes"],
            )
            title = f"Low Attendance — {d['subject']}"
            body  = f"{d['subject']}: {d['attendance_pct']}% — Need {d['required_classes']} more classes."
            NotificationLog.log(student.id, "email", "low_attendance", title, body, status="queued")
            try:
                # Creates the in-app notification AND sends email + push in one call.
                from utils.notifications import notify_low_attendance
                notify_low_attendance(student, summary)
            except Exception:
                pass
            sent += 1
    else:
        defaulters = AttendanceSummary.query.filter_by(is_defaulter=True).all()
        for s in defaulters:
            student = User.query.get(s.student_id)
            if student:
                title = f"Low Attendance — {s.subject}"
                body  = f"{s.subject}: {s.attendance_pct}% — Need {s.required_classes} more classes."
                NotificationLog.log(student.id, "email", "low_attendance", title, body, status="queued")
                try:
                    from utils.notifications import notify_low_attendance
                    notify_low_attendance(student, s)
                except Exception:
                    pass
                sent += 1

    db.session.commit()

    msg = f"Attendance alerts queued for {sent} defaulter row(s)."
    if skipped_unmatched:
        msg += f" ({skipped_unmatched} row(s) in the sheet didn't match any student account.)"

    return jsonify({
        "success": True,
        "source":  "live" if live_available else "db",
        "sent":    sent,
        "message": msg,
    })


@alert_bp.route("/send/risk", methods=["POST"])
@login_required
def send_risk_alerts():
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Access denied."}), 403

    from routes.ml_routes import classify_risk
    dept = None if current_user.is_admin else current_user.department
    query = User.query.filter_by(role="student", is_active=True)
    if dept:
        query = query.filter_by(department=dept)
    students = query.all()

    live_path = get_live_attendance_path(current_app.config)
    high_risk = 0

    for student in students:
        avg_att, _src = get_avg_attendance_for_student(
            live_path, student, threshold=Config.ATTENDANCE_THRESHOLD
        )
        risk = classify_risk(avg_att, 25, 50)
        if risk["risk_level"] in ("HIGH", "MEDIUM"):
            NotificationLog.log(
                student.id, "email", "risk_alert", f"Risk Alert — {risk['risk_level']}",
                risk["action"], status="queued"
            )
            try:
                # Creates the in-app notification AND sends email (+ push if HIGH).
                from utils.notifications import notify_risk_detected
                notify_risk_detected(student, risk["risk_level"], risk["reasons"], risk["action"])
            except Exception:
                pass
            if risk["risk_level"] == "HIGH":
                high_risk += 1

    db.session.commit()

    return jsonify({
        "success":   True,
        "scanned":   len(students),
        "high_risk": high_risk,
        "message":   f"Risk scan complete. {high_risk} HIGH-risk alerts queued.",
    })


# ── PWA Push subscription ─────────────────────────────────────────────────────

@alert_bp.route("/push/vapid-public")
def vapid_public_key():
    from flask import current_app
    return jsonify({"vapid_public_key": current_app.config.get("VAPID_PUBLIC_KEY", "")})


@alert_bp.route("/push/subscribe", methods=["POST"])
@login_required
def push_subscribe():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "No data."}), 400
    sub_json = json.dumps(data)
    existing = PushSubscription.query.filter_by(
        user_id=current_user.id, subscription_json=sub_json).first()
    if not existing:
        sub = PushSubscription(
            user_id           = current_user.id,
            subscription_json = sub_json,
            user_agent        = request.headers.get("User-Agent", "")[:200],
        )
        db.session.add(sub)
        db.session.commit()
    return jsonify({"success": True, "message": "Subscribed."})


@alert_bp.route("/push/unsubscribe", methods=["DELETE"])
@login_required
def push_unsubscribe():
    PushSubscription.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()
    return jsonify({"success": True})


# ── Notification logs ─────────────────────────────────────────────────────────

@alert_bp.route("/logs")
@login_required
def notification_logs():
    if current_user.is_student:
        # Students see only their own notification history
        logs = (NotificationLog.query
                .filter_by(user_id=current_user.id)
                .order_by(NotificationLog.created_at.desc())
                .limit(20).all())
    else:
        # Faculty/HOD/Admin see only broadcast & risk_alert events (things they sent),
        # NOT individual low_attendance student notifications (those are private to students)
        logs = (NotificationLog.query
                .filter(NotificationLog.event_type.in_(["broadcast", "risk_alert"]))
                .order_by(NotificationLog.created_at.desc())
                .limit(50).all())
    return jsonify({"success": True, "logs": [l.to_dict() for l in logs]})


@alert_bp.route("/logs/clear", methods=["DELETE"])
@login_required
def clear_notification_logs():
    """Delete notification history for the current user."""
    try:
        if current_user.is_student:
            # Students clear only their own logs
            NotificationLog.query.filter_by(user_id=current_user.id).delete()
        else:
            # Faculty/HOD/Admin clear broadcast + risk_alert logs
            NotificationLog.query.filter(
                NotificationLog.event_type.in_(["broadcast", "risk_alert"])
            ).delete()
        db.session.commit()
        return jsonify({"success": True, "message": "Notification history cleared."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error: {e}"}), 500



# ── Internal helpers ──────────────────────────────────────────────────────────

def _try_push_bulk(user_ids, title, body, url="/dashboard/"):
    """Try push notifications — returns count sent, 0 if pywebpush not available."""
    try:
        from utils.notifications import send_push_notification
        subs = PushSubscription.query.filter(
            PushSubscription.user_id.in_(user_ids)).all()
        sent = 0
        for sub in subs:
            try:
                info = json.loads(sub.subscription_json)
                r    = send_push_notification(info, title, body, url)
                if r.get("success"):
                    sent += 1
            except Exception:
                pass
        return sent
    except Exception:
        return 0
