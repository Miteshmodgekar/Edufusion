"""Dashboard Blueprint - Role-based dashboard data."""

import pandas as pd
from flask import Blueprint, jsonify, render_template, current_app
from flask_login import login_required, current_user
from models.attendance import AttendanceSummary
from models.leave import LeaveRequest
from models.placement import PlacementProfile, Project, StudentPerformance
from models.user import User
from config import Config
from utils.live_attendance import read_live_attendance_for_student, get_live_attendance_path

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@dashboard_bp.route("/index")
@login_required
def index():
    from flask import redirect, url_for
    if current_user.role == "admin":
        return redirect(url_for("admin.dashboard"))
    return render_template("dashboard/index.html", user=current_user)


@dashboard_bp.route("/api/data")
@login_required
def api_data():
    role = current_user.role
    if role == "student":  return _student_data()
    if role in ("faculty", "hod"): return _faculty_data()
    if role == "admin":    return _admin_data()
    return jsonify({"success": False, "message": "Unknown role"}), 400


def _student_data():
    sid  = current_user.id
    live_path = get_live_attendance_path(current_app.config)
    live = read_live_attendance_for_student(
        live_path, current_user, threshold=Config.ATTENDANCE_THRESHOLD
    )
    if live["success"] and live["subjects"]:
        avg_att        = live["overall"]
        att_subjects   = live["subjects"]
        defaulter_count = sum(1 for s in att_subjects if s["is_defaulter"])
    else:
        summaries      = AttendanceSummary.query.filter_by(student_id=sid).all()
        avg_att        = (round(sum(s.attendance_pct for s in summaries) / len(summaries), 2)
                           if summaries else 0)
        att_subjects   = [s.to_dict() for s in summaries]
        defaulter_count = sum(1 for s in summaries if s.is_defaulter)

    leaves    = LeaveRequest.query.filter_by(student_id=sid).all()
    profile   = PlacementProfile.query.filter_by(student_id=sid).first()
    projects  = Project.query.filter_by(student_id=sid).all()
    perfs     = StudentPerformance.query.filter_by(student_id=sid).all()

    return jsonify({
        "success": True, "role": "student", "name": current_user.name,
        "attendance": {
            "overall":         avg_att,
            "subjects":        att_subjects,
            "defaulter_count": defaulter_count,
        },
        "leaves": {
            "total":    len(leaves),
            "pending":  sum(1 for l in leaves if l.status in ("pending", "mentor_approved")),
            "approved": sum(1 for l in leaves if l.status == "approved"),
        },
        "placement":    profile.to_dict() if profile else None,
        "projects":     [p.to_dict() for p in projects],
        "performance":  [p.to_dict() for p in perfs],
    })


def _faculty_data():
    dept      = current_user.department or "CSE"
    students  = User.query.filter_by(role="student", department=dept).all()
    pending   = LeaveRequest.query.filter_by(
        mentor_id=current_user.id, mentor_status="pending").count()

    from utils.live_attendance import get_live_defaulters, _parse_subject_sheet
    live_path = get_live_attendance_path(current_app.config)
    live_defaulters, live_available = get_live_defaulters(
        live_path, threshold=Config.ATTENDANCE_THRESHOLD, department=dept
    )

    if live_available:
        defaulter_count = len(set(d["roll_number"] for d in live_defaulters))
        all_pcts = []
        try:
            xl = pd.ExcelFile(live_path)
            for sname in xl.sheet_names:
                rows = _parse_subject_sheet(xl, sname, Config.ATTENDANCE_THRESHOLD) or []
                all_pcts.extend(r["attendance_pct"] for r in rows)
        except Exception:
            pass
        avg_att = round(sum(all_pcts) / len(all_pcts), 2) if all_pcts else 0
    else:
        defaulters = AttendanceSummary.query.filter_by(is_defaulter=True).all()
        defaulter_count = len(defaulters)
        all_sum = AttendanceSummary.query.all()
        avg_att = (round(sum(s.attendance_pct for s in all_sum) / len(all_sum), 2)
                   if all_sum else 0)

    return jsonify({
        "success": True, "role": current_user.role, "name": current_user.name,
        "total_students": len(students),
        "defaulters":     defaulter_count,
        "pending_leaves": pending,
        "avg_attendance": avg_att,
    })


def _admin_data():
    from models.attendance import AttendanceSummary
    total_students = User.query.filter_by(role="student").count()
    total_faculty  = User.query.filter_by(role="faculty").count()
    total_leaves   = LeaveRequest.query.count()
    pending_leaves = LeaveRequest.query.filter_by(status="pending").count() + \
                     LeaveRequest.query.filter_by(status="mentor_approved").count()
    defaulters     = AttendanceSummary.query.filter_by(is_defaulter=True).count()
    all_sum        = AttendanceSummary.query.all()
    avg_att        = (round(sum(s.attendance_pct for s in all_sum) / len(all_sum), 1)
                      if all_sum else 0)

    return jsonify({
        "success":        True,
        "role":           "admin",
        "name":           current_user.name,
        # Fields used by dashboard stats cards
        "avg_attendance": avg_att,
        "pending_leaves": pending_leaves,
        "defaulters":     defaulters,
        "total_students": total_students,
        # Admin-only extras
        "total_users":    User.query.count(),
        "total_faculty":  total_faculty,
        "total_leaves":   total_leaves,
    })
