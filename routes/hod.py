"""
HOD Blueprint — Faculty Management & Subject Assignment.
"""
from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required, current_user
from extensions import db
from models.user import User
from models.attendance import SheetRegistry
from models.faculty_subject import FacultySubjectAssignment
from models.notification import InAppNotification
from datetime import datetime, timedelta, timezone

hod_bp = Blueprint("hod", __name__)

_IST = timezone(timedelta(hours=5, minutes=30))

def _ist_now():
    return datetime.now(_IST).replace(tzinfo=None)

def hod_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_hod:
            return jsonify({"success": False, "message": "HOD only."}), 403
        return f(*args, **kwargs)
    return decorated


# ── Pages ─────────────────────────────────────────────────────────────────────

@hod_bp.route("/faculty")
@login_required
@hod_required
def faculty_page():
    return render_template("hod/faculty.html")


# ── API: Faculty Overview ─────────────────────────────────────────────────────

@hod_bp.route("/api/faculty")
@login_required
@hod_required
def api_faculty():
    dept  = current_user.department
    now   = _ist_now()
    ago30 = now - timedelta(days=30)

    faculty_list = User.query.filter_by(
        role="faculty", department=dept, is_active=True
    ).order_by(User.name).all()

    results = []
    for f in faculty_list:
        sheets_all = SheetRegistry.query.filter_by(uploaded_by=f.id).all()
        sheets_30  = [s for s in sheets_all if s.uploaded_at and s.uploaded_at >= ago30]
        subjects_uploaded = list({s.subject for s in sheets_all if s.subject})
        last_upload = max(
            (s.uploaded_at for s in sheets_all if s.uploaded_at), default=None
        )

        # Assigned subjects by HOD
        assignments = FacultySubjectAssignment.query.filter_by(faculty_id=f.id).all()
        assigned_subjects = [
            {"id": a.id, "subject": a.subject, "semester": a.semester, "section": a.section or "All"}
            for a in assignments
        ]

        days_since_login = None
        if f.last_login:
            days_since_login = (now - f.last_login).days

        if days_since_login is None:
            status = "new"
        elif days_since_login <= 3:
            status = "active"
        elif days_since_login <= 14:
            status = "idle"
        else:
            status = "inactive"

        results.append({
            "id":                f.id,
            "name":              f.name,
            "email":             f.email,
            "designation":       f.designation or "Faculty",
            "last_login":        f.last_login.strftime("%d %b %Y, %I:%M %p") if f.last_login else None,
            "days_since_login":  days_since_login,
            "status":            status,
            "sheets_total":      len(sheets_all),
            "sheets_30d":        len(sheets_30),
            "subjects_uploaded": subjects_uploaded,
            "assigned_subjects": assigned_subjects,
            "last_upload":       last_upload.strftime("%d %b %Y") if last_upload else None,
        })

    total_faculty  = len(results)
    active_count   = sum(1 for r in results if r["status"] == "active")
    idle_count     = sum(1 for r in results if r["status"] == "idle")
    inactive_count = sum(1 for r in results if r["status"] == "inactive")
    total_uploads  = sum(r["sheets_total"] for r in results)
    all_subjects   = sorted({s for r in results for s in r["subjects_uploaded"]})

    return jsonify({
        "success":      True,
        "faculty":      results,
        "stats": {
            "total":    total_faculty,
            "active":   active_count,
            "idle":     idle_count,
            "inactive": inactive_count,
            "uploads":  total_uploads,
            "subjects": len(all_subjects),
            "dept":     dept or "—",
        },
        "all_subjects": all_subjects,
    })


# ── API: Assign Subject ───────────────────────────────────────────────────────

@hod_bp.route("/api/assign", methods=["POST"])
@login_required
@hod_required
def api_assign():
    data       = request.get_json() or {}
    faculty_id = data.get("faculty_id")
    subject    = (data.get("subject") or "").strip()
    semester   = data.get("semester")
    section    = (data.get("section") or "").strip() or None

    if not faculty_id or not subject or not semester:
        return jsonify({"success": False, "message": "faculty_id, subject, and semester are required."}), 400

    # Verify faculty belongs to HOD's department
    faculty = User.query.filter_by(id=faculty_id, role="faculty",
                                   department=current_user.department).first()
    if not faculty:
        return jsonify({"success": False, "message": "Faculty not found in your department."}), 404

    # Check duplicate
    existing = FacultySubjectAssignment.query.filter_by(
        faculty_id=faculty_id, subject=subject,
        semester=int(semester), section=section
    ).first()
    if existing:
        return jsonify({"success": False, "message": f"{subject} already assigned to {faculty.name} for this semester/section."}), 400

    assignment = FacultySubjectAssignment(
        faculty_id  = faculty_id,
        subject     = subject,
        semester    = int(semester),
        section     = section,
        department  = current_user.department,
        assigned_by = current_user.id,
    )
    db.session.add(assignment)
    db.session.commit()

    # ── Notify faculty via bell notification ──────────────────────────────
    section_label = f" (Section {section})" if section else ""
    notif = InAppNotification(
        user_id    = faculty_id,
        title      = f"New Subject Assigned: {subject}",
        body       = f"{current_user.name} (HOD) assigned you '{subject}' for Semester {semester}{section_label}. Please upload attendance sheets for this subject.",
        icon       = "bi-journal-bookmark-fill",
        color      = "#3fb950",
        notif_type = "general",
        ref_url    = "/hod/my-subjects",
    )
    db.session.add(notif)
    db.session.commit()

    return jsonify({
        "success": True,
        "message": f"{subject} assigned to {faculty.name}. Faculty notified.",
        "assignment": assignment.to_dict(),
    }), 201


# ── API: Remove Assignment ────────────────────────────────────────────────────

@hod_bp.route("/api/assign/<int:assignment_id>", methods=["DELETE"])
@login_required
@hod_required
def api_unassign(assignment_id):
    a = FacultySubjectAssignment.query.get_or_404(assignment_id)

    # Verify it belongs to HOD's department
    if a.department != current_user.department:
        return jsonify({"success": False, "message": "Access denied."}), 403

    faculty  = db.session.get(User, a.faculty_id)
    subj     = a.subject
    sem      = a.semester
    db.session.delete(a)
    db.session.commit()

    # Notify faculty of removal
    if faculty:
        notif = InAppNotification(
            user_id    = faculty.id,
            title      = f"Subject Assignment Removed: {subj}",
            body       = f"{current_user.name} (HOD) removed your assignment for '{subj}' (Semester {sem}).",
            icon       = "bi-journal-x",
            color      = "#f85149",
            notif_type = "general",
            ref_url    = "/hod/my-subjects",
        )
        db.session.add(notif)
        db.session.commit()

    return jsonify({"success": True, "message": "Assignment removed. Faculty notified."})


# ── API: All assignments for HOD's dept ───────────────────────────────────────

@hod_bp.route("/api/assignments")
@login_required
@hod_required
def api_assignments():
    assignments = FacultySubjectAssignment.query.filter_by(
        department=current_user.department
    ).order_by(FacultySubjectAssignment.semester, FacultySubjectAssignment.subject).all()
    return jsonify({
        "success":     True,
        "assignments": [a.to_dict() for a in assignments],
    })


# ── Faculty: View My Assigned Subjects ────────────────────────────────────────

@hod_bp.route("/my-subjects")
@login_required
def my_subjects_page():
    """Faculty/HOD view of their own subject assignments."""
    if not (current_user.is_faculty or current_user.is_hod):
        return jsonify({"success": False, "message": "Faculty only."}), 403
    return render_template("hod/my_subjects.html")


@hod_bp.route("/api/my-subjects")
@login_required
def api_my_subjects():
    """Return current faculty's assigned subjects."""
    if not (current_user.is_faculty or current_user.is_hod):
        return jsonify({"success": False, "message": "Access denied."}), 403
    assignments = FacultySubjectAssignment.query.filter_by(
        faculty_id=current_user.id
    ).order_by(FacultySubjectAssignment.semester, FacultySubjectAssignment.subject).all()
    return jsonify({
        "success":     True,
        "assignments": [a.to_dict() for a in assignments],
        "faculty_name": current_user.name,
    })
