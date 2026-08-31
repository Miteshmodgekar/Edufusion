"""
Admin Blueprint — User management, audit logs, reports, DB export.
"""

import io
import csv
import os
import shutil
from datetime import datetime
from flask import (Blueprint, jsonify, render_template, request,
                   send_file, redirect, url_for, flash, session)
from flask_login import login_required, current_user
from extensions import db
from models.user import User
from models.attendance import AttendanceRecord, AttendanceSummary, SheetRegistry
from models.leave import LeaveRequest
from models.placement import Project, ProjectUpdate, StudentPerformance, PlacementProfile, Internship
from models.notification import PushSubscription, NotificationLog, InAppNotification
from models.drive import PlacementDrive, DriveApplication

admin_bp = Blueprint("admin", __name__)

# ── Access guard ──────────────────────────────────────────────────────────────

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            return jsonify({"success": False, "message": "Admin only."}), 403
        return f(*args, **kwargs)
    return decorated


# ── Pages ─────────────────────────────────────────────────────────────────────

@admin_bp.route("/")
@admin_bp.route("/dashboard")
@login_required
@admin_required
def dashboard():
    return render_template("admin/dashboard.html")


# ── User Management API ───────────────────────────────────────────────────────

@admin_bp.route("/api/users")
@login_required
@admin_required
def api_users():
    users = User.query.order_by(User.role, User.name).all()
    return jsonify({"success": True, "users": [u.to_dict() for u in users]})


@admin_bp.route("/user/<int:user_id>/toggle", methods=["POST"])
@login_required
@admin_required
def toggle_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        return jsonify({"success": False, "message": "Cannot deactivate yourself."}), 400
    user.is_active = not user.is_active
    db.session.commit()
    return jsonify({
        "success": True,
        "is_active": user.is_active,
        "message": f"User {'activated' if user.is_active else 'deactivated'}."
    })


@admin_bp.route("/user/<int:user_id>", methods=["DELETE"])
@login_required
@admin_required
def delete_user(user_id):
    """
    Deletes a user and everything that references them, in the right
    order, so this doesn't blow up with a foreign-key error the moment
    the student has any attendance/leave/notification history (which,
    in practice, is almost always).
    """
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        return jsonify({"success": False, "message": "Cannot delete yourself."}), 400

    name = user.name
    try:
        uid = user.id

        # ── Attendance ───────────────────────────────────────────────────
        AttendanceRecord.query.filter(
            (AttendanceRecord.student_id == uid) | (AttendanceRecord.uploaded_by == uid)
        ).delete(synchronize_session=False)
        AttendanceSummary.query.filter_by(student_id=uid).delete(synchronize_session=False)
        SheetRegistry.query.filter_by(uploaded_by=uid).update(
            {"uploaded_by": None}, synchronize_session=False)

        # ── Leave ────────────────────────────────────────────────────────
        LeaveRequest.query.filter(
            (LeaveRequest.student_id == uid) |
            (LeaveRequest.mentor_id == uid) |
            (LeaveRequest.hod_id == uid)
        ).delete(synchronize_session=False)

        # ── Projects (delete weekly updates first, then the projects) ─────
        project_ids = [p.id for p in Project.query.filter(
            (Project.student_id == uid) | (Project.guide_id == uid)
        ).all()]
        if project_ids:
            ProjectUpdate.query.filter(ProjectUpdate.project_id.in_(project_ids)) \
                .delete(synchronize_session=False)
        Project.query.filter(
            (Project.student_id == uid) | (Project.guide_id == uid)
        ).delete(synchronize_session=False)

        # ── Placement ────────────────────────────────────────────────────
        StudentPerformance.query.filter_by(student_id=uid).delete(synchronize_session=False)
        profile = PlacementProfile.query.filter_by(student_id=uid).first()
        if profile:
            Internship.query.filter_by(profile_id=profile.id).delete(synchronize_session=False)
            db.session.delete(profile)

        # ── Drives ───────────────────────────────────────────────────────
        DriveApplication.query.filter_by(student_id=uid).delete(synchronize_session=False)
        PlacementDrive.query.filter_by(posted_by=uid).update(
            {"posted_by": None}, synchronize_session=False)

        # ── Notifications ────────────────────────────────────────────────
        PushSubscription.query.filter_by(user_id=uid).delete(synchronize_session=False)
        NotificationLog.query.filter_by(user_id=uid).delete(synchronize_session=False)
        InAppNotification.query.filter_by(user_id=uid).delete(synchronize_session=False)

        db.session.delete(user)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Could not delete user: {e}"}), 500

    return jsonify({"success": True, "message": f"User '{name}' and all related records deleted."})


# ── Audit Log API ─────────────────────────────────────────────────────────────

@admin_bp.route("/api/logs")
@login_required
@admin_required
def api_logs():
    """Return recent audit events assembled from DB state."""
    events = []

    # Recent logins
    recent_logins = (User.query
                     .filter(User.last_login.isnot(None))
                     .order_by(User.last_login.desc())
                     .limit(5).all())
    for u in recent_logins:
        events.append({
            "time":    u.last_login.strftime("%H:%M") if u.last_login else "—",
            "type":    "login",
            "message": f"{u.name} logged in as {u.role}",
            "ts":      u.last_login.isoformat() if u.last_login else "",
        })

    # Recent leaves
    recent_leaves = (LeaveRequest.query
                     .order_by(LeaveRequest.created_at.desc())
                     .limit(5).all())
    for l in recent_leaves:
        events.append({
            "time":    l.created_at.strftime("%H:%M"),
            "type":    "leave",
            "message": f"Leave #{l.id} submitted — {l.days} days {l.leave_type}",
            "ts":      l.created_at.isoformat(),
        })

    # Sort by timestamp desc
    events.sort(key=lambda x: x.get("ts", ""), reverse=True)
    return jsonify({"success": True, "logs": events[:20]})


# ── CSV Exports ───────────────────────────────────────────────────────────────

@admin_bp.route("/export/attendance")
@login_required
@admin_required
def export_attendance():
    summaries = AttendanceSummary.query.all()
    output    = io.StringIO()
    writer    = csv.writer(output)
    writer.writerow(["Student ID", "Subject", "Semester", "Section",
                     "Total Classes", "Attended", "Attendance %",
                     "Is Defaulter", "Classes Needed"])
    for s in summaries:
        writer.writerow([s.student_id, s.subject, s.semester, s.section,
                         s.total_classes, s.classes_attended,
                         s.attendance_pct, s.is_defaulter, s.required_classes])
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode()),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"attendance_export_{datetime.now().strftime('%Y%m%d')}.csv"
    )


@admin_bp.route("/export/defaulters")
@login_required
@admin_required
def export_defaulters():
    defaulters = AttendanceSummary.query.filter_by(is_defaulter=True).all()
    output     = io.StringIO()
    writer     = csv.writer(output)
    writer.writerow(["Student ID", "Subject", "Semester", "Attendance %", "Classes Needed"])
    for s in defaulters:
        writer.writerow([s.student_id, s.subject, s.semester,
                         s.attendance_pct, s.required_classes])
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode()),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"defaulters_{datetime.now().strftime('%Y%m%d')}.csv"
    )


@admin_bp.route("/export/db")
@login_required
@admin_required
def export_db():
    """Export a JSON snapshot of all key data — works with MySQL and SQLite."""
    import json
    snapshot = {
        "exported_at": datetime.now().isoformat(),
        "users": [u.to_dict() for u in User.query.all()],
        "attendance_summary": [
            {"student_id": s.student_id, "subject": s.subject,
             "semester": s.semester, "section": s.section,
             "attendance_pct": s.attendance_pct, "is_defaulter": s.is_defaulter}
            for s in AttendanceSummary.query.all()
        ],
        "leave_requests": [
            {"id": l.id, "student_id": l.student_id, "days": l.days,
             "status": l.status, "created_at": l.created_at.isoformat()}
            for l in LeaveRequest.query.all()
        ],
        "placement_profiles": [p.to_dict() for p in PlacementProfile.query.all()],
        "placement_drives": [d.to_dict() for d in PlacementDrive.query.all()],
    }
    json_bytes = json.dumps(snapshot, indent=2, default=str).encode("utf-8")
    return send_file(
        io.BytesIO(json_bytes),
        mimetype="application/json",
        as_attachment=True,
        download_name=f"edufusion_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    )


# ── PDF Reports ───────────────────────────────────────────────────────────────

@admin_bp.route("/report/<string:report_type>")
@login_required
@admin_required
def generate_report(report_type):
    """Generate PDF report for the given type."""
    from utils.pdf_generator import generate_pdf_report
    try:
        pdf_bytes = generate_pdf_report(report_type)
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"{report_type}_report_{datetime.now().strftime('%Y%m%d')}.pdf"
        )
    except Exception as e:
        flash(f"Report generation failed: {str(e)}", "danger")
        return redirect(url_for("admin.dashboard"))


# ── Mentor Management ──────────────────────────────────────────────────────────

@admin_bp.route("/mentors")
@login_required
def mentors_page():
    """Mentor assignment admin page."""
    if not current_user.is_admin:
        return redirect(url_for("admin.dashboard"))
    return render_template("admin/mentors.html")


@admin_bp.route("/api/mentors", methods=["GET"])
@login_required
def api_mentors():
    """Return all faculty/HOD mentors with their mentee counts."""
    if not current_user.is_admin:
        return jsonify({"success": False}), 403

    # All possible mentors (faculty + hod)
    mentors = User.query.filter(
        User.role.in_(["faculty", "hod"]),
        User.is_active == True
    ).order_by(User.name).all()

    # All students with their mentor info
    students = User.query.filter_by(role="student", is_active=True)\
                         .order_by(User.semester, User.section, User.name).all()

    mentor_list = []
    for m in mentors:
        mentees = [s for s in students if s.mentor_id == m.id]
        mentor_list.append({
            "id":          m.id,
            "name":        m.name,
            "role":        m.role,
            "designation": m.designation or "",
            "mentee_count": len(mentees),
            "mentees": [{"id": s.id, "name": s.name,
                         "roll_number": s.roll_number or s.username,
                         "semester": s.semester, "section": s.section}
                        for s in mentees],
        })

    unassigned = [{"id": s.id, "name": s.name,
                   "roll_number": s.roll_number or s.username,
                   "semester": s.semester, "section": s.section or ""}
                  for s in students if not s.mentor_id]

    return jsonify({
        "success":    True,
        "mentors":    mentor_list,
        "unassigned": unassigned,
        "total_students": len(students),
        "assigned_count": len(students) - len(unassigned),
    })


@admin_bp.route("/api/mentors/assign", methods=["POST"])
@login_required
def api_assign_mentor():
    """
    Assign a mentor to students.
    Body options:
      { mentor_id, student_ids: [1,2,3] }          ← individual
      { mentor_id, semester: 4, section: "A" }      ← batch by section
    """
    if not current_user.is_admin:
        return jsonify({"success": False}), 403

    data       = request.get_json() or {}
    mentor_id  = data.get("mentor_id")
    student_ids = data.get("student_ids", [])
    semester   = data.get("semester")
    section    = data.get("section")

    if not mentor_id:
        return jsonify({"success": False, "message": "mentor_id required"}), 400

    mentor = db.session.get(User, mentor_id)
    if not mentor or mentor.role not in ("faculty", "hod"):
        return jsonify({"success": False, "message": "Invalid mentor"}), 400

    # Batch by section
    if not student_ids and (semester or section):
        q = User.query.filter_by(role="student", is_active=True)
        if semester:  q = q.filter_by(semester=int(semester))
        if section:   q = q.filter_by(section=section.upper())
        student_ids = [s.id for s in q.all()]

    if not student_ids:
        return jsonify({"success": False, "message": "No students selected"}), 400

    updated = 0
    for sid in student_ids:
        s = db.session.get(User, sid)
        if s and s.is_student:
            s.mentor_id = mentor_id
            updated += 1

    db.session.commit()
    return jsonify({
        "success": True,
        "message": f"Assigned {mentor.name} as mentor to {updated} student(s).",
        "updated": updated,
    })


@admin_bp.route("/api/mentors/unassign", methods=["POST"])
@login_required
def api_unassign_mentor():
    """Remove mentor assignment from one or more students."""
    if not current_user.is_admin:
        return jsonify({"success": False}), 403

    data        = request.get_json() or {}
    student_ids = data.get("student_ids", [])

    updated = 0
    for sid in student_ids:
        s = db.session.get(User, sid)
        if s and s.is_student:
            s.mentor_id = None
            updated += 1

    db.session.commit()
    return jsonify({"success": True, "message": f"Unassigned {updated} student(s).", "updated": updated})


# ── Impersonation ─────────────────────────────────────────────────────────────

@admin_bp.route("/impersonate/<int:user_id>")
@login_required
def impersonate(user_id):
    """Admin previews the app as any user. Stores real admin ID in session."""
    if not current_user.is_admin:
        flash("Admin only.", "danger")
        return redirect(url_for("admin.dashboard"))

    target = db.session.get(User, user_id)
    if not target:
        flash("User not found.", "danger")
        return redirect(url_for("admin.users"))

    from flask_login import login_user
    # Store the real admin's id so we can return later
    session["_impersonator_id"] = current_user.id
    session["_impersonating_name"] = target.name
    session["_impersonating_role"] = target.role
    login_user(target, remember=False)
    flash(f"You are now previewing as {target.name} ({target.role}). "
          f"Click 'Exit Preview' in the banner to return.", "info")

    # Redirect to the right home for that role
    if target.is_admin:
        return redirect(url_for("admin.dashboard"))
    return redirect(url_for("dashboard.index"))


@admin_bp.route("/exit-impersonation")
@login_required
def exit_impersonation():
    """Return to the original admin account."""
    admin_id = session.pop("_impersonator_id", None)
    session.pop("_impersonating_name", None)
    session.pop("_impersonating_role", None)

    if not admin_id:
        return redirect(url_for("admin.dashboard"))

    admin_user = db.session.get(User, admin_id)
    if not admin_user or not admin_user.is_admin:
        flash("Could not restore admin session.", "danger")
        return redirect(url_for("auth.login"))

    from flask_login import login_user
    login_user(admin_user, remember=False)
    flash("You have exited preview mode and returned to your admin account.", "success")
    return redirect(url_for("admin.dashboard"))
