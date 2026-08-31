"""
Placement Drives Blueprint — AI-powered eligibility matching.

Flow:
  1. Admin posts a drive with requirements (CGPA, skills, backlogs, etc.)
  2. AI engine checks every student's PlacementProfile against the requirements.
  3. Eligible students receive an InAppNotification.
  4. Students view drives list → see eligibility badge → can apply.
"""

from flask import Blueprint, request, jsonify, render_template
from flask_login import login_required, current_user
from extensions import db
from models.drive import PlacementDrive, DriveApplication
from models.placement import PlacementProfile
from models.attendance import AttendanceSummary
from models.notification import InAppNotification
from models.user import User
from datetime import datetime
from config import Config
from utils.live_attendance import get_avg_attendance_for_student, get_live_attendance_path
from flask import current_app

drives_bp = Blueprint("drives", __name__)


# ── AI Matching Engine ────────────────────────────────────────────────────────

def _compute_skill_match(student_skills_str, required_skills_list):
    """
    Returns (matched_count, total_required, match_pct, matched_skills, missing_skills).
    Uses case-insensitive substring matching so 'Python' matches 'python3'.
    """
    if not required_skills_list:
        return 0, 0, 100.0, [], []

    student_skills = [s.strip().lower() for s in (student_skills_str or "").split(",") if s.strip()]
    matched, missing = [], []

    for req in required_skills_list:
        req_lower = req.lower()
        found = any(req_lower in sk or sk in req_lower for sk in student_skills)
        (matched if found else missing).append(req)

    total = len(required_skills_list)
    pct   = round((len(matched) / total) * 100, 1) if total else 100.0
    return len(matched), total, pct, matched, missing


def _ai_match_students(drive, notify=True):
    """
    Match all student PlacementProfiles against the drive requirements.
    Returns a result dict with per-student breakdown.
    """
    students = User.query.filter_by(role="student", is_active=True).all()
    results  = []
    eligible_count = 0
    notified_count = 0

    required_skills = drive.skills_list   # list of lowercase strings

    for student in students:
        profile = PlacementProfile.query.filter_by(student_id=student.id).first()
        if not profile:
            results.append({
                "student_id":  student.id,
                "student":     student.name,
                "eligible":    False,
                "reason":      "No placement profile found",
                "match_score": 0,
            })
            continue

        # ── Average attendance (live Excel, DB fallback) ──────────────────
        avg_att, _att_src = get_avg_attendance_for_student(
            get_live_attendance_path(current_app.config), student,
            threshold=Config.ATTENDANCE_THRESHOLD
        )

        cgpa      = profile.cgpa or 0.0
        backlogs  = profile.backlogs or 0

        # ── Check hard criteria ───────────────────────────────────────────
        fail_reasons = []
        if cgpa < drive.min_cgpa:
            fail_reasons.append(f"CGPA {cgpa} < required {drive.min_cgpa}")
        if backlogs > drive.max_backlogs:
            fail_reasons.append(f"Backlogs {backlogs} > allowed {drive.max_backlogs}")

        # ── Branch filter (optional) ──────────────────────────────────────
        if drive.eligible_branches and drive.eligible_branches.strip():
            allowed = [b.strip().upper() for b in drive.eligible_branches.split(",")]
            dept    = (student.department or "").upper()
            if dept not in allowed:
                fail_reasons.append(f"Branch {dept} not in {drive.eligible_branches}")

        # ── Skill match ───────────────────────────────────────────────────
        matched_n, total_n, skill_pct, matched, missing = _compute_skill_match(
            profile.skills, required_skills)

        # ── Compute AI match score (0–100) ────────────────────────────────
        cgpa_score  = min(cgpa / 10.0, 1.0) * 50
        skill_score = (skill_pct / 100.0) * 50
        match_score = round(cgpa_score + skill_score, 1)

        is_eligible = len(fail_reasons) == 0

        entry = {
            "student_id":     student.id,
            "student":        student.name,
            "roll":           student.roll_number,
            "cgpa":           cgpa,
            "backlogs":       backlogs,
            "attendance":     round(avg_att, 1),
            "skill_match_pct": skill_pct,
            "matched_skills": matched,
            "missing_skills": missing,
            "match_score":    match_score,
            "eligible":       is_eligible,
            "reason":         "; ".join(fail_reasons) if fail_reasons else "All criteria met",
        }
        results.append(entry)

        if is_eligible:
            eligible_count += 1

            if notify:
                # Check if notification already sent for this drive+student
                already = InAppNotification.query.filter_by(
                    user_id    = student.id,
                    notif_type = "placement",
                    ref_id     = drive.id,
                ).first()

                if not already:
                    skills_preview = ", ".join(required_skills[:3]) + \
                                     ("..." if len(required_skills) > 3 else "")
                    body = (
                        f"{drive.company_name} is hiring {drive.role}. "
                        f"Package: {drive.package_lpa or '?'} LPA. "
                        f"Your match score: {match_score}/100. "
                        f"Skills needed: {skills_preview or 'None specified'}. "
                        f"{'Deadline: ' + drive.deadline.strftime('%d %b %Y') if drive.deadline else ''}"
                    )
                    notif = InAppNotification(
                        user_id    = student.id,
                        title      = f"You are eligible! {drive.company_name} — {drive.role}",
                        body       = body,
                        icon       = "bi-building",
                        color      = "#3fb950",
                        notif_type = "placement",
                        ref_id     = drive.id,
                        ref_url    = f"/drives/",
                    )
                    db.session.add(notif)
                    notified_count += 1
                    try:
                        from utils.notifications import send_email
                        send_email(
                            student.email,
                            f"You're eligible: {drive.company_name} — {drive.role}",
                            f"""<div style="font-family:Segoe UI,Arial,sans-serif;max-width:600px;margin:0 auto;
                                     background:#0d1117;color:#e6edf3;border-radius:12px;overflow:hidden">
                                  <div style="background:#3fb950;padding:24px 32px">
                                    <h2 style="margin:0;color:#0d1117;font-size:20px">New Placement Match</h2>
                                  </div>
                                  <div style="padding:28px 32px">
                                    <p style="font-size:15px;margin-bottom:16px">Dear <strong>{student.name}</strong>,</p>
                                    <p style="font-size:14px;color:#8b949e">{body}</p>
                                  </div>
                                </div>"""
                        )
                    except Exception:
                        pass

    db.session.commit()

    # Update drive stats
    drive.eligible_count     = eligible_count
    drive.notifications_sent = (drive.notifications_sent or 0) + notified_count
    db.session.commit()

    return {
        "eligible":  eligible_count,
        "notified":  notified_count,
        "total":     len(results),
        "details":   results,
    }


# ── Student: List drives ──────────────────────────────────────────────────────

@drives_bp.route("/", methods=["GET"])
@login_required
def list_drives():
    return render_template("drives/list.html")


@drives_bp.route("/api/list", methods=["GET"])
@login_required
def api_list_drives():
    """Return open drives. For students, also include eligibility flag."""
    drives = (PlacementDrive.query
              .filter_by(status="open")
              .order_by(PlacementDrive.created_at.desc())
              .all())

    result = []
    for d in drives:
        obj = d.to_dict()

        # Eligibility check for students
        if current_user.is_student:
            profile = PlacementProfile.query.filter_by(student_id=current_user.id).first()
            avg_att, _att_src = get_avg_attendance_for_student(
                get_live_attendance_path(current_app.config), current_user,
                threshold=Config.ATTENDANCE_THRESHOLD
            )

            if profile:
                _, _, skill_pct, matched, missing = _compute_skill_match(
                    profile.skills, d.skills_list)
                cgpa     = profile.cgpa or 0.0
                backlogs = profile.backlogs or 0

                fail_reasons = []
                if cgpa < d.min_cgpa:
                    fail_reasons.append(f"CGPA {cgpa} < required {d.min_cgpa}")
                if backlogs > d.max_backlogs:
                    fail_reasons.append(f"Backlogs {backlogs} > allowed {d.max_backlogs}")
                if d.eligible_branches and d.eligible_branches.strip():
                    allowed = [b.strip().upper() for b in d.eligible_branches.split(",")]
                    if (current_user.department or "").upper() not in allowed:
                        fail_reasons.append(f"Branch not in {d.eligible_branches}")

                obj["my_cgpa"]         = cgpa
                obj["my_skill_pct"]    = skill_pct
                obj["matched_skills"]  = matched
                obj["missing_skills"]  = missing
                obj["my_fail_reasons"] = fail_reasons
                obj["my_eligible"]     = len(fail_reasons) == 0
                cgpa_s  = min(cgpa / 10.0, 1.0) * 50
                skill_s = (skill_pct / 100.0) * 50
                obj["my_match_score"] = round(cgpa_s + skill_s, 1)
            else:
                obj["my_eligible"]    = False
                obj["my_match_score"] = 0
                obj["my_cgpa"]        = 0
                obj["my_skill_pct"]   = 0
                obj["matched_skills"] = []
                obj["missing_skills"] = d.skills_list
                obj["my_fail_reasons"] = ["No placement profile filled in yet"]

            # Check if already applied
            applied = DriveApplication.query.filter_by(
                drive_id=d.id, student_id=current_user.id).first()
            obj["already_applied"] = applied is not None
            obj["application_status"] = applied.status if applied else None

        obj["applicant_count"] = DriveApplication.query.filter_by(drive_id=d.id).count()
        result.append(obj)

    return jsonify({"success": True, "drives": result})


# ── Student: Apply ────────────────────────────────────────────────────────────

@drives_bp.route("/api/apply/<int:drive_id>", methods=["POST"])
@login_required
def apply(drive_id):
    if not current_user.is_student:
        return jsonify({"success": False, "message": "Students only."}), 403

    drive = PlacementDrive.query.get_or_404(drive_id)
    if drive.status != "open":
        return jsonify({"success": False, "message": "This drive is closed."}), 400

    # Check for existing application
    existing = DriveApplication.query.filter_by(
        drive_id=drive_id, student_id=current_user.id).first()
    if existing:
        return jsonify({"success": False, "message": "You have already applied."}), 400

    # Compute match score
    profile   = PlacementProfile.query.filter_by(student_id=current_user.id).first()
    avg_att, _att_src = get_avg_attendance_for_student(
        get_live_attendance_path(current_app.config), current_user,
        threshold=Config.ATTENDANCE_THRESHOLD
    )

    match_score = 0.0
    if profile:
        _, _, skill_pct, _, _ = _compute_skill_match(profile.skills, drive.skills_list)
        cgpa_s      = min((profile.cgpa or 0) / 10.0, 1.0) * 50
        skill_s     = (skill_pct / 100.0) * 50
        match_score = round(cgpa_s + skill_s, 1)

    app = DriveApplication(
        drive_id    = drive_id,
        student_id  = current_user.id,
        match_score = match_score,
        status      = "applied",
    )
    db.session.add(app)
    db.session.commit()

    return jsonify({
        "success":     True,
        "message":     f"Application submitted to {drive.company_name}!",
        "match_score": match_score,
    }), 201


# ── Student: My applications ──────────────────────────────────────────────────

@drives_bp.route("/api/my", methods=["GET"])
@login_required
def my_applications():
    if not current_user.is_student:
        return jsonify({"success": False, "message": "Students only."}), 403
    apps = (DriveApplication.query
            .filter_by(student_id=current_user.id)
            .order_by(DriveApplication.applied_at.desc())
            .all())
    result = []
    for a in apps:
        d = a.drive.to_dict()
        d["application_status"] = a.status
        d["match_score"]        = a.match_score
        d["applied_at"]         = a.applied_at.isoformat()
        result.append(d)
    return jsonify({"success": True, "applications": result})


# ── Admin/HOD: Post a new drive ───────────────────────────────────────────────

@drives_bp.route("/post", methods=["GET"])
@login_required
def post_page():
    if not (current_user.is_admin or current_user.is_hod or current_user.is_faculty):
        return jsonify({"success": False, "message": "Access denied."}), 403
    return render_template("drives/post.html")


@drives_bp.route("/api/post", methods=["POST"])
@login_required
def api_post_drive():
    if not (current_user.is_admin or current_user.is_hod or current_user.is_faculty):
        return jsonify({"success": False, "message": "Access denied."}), 403

    data = request.get_json() or {}

    deadline = None
    if data.get("deadline"):
        try:
            deadline = datetime.strptime(data["deadline"], "%Y-%m-%d").date()
        except ValueError:
            pass

    drive = PlacementDrive(
        company_name      = data.get("company_name", "").strip(),
        role              = data.get("role", "").strip(),
        package_lpa       = float(data["package_lpa"]) if data.get("package_lpa") else None,
        location          = data.get("location", "").strip(),
        description       = data.get("description", "").strip(),
        deadline          = deadline,
        min_cgpa          = float(data.get("min_cgpa", 6.0)),
        required_skills   = data.get("required_skills", "").strip(),
        max_backlogs      = int(data.get("max_backlogs", 0)),
        min_attendance    = 0.0,  # attendance no longer gates eligibility
        eligible_branches = data.get("eligible_branches", "").strip(),
        posted_by         = current_user.id,
        status            = "open",
    )

    if not drive.company_name or not drive.role:
        return jsonify({"success": False, "message": "Company name and role are required."}), 400

    db.session.add(drive)
    db.session.commit()

    # Run AI matching immediately
    match_result = _ai_match_students(drive, notify=True)

    return jsonify({
        "success":      True,
        "message":      f"Drive posted! {match_result['eligible']} eligible students notified.",
        "drive":        drive.to_dict(),
        "match_result": match_result,
    }), 201


# ── Admin: All drives ─────────────────────────────────────────────────────────

@drives_bp.route("/api/all", methods=["GET"])
@login_required
def api_all_drives():
    if not (current_user.is_admin or current_user.is_hod or current_user.is_faculty):
        return jsonify({"success": False, "message": "Access denied."}), 403
    drives = (PlacementDrive.query
              .order_by(PlacementDrive.created_at.desc())
              .all())
    result = []
    for d in drives:
        obj = d.to_dict()
        obj["applicant_count"] = DriveApplication.query.filter_by(drive_id=d.id).count()
        result.append(obj)
    return jsonify({"success": True, "drives": result})


# ── Admin: Re-run AI matching ─────────────────────────────────────────────────

@drives_bp.route("/api/notify/<int:drive_id>", methods=["POST"])
@login_required
def re_notify(drive_id):
    if not (current_user.is_admin or current_user.is_hod or current_user.is_faculty):
        return jsonify({"success": False, "message": "Access denied."}), 403
    drive  = PlacementDrive.query.get_or_404(drive_id)
    result = _ai_match_students(drive, notify=True)
    return jsonify({"success": True, "result": result})


# ── Admin: Close/reopen drive ─────────────────────────────────────────────────

@drives_bp.route("/api/toggle/<int:drive_id>", methods=["POST"])
@login_required
def toggle_drive(drive_id):
    if not (current_user.is_admin or current_user.is_hod):
        return jsonify({"success": False, "message": "Access denied."}), 403
    drive = PlacementDrive.query.get_or_404(drive_id)
    drive.status = "closed" if drive.status == "open" else "open"
    db.session.commit()
    return jsonify({"success": True, "status": drive.status})


# ── Admin: Applicants for a drive ─────────────────────────────────────────────

@drives_bp.route("/<int:drive_id>/applicants", methods=["GET"])
@login_required
def applicants_page(drive_id):
    if not (current_user.is_admin or current_user.is_hod or current_user.is_faculty):
        return jsonify({"success": False, "message": "Access denied."}), 403
    return render_template("drives/applicants.html", drive_id=drive_id)


@drives_bp.route("/api/<int:drive_id>/applicants", methods=["GET"])
@login_required
def api_applicants(drive_id):
    if not (current_user.is_admin or current_user.is_hod or current_user.is_faculty):
        return jsonify({"success": False, "message": "Access denied."}), 403
    drive = PlacementDrive.query.get_or_404(drive_id)
    apps  = (DriveApplication.query
             .filter_by(drive_id=drive_id)
             .order_by(DriveApplication.match_score.desc())
             .all())
    return jsonify({
        "success":      True,
        "drive":        drive.to_dict(),
        "applicants":   [a.to_dict() for a in apps],
        "total":        len(apps),
    })


# ── Admin: Update applicant status (shortlist/select/reject) ──────────────────

@drives_bp.route("/api/application/<int:app_id>/status", methods=["PUT"])
@login_required
def update_application_status(app_id):
    if not (current_user.is_admin or current_user.is_hod or current_user.is_faculty):
        return jsonify({"success": False, "message": "Access denied."}), 403
    app_obj = DriveApplication.query.get_or_404(app_id)
    data    = request.get_json() or {}
    new_status = data.get("status", "")
    if new_status not in ("applied", "shortlisted", "selected", "rejected"):
        return jsonify({"success": False, "message": "Invalid status."}), 400

    app_obj.status = new_status
    db.session.commit()

    # Notify student of status change
    drive = PlacementDrive.query.get(app_obj.drive_id)
    if drive:
        icons = {"shortlisted": "bi-star", "selected": "bi-trophy", "rejected": "bi-x-circle"}
        colors = {"shortlisted": "#d2991c", "selected": "#3fb950",   "rejected": "#f85149"}
        notif = InAppNotification(
            user_id    = app_obj.student_id,
            title      = f"{drive.company_name} — Your application was {new_status}!",
            body       = f"Your application for {drive.role} at {drive.company_name} has been {new_status}.",
            icon       = icons.get(new_status, "bi-building"),
            color      = colors.get(new_status, "#58a6ff"),
            notif_type = "placement",
            ref_id     = drive.id,
            ref_url    = "/drives/",
        )
        db.session.add(notif)
        db.session.commit()

    return jsonify({"success": True, "status": new_status})
