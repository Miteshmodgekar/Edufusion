"""Leave Blueprint — Full multi-level approval flow.

Flow: Student applies → Mentor reviews → HOD approves
      → Attendance adjustment DEFERRED until next day 12 PM IST
      → Scheduler runs at 12:05 PM, by which time all faculty
        must have uploaded attendance (deadline = 12 PM)
      → Only subjects with actual AttendanceRecord on leave date get credit.
"""

from flask import Blueprint, request, jsonify, render_template, send_from_directory, abort
from flask_login import login_required, current_user
from extensions import db
from models.leave import LeaveRequest
from models.attendance import AttendanceRecord, AttendanceSummary
from models.user import User
from datetime import datetime, timedelta, time as dt_time, timezone
from zoneinfo import ZoneInfo
import os, uuid

_IST_TZ = timezone(timedelta(hours=5, minutes=30))
def _ist_now():
    return datetime.now(_IST_TZ).replace(tzinfo=None)

leave_bp = Blueprint("leave", __name__)

# Proof upload folder
PROOF_UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads", "leave_proofs")
os.makedirs(PROOF_UPLOAD_DIR, exist_ok=True)
ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png"}

def _allowed(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Leave requests for "tomorrow" must be submitted before this local time today.
NEXT_DAY_LEAVE_CUTOFF = dt_time(14, 0)   # 2:00 PM
COLLEGE_TZ           = ZoneInfo("Asia/Kolkata")

# Faculty must upload attendance by this time the NEXT day.
# The scheduler runs 5 minutes after this deadline.
ATTENDANCE_DEADLINE   = dt_time(12, 0)   # 12:00 PM noon


# ── Helpers ───────────────────────────────────────────────────────────────────

def _date_range(from_date, to_date):
    """Yield every date from from_date to to_date inclusive."""
    delta = (to_date - from_date).days
    for i in range(delta + 1):
        yield from_date + timedelta(days=i)


# ── Student: Apply ─────────────────────────────────────────────────────────────

@leave_bp.route("/apply", methods=["GET"])
@login_required
def apply_page():
    return render_template("leave/apply.html")


@leave_bp.route("/apply", methods=["POST"])
@login_required
def apply():
    if not current_user.is_student:
        return jsonify({"success": False, "message": "Only students can apply."}), 403

    data = request.get_json() if request.is_json else request.form

    try:
        from_date = datetime.strptime(data["from_date"], "%Y-%m-%d").date()
        to_date   = datetime.strptime(data["to_date"],   "%Y-%m-%d").date()
    except (KeyError, ValueError) as e:
        return jsonify({"success": False, "message": f"Invalid date: {e}"}), 400

    if to_date < from_date:
        return jsonify({"success": False,
                        "message": "End date must be after start date."}), 400

    # ── Next-day cutoff rule ─────────────────────────────────────────────────
    # A leave that starts tomorrow must be submitted before 2:00 PM today
    # (college local time); after the cutoff it's too late for approvals to
    # process in time, so the request is rejected outright.
    now_local = datetime.now(COLLEGE_TZ)
    tomorrow_local = now_local.date() + timedelta(days=1)
    if from_date == tomorrow_local and now_local.time() >= NEXT_DAY_LEAVE_CUTOFF:
        return jsonify({
            "success": False,
            "message": (
                "Too late to apply for tomorrow's leave. Requests for the next "
                "day must be submitted before 2:00 PM today — this one is past "
                "that cutoff and won't be considered. Please contact your "
                "mentor directly if it's urgent."
            ),
        }), 400

    reason = data.get("reason", "").strip()
    if not reason:
        return jsonify({"success": False, "message": "Reason is required."}), 400

    # ── Use assigned mentor, fall back to HOD if none ────────────────────────
    mentor = None
    if current_user.mentor_id:
        mentor = db.session.get(User, current_user.mentor_id)
    if not mentor:
        # No assigned mentor — fall back to HOD (skip mentor step)
        mentor = User.query.filter_by(role="hod").first()

    # ── Find HOD ───────────────────────────────────────────────────────────────
    hod = User.query.filter_by(role="hod").first()

    # If HOD is also the mentor, skip mentor step — go straight to HOD approval
    skip_mentor = mentor and hod and mentor.id == hod.id

    leave = LeaveRequest(
        student_id    = current_user.id,
        from_date     = from_date,
        to_date       = to_date,
        reason        = reason,
        leave_type    = data.get("leave_type", "personal"),
        mentor_id     = mentor.id if mentor else None,
        hod_id        = hod.id    if hod    else None,
        status        = "mentor_approved" if skip_mentor else "pending",
        mentor_status = "approved"        if skip_mentor else "pending",
        hod_status    = "pending",
    )
    db.session.add(leave)
    db.session.flush()   # get leave.id before commit

    # ── Handle optional proof file upload ────────────────────────────────────
    proof_file = request.files.get("proof")
    if proof_file and proof_file.filename:
        if not _allowed(proof_file.filename):
            db.session.rollback()
            return jsonify({"success": False,
                            "message": "Invalid file type. Allowed: PDF, JPG, PNG."}), 400
        ext       = proof_file.filename.rsplit('.', 1)[1].lower()
        safe_name = f"leave_{leave.id}_{uuid.uuid4().hex}.{ext}"
        proof_file.save(os.path.join(PROOF_UPLOAD_DIR, safe_name))
        leave.proof_filename      = safe_name
        leave.proof_original_name = proof_file.filename

    db.session.commit()

    msg = ("Leave submitted! Your HOD will review it directly."
           if skip_mentor else
           "Leave submitted! Awaiting your mentor's approval.")
    return jsonify({"success": True, "message": msg, "leave": leave.to_dict()}), 201


# ── Student: My leaves ─────────────────────────────────────────────────────────

@leave_bp.route("/my", methods=["GET"])
@login_required
def my_leaves():
    leaves = (LeaveRequest.query
              .filter_by(student_id=current_user.id)
              .order_by(LeaveRequest.created_at.desc())
              .all())
    return jsonify({"success": True, "leaves": [l.to_dict() for l in leaves]})


# ── Mentor: Page ───────────────────────────────────────────────────────────────

@leave_bp.route("/pending/mentor", methods=["GET"])
@login_required
def mentor_pending():
    if not (current_user.is_faculty or current_user.is_hod):
        return jsonify({"success": False, "message": "Access denied."}), 403
    return render_template("leave/mentor_approve.html")


@leave_bp.route("/proof/<path:filename>", methods=["GET"])
@login_required
def serve_proof(filename):
    """Securely serve a proof document — only mentor, HOD, admin, or owner."""
    leave = LeaveRequest.query.filter_by(proof_filename=filename).first()
    if not leave:
        abort(404)
    # Access control
    is_owner   = leave.student_id == current_user.id
    is_allowed = (current_user.is_admin or current_user.is_hod or
                  current_user.is_faculty or is_owner)
    if not is_allowed:
        abort(403)
    return send_from_directory(PROOF_UPLOAD_DIR, filename)


@leave_bp.route("/api/mentor/pending", methods=["GET"])
@login_required
def api_mentor_pending():
    """Return leaves assigned STRICTLY to this mentor only — no leakage."""
    if not (current_user.is_faculty or current_user.is_hod):
        return jsonify({"success": False, "message": "Access denied."}), 403

    filter_mode = request.args.get("filter", "pending")   # pending | all

    # STRICT: only assigned mentees — never show other mentors' students
    query = LeaveRequest.query.filter(LeaveRequest.mentor_id == current_user.id)

    if filter_mode == "pending":
        query = query.filter(LeaveRequest.mentor_status == "pending")

    leaves = query.order_by(LeaveRequest.created_at.desc()).all()

    pending_count = LeaveRequest.query.filter_by(
        mentor_id=current_user.id, mentor_status="pending"
    ).count()

    return jsonify({
        "success": True,
        "leaves":  [l.to_dict() for l in leaves],
        "pending": pending_count,
    })



@leave_bp.route("/mentor/<int:leave_id>", methods=["PUT"])
@login_required
def mentor_action(leave_id):
    if not (current_user.is_faculty or current_user.is_hod):
        return jsonify({"success": False, "message": "Access denied."}), 403

    leave   = LeaveRequest.query.get_or_404(leave_id)
    data    = request.get_json() or {}
    action  = data.get("action", "")
    comment = data.get("comment", "").strip()

    # Guard: can only act on pending leaves at mentor stage
    if leave.mentor_status != "pending":
        return jsonify({"success": False,
                        "message": "This leave has already been reviewed by mentor."}), 400

    # Assign this faculty as mentor if not already
    if not leave.mentor_id:
        leave.mentor_id = current_user.id

    if action == "approve":
        leave.mentor_status  = "approved"
        leave.status         = "mentor_approved"
        leave.mentor_comment = comment or "Approved by mentor."
        leave.mentor_at      = _ist_now()
        # Ensure HOD is assigned for next step
        if not leave.hod_id:
            hod = User.query.filter_by(role="hod").first()
            if hod:
                leave.hod_id = hod.id

    elif action == "reject":
        leave.mentor_status  = "rejected"
        leave.status         = "rejected"
        leave.mentor_comment = comment or "Rejected by mentor."
        leave.mentor_at      = _ist_now()

    else:
        return jsonify({"success": False,
                        "message": "Invalid action. Use 'approve' or 'reject'."}), 400

    db.session.commit()

    # Optional notification
    try:
        from utils.notifications import notify_leave_decision
        notify_leave_decision(leave, action, comment, "Mentor")
    except Exception:
        pass

    return jsonify({"success": True, "leave": leave.to_dict()})


# ── HOD: Page + API ────────────────────────────────────────────────────────────

@leave_bp.route("/pending/hod", methods=["GET"])
@login_required
def hod_pending():
    if not current_user.is_hod:
        return jsonify({"success": False, "message": "Access denied."}), 403
    return render_template("leave/hod_approve.html")


@leave_bp.route("/api/hod/pending", methods=["GET"])
@login_required
def api_hod_pending():
    """Return mentor-approved leaves waiting for HOD sign-off."""
    if not current_user.is_hod:
        return jsonify({"success": False, "message": "Access denied."}), 403

    filter_mode = request.args.get("filter", "pending")   # pending | all

    query = (LeaveRequest.query
             .filter(LeaveRequest.mentor_status == "approved"))

    if filter_mode == "pending":
        query = query.filter(LeaveRequest.hod_status == "pending")

    leaves = query.order_by(LeaveRequest.created_at.desc()).all()

    return jsonify({
        "success": True,
        "leaves":  [l.to_dict() for l in leaves],
        "pending": LeaveRequest.query.filter_by(
            mentor_status="approved", hod_status="pending").count(),
    })


@leave_bp.route("/hod/<int:leave_id>", methods=["PUT"])
@login_required
def hod_action(leave_id):
    if not current_user.is_hod:
        return jsonify({"success": False, "message": "Access denied."}), 403

    leave   = LeaveRequest.query.get_or_404(leave_id)
    data    = request.get_json() or {}
    action  = data.get("action", "")
    comment = data.get("comment", "").strip()

    # Guard: must have mentor approval first
    if leave.mentor_status != "approved":
        return jsonify({"success": False,
                        "message": "Mentor has not approved this leave yet."}), 400

    # Guard: already acted
    if leave.hod_status != "pending":
        return jsonify({"success": False,
                        "message": "This leave has already been reviewed by HOD."}), 400

    if not leave.hod_id:
        leave.hod_id = current_user.id

    if action == "approve":
        leave.hod_status  = "approved"
        leave.status      = "approved"
        leave.hod_comment = comment or "Approved by HOD."
        leave.hod_at      = _ist_now()

        # ── Try IMMEDIATE adjustment if attendance already exists ──────────
        # For past dates, faculty have already marked attendance — adjust now.
        # For future dates, defer to the scheduler (runs at 12:05 PM IST).
        now_local  = datetime.now(COLLEGE_TZ)
        today_local = now_local.date()

        # Check if any AttendanceRecord already exists for this student on leave dates
        leave_dates_list = list(_date_range(leave.from_date, leave.to_date))
        existing_records = (AttendanceRecord.query
                            .filter_by(student_id=leave.student_id)
                            .filter(AttendanceRecord.date.in_(leave_dates_list))
                            .count())

        if existing_records > 0 or leave.to_date <= today_local:
            # Attendance already marked — adjust immediately
            db.session.commit()  # save hod approval first
            adj_result = _adjust_attendance(leave)
        else:
            # Future leave — defer to scheduler
            next_day_local = (now_local + timedelta(days=1)).replace(
                hour=ATTENDANCE_DEADLINE.hour,
                minute=ATTENDANCE_DEADLINE.minute,
                second=0, microsecond=0,
            )
            next_day_utc = next_day_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
            leave.adjust_after    = next_day_utc
            leave.adjustment_note = (
                f"Attendance will be auto-adjusted after "
                f"{next_day_local.strftime('%d %b %Y, %I:%M %p')} IST "
                f"(faculty attendance upload deadline)."
            )
            adj_result = {
                "deferred": True,
                "adjust_after": next_day_utc.isoformat(),
                "message": leave.adjustment_note,
            }

    elif action == "reject":
        leave.hod_status  = "rejected"
        leave.status      = "rejected"
        leave.hod_comment = comment or "Rejected by HOD."
        leave.hod_at      = _ist_now()
        adj_result = {}

    else:
        return jsonify({"success": False, "message": "Invalid action."}), 400

    db.session.commit()

    # Optional notification
    try:
        from utils.notifications import notify_leave_decision
        notify_leave_decision(leave, action, comment, "HOD")
    except Exception:
        pass

    return jsonify({
        "success":    True,
        "leave":      leave.to_dict(),
        "adjustment": adj_result,
    })


# ── All leaves overview (admin/faculty) ───────────────────────────────────────

@leave_bp.route("/all", methods=["GET"])
@login_required
def all_leaves():
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Access denied."}), 403
    leaves = (LeaveRequest.query
              .order_by(LeaveRequest.created_at.desc())
              .limit(200).all())
    return jsonify({"success": True, "leaves": [l.to_dict() for l in leaves]})


# ── Mentor Leave Report ────────────────────────────────────────────────────────

@leave_bp.route("/mentor/report", methods=["GET"])
@login_required
def mentor_report_page():
    """Mentor / Admin views the leave report for a mentor."""
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Access denied."}), 403
    return render_template("leave/mentor_report.html")


@leave_bp.route("/api/mentor/report", methods=["GET"])
@login_required
def api_mentor_report():
    """JSON report — summary + detail of all leaves handled by a mentor."""
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Access denied."}), 403

    # Admin can query any mentor; faculty/hod can only get their own
    if current_user.is_admin:
        mentor_id = request.args.get("mentor_id", type=int)
        if not mentor_id:
            return jsonify({"success": False, "message": "mentor_id required for admin"}), 400
    else:
        mentor_id = current_user.id

    mentor = db.session.get(User, mentor_id)
    if not mentor:
        return jsonify({"success": False, "message": "Mentor not found"}), 404

    leaves = (LeaveRequest.query
              .filter_by(mentor_id=mentor_id)
              .order_by(LeaveRequest.created_at.desc())
              .all())

    approved = [l for l in leaves if l.mentor_status == "approved"]
    rejected = [l for l in leaves if l.mentor_status == "rejected"]
    pending  = [l for l in leaves if l.mentor_status == "pending"]

    def fmt_leave(l):
        student = db.session.get(User, l.student_id)
        return {
            "id":             l.id,
            "student_name":   student.name        if student else "Unknown",
            "roll_number":    student.roll_number  if student else "—",
            "semester":       student.semester     if student else "—",
            "section":        student.section      if student else "—",
            "from_date":      l.from_date.strftime("%d %b %Y"),
            "to_date":        l.to_date.strftime("%d %b %Y"),
            "days":           l.days,
            "reason":         l.reason,
            "leave_type":     l.leave_type,
            "applied_on":     l.created_at.strftime("%d %b %Y, %I:%M %p") if l.created_at else "—",
            "mentor_status":  l.mentor_status,
            "mentor_comment": l.mentor_comment or "—",
            "mentor_at":      l.mentor_at.strftime("%d %b %Y, %I:%M %p") if l.mentor_at else "—",
            "hod_status":     l.hod_status,
            "final_status":   l.status,
        }

    return jsonify({
        "success": True,
        "mentor": {
            "id":          mentor.id,
            "name":        mentor.name,
            "designation": mentor.designation or mentor.role,
            "department":  mentor.department or "—",
        },
        "summary": {
            "total":    len(leaves),
            "approved": len(approved),
            "rejected": len(rejected),
            "pending":  len(pending),
        },
        "leaves": {
            "approved": [fmt_leave(l) for l in approved],
            "rejected": [fmt_leave(l) for l in rejected],
            "pending":  [fmt_leave(l) for l in pending],
        },
    })


@leave_bp.route("/mentor/report/download", methods=["GET"])
@login_required
def download_mentor_report():
    """Download mentor leave report as Excel (.xlsx)."""
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Access denied."}), 403

    import io
    import pandas as pd
    from flask import send_file

    mentor_id = (request.args.get("mentor_id", type=int)
                 if current_user.is_admin
                 else current_user.id)

    mentor = db.session.get(User, mentor_id)
    if not mentor:
        return jsonify({"success": False, "message": "Mentor not found"}), 404

    leaves = (LeaveRequest.query
              .filter_by(mentor_id=mentor_id)
              .order_by(LeaveRequest.created_at.desc())
              .all())

    rows = []
    for l in leaves:
        student = db.session.get(User, l.student_id)
        rows.append({
            "Student Name":    student.name        if student else "Unknown",
            "Roll Number":     student.roll_number  if student else "—",
            "Semester":        student.semester     if student else "—",
            "Section":         student.section      if student else "—",
            "Leave Type":      l.leave_type.title(),
            "From Date":       l.from_date.strftime("%d %b %Y"),
            "To Date":         l.to_date.strftime("%d %b %Y"),
            "Days":            l.days,
            "Reason":          l.reason,
            "Applied On":      l.created_at.strftime("%d %b %Y") if l.created_at else "—",
            "Mentor Decision": l.mentor_status.upper(),
            "Mentor Remarks":  l.mentor_comment or "—",
            "Mentor Decided At": l.mentor_at.strftime("%d %b %Y") if l.mentor_at else "Pending",
            "HOD Decision":    l.hod_status.upper(),
            "Final Status":    l.status.replace("_", " ").title(),
        })

    df = pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["Student Name","Roll Number","Semester","Section","Leave Type",
                 "From Date","To Date","Days","Reason","Applied On",
                 "Mentor Decision","Mentor Remarks","Mentor Decided At",
                 "HOD Decision","Final Status"])

    # Summary sheet
    summary_data = {
        "Metric": ["Mentor Name", "Designation", "Total Applications",
                   "Approved by Mentor", "Rejected by Mentor", "Pending with Mentor",
                   "Finally Approved (HOD)", "Finally Rejected"],
        "Value": [
            mentor.name,
            mentor.designation or mentor.role,
            len(leaves),
            sum(1 for l in leaves if l.mentor_status == "approved"),
            sum(1 for l in leaves if l.mentor_status == "rejected"),
            sum(1 for l in leaves if l.mentor_status == "pending"),
            sum(1 for l in leaves if l.status == "approved"),
            sum(1 for l in leaves if l.status == "rejected"),
        ]
    }
    df_summary = pd.DataFrame(summary_data)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_summary.to_excel(writer, index=False, sheet_name="Summary")
        df.to_excel(writer, index=False, sheet_name="All Leave Details")

        # Style summary sheet
        ws = writer.sheets["Summary"]
        ws.column_dimensions["A"].width = 28
        ws.column_dimensions["B"].width = 30

        # Style details sheet
        wd = writer.sheets["All Leave Details"]
        for col in wd.columns:
            max_len = max((len(str(cell.value or "")) for cell in col), default=10)
            wd.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)

    output.seek(0)
    filename = f"mentor_leave_report_{mentor.name.replace(' ','_')}_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return send_file(output, as_attachment=True,
                     download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ── All leaves overview (admin/faculty) ───────────────────────────────────────


def process_pending_leave_adjustments():
    """
    Called by the APScheduler job every day at 12:05 PM IST.

    Finds all HOD-approved leaves where:
      - attendance_adjusted is False
      - adjust_after <= now (UTC)  ← deadline has passed

    For each such leave, runs _adjust_attendance() which reads actual
    AttendanceRecord rows to discover which subjects had class on the
    leave dates, then credits only those subjects.
    """
    from flask import current_app
    now_utc = datetime.utcnow()

    due_leaves = (LeaveRequest.query
                  .filter_by(status="approved", attendance_adjusted=False)
                  .filter(LeaveRequest.adjust_after != None)
                  .filter(LeaveRequest.adjust_after <= now_utc)
                  .all())

    processed, skipped, errors = 0, 0, 0
    for leave in due_leaves:
        try:
            result = _adjust_attendance(leave)
            if result.get("skipped"):
                skipped += 1
            else:
                processed += 1
                current_app.logger.info(
                    f"[LEAVE-SCHEDULER] Adjusted leave #{leave.id} "
                    f"(student {leave.student_id}): "
                    f"{result.get('subjects_adjusted', 0)} subjects adjusted, "
                    f"skipped (no class): {result.get('skipped_subjects', [])}"
                )
        except Exception as e:
            errors += 1
            current_app.logger.error(
                f"[LEAVE-SCHEDULER] Error adjusting leave #{leave.id}: {e}")

    current_app.logger.info(
        f"[LEAVE-SCHEDULER] Done. Processed={processed}, "
        f"Skipped={skipped}, Errors={errors}"
    )
    return {"processed": processed, "skipped": skipped, "errors": errors}


# ── Core attendance adjustment logic ──────────────────────────────────────────

def _adjust_attendance(leave):
    """
    Adjust attendance for a single approved leave request.
    Called by process_pending_leave_adjustments() AFTER the 12 PM deadline
    so that faculty have had time to upload attendance.

    Strategy:
      1. Look for AttendanceRecord rows for this student where date falls
         in [from_date, to_date] and status == 'absent'.
         → Flip those to 'od' and recompute AttendanceSummary for each subject.
      2. If NO records exist for those dates (attendance not yet uploaded),
         fall back to incrementing each summary by the number of leave days,
         capped at total_classes.

    Returns a dict describing what was adjusted (for the API response).
    """
    if leave.attendance_adjusted:
        return {"skipped": True, "reason": "Already adjusted."}

    leave_dates = set(_date_range(leave.from_date, leave.to_date))
    adjusted_subjects = {}

    # ── Strategy 1: record-level flip ─────────────────────────────────────────
    absent_records = (AttendanceRecord.query
                      .filter_by(student_id=leave.student_id, status="absent")
                      .filter(AttendanceRecord.date.in_(leave_dates))
                      .all())

    if absent_records:
        affected_subjects = set(r.subject for r in absent_records)
        for r in absent_records:
            r.status = "od"   # Credit leave day as On Duty (excused absence)

        db.session.flush()   # Push flips before recomputing summaries

        # Recompute each affected summary using P/(P+A) formula
        # OD records are EXCLUDED from both total and attended (they are excused)
        for subject in affected_subjects:
            summary = (AttendanceSummary.query
                       .filter_by(student_id=leave.student_id, subject=subject)
                       .first())
            if not summary:
                continue

            all_recs = (AttendanceRecord.query
                        .filter_by(student_id=leave.student_id, subject=subject)
                        .all())
            # OD excluded — only P and A count toward attendance %
            present = sum(1 for r in all_recs if r.status == "present")
            absent  = sum(1 for r in all_recs if r.status == "absent")
            total   = present + absent

            if total > 0:
                pct = round((present / total) * 100, 2)
                summary.classes_attended = present
                summary.total_classes    = total
                summary.attendance_pct   = pct
                summary.is_defaulter     = pct < 85.0
                summary.required_classes = summary.calculate_required_classes()
                adjusted_subjects[subject] = {
                    "present": present, "absent": absent, "total": total, "pct": pct,
                }

    else:
        # ── Strategy 2: SMART summary-level fallback ──────────────────────────
        from models.user import User as _User
        student = _User.query.get(leave.student_id)
        student_semester = student.semester if student else None
        student_section  = student.section  if student else None

        # Build: subject → list of leave dates on which that subject had class
        subject_class_dates = {}   # {subject: [date, date, ...]}
        for leave_date in leave_dates:
            if student_semester and student_section:
                date_subjs = (AttendanceRecord.query
                              .filter(
                                  AttendanceRecord.date == leave_date,
                                  AttendanceRecord.semester == student_semester,
                                  AttendanceRecord.section == student_section,
                              )
                              .with_entities(AttendanceRecord.subject)
                              .distinct().all())
            else:
                date_subjs = []

            if not date_subjs:
                date_subjs = (AttendanceRecord.query
                              .filter(AttendanceRecord.date == leave_date)
                              .with_entities(AttendanceRecord.subject)
                              .distinct().all())

            for (subj,) in date_subjs:
                subject_class_dates.setdefault(subj, []).append(leave_date)

        summaries = (AttendanceSummary.query
                     .filter_by(student_id=leave.student_id)
                     .all())
        skipped_no_class = []
        for s in summaries:
            dates_with_class = subject_class_dates.get(s.subject, [])
            if not dates_with_class:
                skipped_no_class.append(s.subject)
                continue

            # Create AttendanceRecord rows so grid/mark view shows 1
            for leave_date in dates_with_class:
                existing = (AttendanceRecord.query
                            .filter_by(student_id=leave.student_id,
                                       subject=s.subject,
                                       date=leave_date)
                            .first())
                if not existing:
                    db.session.add(AttendanceRecord(
                        student_id  = leave.student_id,
                        subject     = s.subject,
                        date        = leave_date,
                        status      = "present",
                        semester    = student_semester,
                        section     = student_section,
                    ))
                elif existing.status == "absent":
                    existing.status = "present"

            # Update summary
            days_credited = len(dates_with_class)
            before = s.classes_attended
            s.classes_attended = min(s.classes_attended + days_credited, s.total_classes)
            if s.total_classes > 0:
                s.attendance_pct   = round(
                    (s.classes_attended / s.total_classes) * 100, 2)
                s.is_defaulter     = s.attendance_pct < 85.0
                s.required_classes = s.calculate_required_classes()
            adjusted_subjects[s.subject] = {
                "before":              before,
                "after":               s.classes_attended,
                "pct":                 s.attendance_pct,
                "class_days_credited": days_credited,
            }

    leave.attendance_adjusted = True
    db.session.commit()

    return {
        "skipped":           False,
        "strategy":          "record_flip" if absent_records else "smart_summary_fallback",
        "subjects_adjusted": len(adjusted_subjects),
        "details":           adjusted_subjects,
        "skipped_subjects":  locals().get("skipped_no_class", []),
    }
