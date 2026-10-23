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


@alert_bp.route("/brevo-status")
def brevo_status():
    import os, urllib.request, json
    brevo_key = (current_app.config.get("BREVO_API_KEY") or os.environ.get("BREVO_API_KEY", "")).strip()
    if not brevo_key:
        return jsonify({"error": "No BREVO_API_KEY found"})
    
    headers = {"api-key": brevo_key, "Content-Type": "application/json", "User-Agent": "EduFusion/1.0"}
    results = {}
    
    # 1. Account details
    try:
        req = urllib.request.Request("https://api.brevo.com/v3/account", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as r:
            results["account"] = json.loads(r.read().decode())
    except Exception as e:
        results["account_error"] = str(e)
        
    # 2. Transactional email events
    try:
        req = urllib.request.Request("https://api.brevo.com/v3/smtp/statistics/events?limit=10", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as r:
            results["events"] = json.loads(r.read().decode())
    except Exception as e:
        results["events_error"] = str(e)

    # 3. Senders list
    try:
        req = urllib.request.Request("https://api.brevo.com/v3/senders", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as r:
            results["senders"] = json.loads(r.read().decode())
    except Exception as e:
        results["senders_error"] = str(e)

    # 4. Trigger test email with verified sender
    try:
        from utils.notifications import send_email
        results["send_result"] = send_email(
            "scammitya@gmail.com",
            "EduFusion Verified Email Test",
            "<div style='font-family:sans-serif;padding:20px;border-left:4px solid #238636'><h2>EduFusion Verified Delivery</h2><p>This message was sent using the verified Brevo sender address.</p></div>"
        )
    except Exception as e:
        results["send_result_error"] = str(e)

    return jsonify(results)



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





import threading

def _run_async(app, fn, *args):
    def _worker():
        with app.app_context():
            try:
                fn(*args)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"[ASYNC-ALERT] {e}")
    threading.Thread(target=_worker, daemon=True).start()


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
        NotificationLog.log(sid, "both", "broadcast", title, body, status="sent")
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

    # Email students in background thread so HTTP response returns in <200ms
    def _dispatch_broadcast_email(student_ids, b_title, b_body):
        from utils.notifications import send_email
        for sid in student_ids:
            st = db.session.get(User, sid)
            if st and st.email and "@" in st.email:
                html = f"""
                <div style="font-family:Segoe UI,Arial,sans-serif;max-width:600px;margin:0 auto;
                            background:#0d1117;color:#e6edf3;border-radius:12px;overflow:hidden">
                  <div style="background:#58a6ff;padding:24px 32px">
                    <h2 style="margin:0;color:#fff;font-size:20px">{b_title}</h2>
                    <p style="margin:4px 0 0;color:rgba(255,255,255,.8);font-size:13px">
                      CSE Department — Smart Academic System
                    </p>
                  </div>
                  <div style="padding:28px 32px">
                    <p style="font-size:15px;margin-bottom:16px">Dear <strong>{st.name}</strong>,</p>
                    <div style="background:#161b22;border-radius:10px;padding:16px;margin-bottom:20px;
                                border-left:4px solid #58a6ff;line-height:1.6">
                      {b_body}
                    </div>
                    <p style="font-size:13px;color:#8b949e">
                      Please log in to the portal for more details and actions.
                    </p>
                  </div>
                  <div style="background:#161b22;padding:16px 32px;font-size:11px;color:#8b949e">
                    CSE Department · Academic Management System · Broadcast Announcement
                  </div>
                </div>"""
                try:
                    send_email(st.email, f"Department Announcement: {b_title}", html, b_body)
                except Exception:
                    pass

    app = current_app._get_current_object()
    _run_async(app, _dispatch_broadcast_email, sids, title, body)

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
    from utils.notifications import notify_low_attendance

    dept = None if current_user.is_admin else current_user.department
    live_path = get_live_attendance_path(current_app.config)
    live_defaulters, live_available = get_live_defaulters(
        live_path, threshold=Config.ATTENDANCE_THRESHOLD, department=dept
    )

    sent, skipped_unmatched = 0, 0
    items_to_notify = []

    if live_available:
        for d in live_defaulters:
            if not d["student_id"]:
                skipped_unmatched += 1   # USN in the sheet doesn't match any account
                continue
            student = db.session.get(User, d["student_id"])
            if not student:
                continue
            summary = SimpleNamespace(
                subject=d["subject"], attendance_pct=d["attendance_pct"],
                required_classes=d["required_classes"],
            )
            title = f"Low Attendance — {d['subject']}"
            body  = f"{d['subject']}: {d['attendance_pct']}% — Need {d['required_classes']} more classes."
            NotificationLog.log(student.id, "email", "low_attendance", title, body, status="sent")
            items_to_notify.append((student.id, summary))
            sent += 1
    else:
        defaulters = AttendanceSummary.query.filter_by(is_defaulter=True).all()
        for s in defaulters:
            student = db.session.get(User, s.student_id)
            if student:
                title = f"Low Attendance — {s.subject}"
                body  = f"{s.subject}: {s.attendance_pct}% — Need {s.required_classes} more classes."
                NotificationLog.log(student.id, "email", "low_attendance", title, body, status="sent")
                items_to_notify.append((student.id, s))
                sent += 1

    db.session.commit()

    # Dispatch email & push notifications in background thread to respond in <200ms
    def _dispatch_attendance(items):
        for sid, summ in items:
            st = db.session.get(User, sid)
            if st:
                try:
                    notify_low_attendance(st, summ)
                except Exception:
                    pass

    app = current_app._get_current_object()
    _run_async(app, _dispatch_attendance, items_to_notify)

    msg = f"Attendance alerts dispatched for {sent} defaulter row(s)."
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
    from utils.notifications import notify_risk_detected

    dept = None if current_user.is_admin else current_user.department
    query = User.query.filter_by(role="student", is_active=True)
    if dept:
        query = query.filter_by(department=dept)
    students = query.all()

    live_path = get_live_attendance_path(current_app.config)
    high_risk = 0
    risk_items = []

    for student in students:
        avg_att, _src = get_avg_attendance_for_student(
            live_path, student, threshold=Config.ATTENDANCE_THRESHOLD
        )
        risk = classify_risk(avg_att, 25, 50)
        if risk["risk_level"] in ("HIGH", "MEDIUM"):
            NotificationLog.log(
                student.id, "email", "risk_alert", f"Risk Alert — {risk['risk_level']}",
                risk["action"], status="sent"
            )
            risk_items.append((student.id, risk["risk_level"], risk["reasons"], risk["action"]))
            if risk["risk_level"] == "HIGH":
                high_risk += 1

    db.session.commit()

    def _dispatch_risk(items):
        for sid, rlevel, reasons, action in items:
            st = db.session.get(User, sid)
            if st:
                try:
                    notify_risk_detected(st, rlevel, reasons, action)
                except Exception:
                    pass

    app = current_app._get_current_object()
    _run_async(app, _dispatch_risk, risk_items)

    return jsonify({
        "success":   True,
        "scanned":   len(students),
        "high_risk": high_risk,
        "message":   f"Risk scan complete. {high_risk} HIGH-risk alerts dispatched.",
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
