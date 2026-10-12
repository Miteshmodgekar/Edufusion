"""
Academic Lifecycle Blueprint — Semester Promotion, Graduation, Batch Management,
Student Academic History.

Security model:
  - All admin operations require role == 'admin', enforced server-side.
  - Student history endpoints verify the authenticated user owns the data.
  - Period IDs, student IDs, and USNs submitted by the browser are always
    validated against the database — never trusted as-is.
  - All multi-step operations use database transactions with rollback on failure.
  - Idempotency tokens prevent duplicate promotion from double-clicks or retries.
"""

import json
from datetime import datetime, date, timezone, timedelta
from functools import wraps

from flask import Blueprint, jsonify, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError

from extensions import db
from models.user import User, normalize_usn
from models.academic import (
    AcademicPeriod, StudentEnrollment, AcademicAuditLog,
    PeriodStatus, EnrollmentStatus, StudentStatus
)
from models.attendance import AttendanceSummary, AttendanceRecord
from models.internal_marks import InternalMarks

academic_bp = Blueprint("academic", __name__)

_IST = timezone(timedelta(hours=5, minutes=30))


def _ist_now():
    return datetime.now(_IST).replace(tzinfo=None)


# ── Authorization helpers ──────────────────────────────────────────────────────

def admin_required(f):
    """Decorator: reject non-admins with 403."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            if request.is_json or request.path.startswith("/academic/api"):
                return jsonify({"success": False, "message": "Admin only."}), 403
            flash("Admin access required.", "danger")
            return redirect(url_for("dashboard.index"))
        return f(*args, **kwargs)
    return decorated


def _student_owns_period(student: User, period_id: int) -> bool:
    """
    Verify that the given student has an enrollment in the given academic period.
    Used to prevent students from accessing another student's historical records
    by manipulating URL parameters.
    """
    return StudentEnrollment.query.filter_by(
        student_id=student.id,
        academic_period_id=period_id
    ).first() is not None


# ── Admin Pages ───────────────────────────────────────────────────────────────

@academic_bp.route("/")
@academic_bp.route("/manage")
@login_required
@admin_required
def manage():
    """Admin: Academic period and lifecycle management main page."""
    return render_template("admin/academic/manage.html")


@academic_bp.route("/batches")
@login_required
@admin_required
def batches():
    """Admin: Batch and enrollment management page."""
    return render_template("admin/academic/batches.html")


@academic_bp.route("/promote")
@login_required
@admin_required
def promote_page():
    """Admin: Semester promotion page."""
    return render_template("admin/academic/promote.html")


@academic_bp.route("/graduation")
@login_required
@admin_required
def graduation_page():
    """Admin: Graduation management page."""
    return render_template("admin/academic/graduation.html")


@academic_bp.route("/audit")
@login_required
@admin_required
def audit_page():
    """Admin: Audit log page."""
    return render_template("admin/academic/audit.html")


@academic_bp.route("/import")
@login_required
@admin_required
def import_page():
    """Admin: Import existing students into the academic lifecycle system."""
    return render_template("admin/academic/import_existing.html")


# ── Admin API: Import Existing Students ───────────────────────────────────────

@academic_bp.route("/api/import/preview", methods=["GET"])
@login_required
@admin_required
def api_import_preview():
    """
    Scan existing students in the database who do NOT yet have any
    StudentEnrollment records. Group them by their current users.semester.
    Returns a preview so the admin can assign academic years and batches
    before committing the import.
    """
    # Find all active students with no enrollment records yet
    enrolled_student_ids = db.session.query(
        StudentEnrollment.student_id
    ).distinct().subquery()

    unlinked_students = (
        User.query
        .filter_by(role="student")
        .filter(~User.id.in_(enrolled_student_ids))
        .order_by(User.semester, User.section, User.name)
        .all()
    )

    # Group by semester
    by_sem = {}
    for s in unlinked_students:
        sem = s.semester or 0
        if sem not in by_sem:
            by_sem[sem] = []
        by_sem[sem].append({
            "id":              s.id,
            "name":            s.name,
            "usn":             s.effective_usn,
            "section":         s.section or "A",
            "department":      s.department or "CSE",
            "semester":        sem,
            "admission_batch": s.admission_batch or "",
            "status":          s.status or "active",
        })

    # Also report already-linked students
    total_students     = User.query.filter_by(role="student").count()
    already_linked     = total_students - len(unlinked_students)

    return jsonify({
        "success":        True,
        "by_semester":    {str(k): v for k, v in sorted(by_sem.items())},
        "total_unlinked": len(unlinked_students),
        "already_linked": already_linked,
        "total_students": total_students,
        "semesters_found": sorted(by_sem.keys()),
    })


@academic_bp.route("/api/import/existing", methods=["POST"])
@login_required
@admin_required
def api_import_existing():
    """
    Bulk-import existing students into the academic lifecycle system.

    For each semester group the admin configures:
      1. Create (or reuse) an AcademicPeriod for that (batch, semester, academic_year).
      2. Create a StudentEnrollment for every student in that group.
      3. Backfill users.admission_batch if not set.

    Idempotent:
      - Existing periods are reused (not duplicated).
      - Existing enrollments are skipped (not duplicated).
      - users.admission_batch is only set when currently NULL/empty.

    Expected body:
    {
      "semester_configs": [
        {
          "semester_number": 4,
          "admission_batch": "2021",
          "academic_year":   "2023-24",
          "department":      "CSE",
          "student_ids":     [1, 2, 3, ...],   // specific IDs, or omit for all in that sem
          "period_status":   "active"           // optional
        },
        ...
      ]
    }
    """
    data    = request.get_json() or {}
    configs = data.get("semester_configs", [])

    if not configs:
        return jsonify({"success": False,
                        "message": "semester_configs list is required."}), 400

    results = []
    total_enrolled  = 0
    total_skipped   = 0
    total_errors    = 0

    try:
        for cfg in configs:
            sem_num   = cfg.get("semester_number")
            batch     = (cfg.get("admission_batch") or "").strip()
            acad_year = (cfg.get("academic_year") or "").strip()
            dept      = (cfg.get("department") or "CSE").strip()
            status    = cfg.get("period_status", PeriodStatus.ACTIVE)
            student_ids = cfg.get("student_ids")  # None = all students in this semester

            if not sem_num or not batch or not acad_year:
                results.append({
                    "semester_number": sem_num,
                    "success": False,
                    "message": "semester_number, admission_batch, and academic_year are required.",
                })
                total_errors += 1
                continue

            try:
                sem_num = int(sem_num)
            except (TypeError, ValueError):
                results.append({"semester_number": sem_num, "success": False,
                                 "message": "semester_number must be an integer."})
                total_errors += 1
                continue

            # ── Step 1: Get or create AcademicPeriod ──────────────────────────
            period = AcademicPeriod.query.filter_by(
                admission_batch=batch,
                semester_number=sem_num,
                academic_year=acad_year,
            ).first()

            period_created = False
            if not period:
                period = AcademicPeriod(
                    admission_batch = batch,
                    semester_number = sem_num,
                    academic_year   = acad_year,
                    department      = dept,
                    status          = status if status in (
                        PeriodStatus.PLANNED, PeriodStatus.ACTIVE,
                        PeriodStatus.COMPLETED, PeriodStatus.CLOSED
                    ) else PeriodStatus.ACTIVE,
                    created_by      = current_user.id,
                )
                db.session.add(period)
                db.session.flush()   # get period.id
                period_created = True

            # ── Step 2: Determine which students to enroll ────────────────────
            if student_ids is not None:
                # Admin specified exact student IDs
                students = (User.query
                            .filter(User.id.in_(student_ids))
                            .filter_by(role="student")
                            .all())
            else:
                # All students with this semester number who aren't enrolled yet
                enrolled_ids = db.session.query(
                    StudentEnrollment.student_id
                ).filter_by(academic_period_id=period.id).all()
                enrolled_ids = {row[0] for row in enrolled_ids}

                students = (User.query
                            .filter_by(role="student", semester=sem_num)
                            .all())
                students = [s for s in students if s.id not in enrolled_ids]

            # ── Step 3: Enroll each student ───────────────────────────────────
            enrolled = []
            skipped  = []
            errors   = []

            for student in students:
                # Skip graduated students
                if student.status == StudentStatus.GRADUATED:
                    skipped.append({
                        "id": student.id, "usn": student.effective_usn,
                        "reason": "Already graduated."
                    })
                    continue

                # Idempotency: skip if already enrolled in this period
                existing = StudentEnrollment.query.filter_by(
                    student_id=student.id,
                    academic_period_id=period.id
                ).first()
                if existing:
                    skipped.append({
                        "id": student.id, "usn": student.effective_usn,
                        "reason": "Already enrolled in this period."
                    })
                    continue

                # Backfill admission_batch if not set
                if not student.admission_batch:
                    student.admission_batch = batch

                enrollment = StudentEnrollment(
                    student_id         = student.id,
                    academic_period_id = period.id,
                    status             = EnrollmentStatus.ENROLLED,
                    section            = student.section or "A",
                    enrollment_date    = _ist_now().date(),
                    notes              = f"Imported from legacy data by {current_user.name}",
                )
                db.session.add(enrollment)
                enrolled.append({
                    "id":  student.id,
                    "usn": student.effective_usn,
                    "name": student.name,
                })

            # Audit entry per semester group
            db.session.add(AcademicAuditLog(
                event_type   = "batch_created" if period_created else "enrollment_status_change",
                performed_by = current_user.id,
                description  = (
                    f"Imported existing students into "
                    f"Batch {batch} Sem {sem_num} ({acad_year}): "
                    f"{len(enrolled)} enrolled, {len(skipped)} skipped."
                ),
                detail_json  = json.dumps({"enrolled": enrolled, "skipped": skipped}),
            ))

            results.append({
                "semester_number": sem_num,
                "batch":           batch,
                "academic_year":   acad_year,
                "period_id":       period.id,
                "period_created":  period_created,
                "success":         True,
                "enrolled":        len(enrolled),
                "skipped":         len(skipped),
                "errors":          len(errors),
                "enrolled_list":   enrolled,
                "skipped_list":    skipped,
            })
            total_enrolled += len(enrolled)
            total_skipped  += len(skipped)

        db.session.commit()

    except Exception as exc:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": f"Import failed and was rolled back: {exc}",
            "results": results,
        }), 500

    return jsonify({
        "success":        True,
        "total_enrolled": total_enrolled,
        "total_skipped":  total_skipped,
        "total_errors":   total_errors,
        "results":        results,
        "message": (
            f"Import complete: {total_enrolled} student(s) enrolled across "
            f"{len(results)} semester group(s). "
            f"{total_skipped} skipped (already enrolled). "
            f"{total_errors} config error(s)."
        ),
    })


# ── Student Page ──────────────────────────────────────────────────────────────

@academic_bp.route("/history")
@login_required
def student_history_page():
    """Student: My Academic History page."""
    if not current_user.is_student:
        flash("This page is for students only.", "warning")
        return redirect(url_for("dashboard.index"))
    return render_template("dashboard/academic_history.html")





@academic_bp.route("/api/periods", methods=["GET"])
@login_required
@admin_required
def api_list_periods():
    """Return all academic periods, optionally filtered by batch or status."""
    batch  = request.args.get("batch")
    status = request.args.get("status")
    q = AcademicPeriod.query.order_by(
        AcademicPeriod.admission_batch.desc(),
        AcademicPeriod.semester_number
    )
    if batch:  q = q.filter_by(admission_batch=batch)
    if status: q = q.filter_by(status=status)
    periods = q.all()
    return jsonify({"success": True, "periods": [p.to_dict() for p in periods]})


@academic_bp.route("/api/periods", methods=["POST"])
@login_required
@admin_required
def api_create_period():
    """Create a new academic period. Prevents duplicate (batch, semester, year)."""
    data = request.get_json() or {}
    batch  = (data.get("admission_batch") or "").strip()
    sem    = data.get("semester_number")
    year   = (data.get("academic_year") or "").strip()
    dept   = (data.get("department") or "CSE").strip()
    label  = (data.get("label") or "").strip() or None
    start  = data.get("start_date")
    end    = data.get("end_date")

    if not batch or not sem or not year:
        return jsonify({"success": False,
                        "message": "admission_batch, semester_number, and academic_year are required."}), 400
    try:
        sem = int(sem)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "semester_number must be an integer 1–8."}), 400
    if not (1 <= sem <= 8):
        return jsonify({"success": False, "message": "semester_number must be between 1 and 8."}), 400

    # Check for existing period
    existing = AcademicPeriod.query.filter_by(
        admission_batch=batch, semester_number=sem, academic_year=year
    ).first()
    if existing:
        return jsonify({
            "success": False,
            "message": f"An academic period for Batch {batch} Sem {sem} ({year}) already exists.",
            "existing": existing.to_dict()
        }), 409

    try:
        start_date = datetime.strptime(start, "%Y-%m-%d").date() if start else None
        end_date   = datetime.strptime(end,   "%Y-%m-%d").date() if end   else None
    except ValueError:
        return jsonify({"success": False, "message": "Invalid date format. Use YYYY-MM-DD."}), 400

    period = AcademicPeriod(
        admission_batch  = batch,
        semester_number  = sem,
        academic_year    = year,
        department       = dept,
        label            = label,
        start_date       = start_date,
        end_date         = end_date,
        status           = PeriodStatus.PLANNED,
        created_by       = current_user.id,
    )
    db.session.add(period)

    # Audit log
    audit = AcademicAuditLog(
        event_type   = "period_created",
        performed_by = current_user.id,
        description  = f"Created academic period: Batch {batch} Sem {sem} ({year})",
    )
    db.session.add(audit)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"success": False,
                        "message": "A period with this batch/semester/year combination already exists."}), 409

    return jsonify({"success": True, "period": period.to_dict()}), 201


@academic_bp.route("/api/periods/<int:period_id>", methods=["PATCH"])
@login_required
@admin_required
def api_update_period(period_id):
    """Update period status, dates, or label."""
    period = db.get_or_404(AcademicPeriod, period_id)
    data   = request.get_json() or {}

    allowed_status = {PeriodStatus.PLANNED, PeriodStatus.ACTIVE,
                      PeriodStatus.COMPLETED, PeriodStatus.CLOSED}

    if "status" in data:
        new_status = data["status"]
        if new_status not in allowed_status:
            return jsonify({"success": False,
                            "message": f"Invalid status. Allowed: {sorted(allowed_status)}"}), 400
        # Prevent re-opening a closed period without explicit reason
        if period.status == PeriodStatus.CLOSED and new_status != PeriodStatus.CLOSED:
            reason = (data.get("reason") or "").strip()
            if not reason:
                return jsonify({"success": False,
                                "message": "A reason is required to re-open a closed period."}), 400
        old_status = period.status
        period.status = new_status
        # Audit
        db.session.add(AcademicAuditLog(
            event_type   = "period_status_change",
            performed_by = current_user.id,
            description  = f"Period '{period._auto_label()}' status changed from {old_status} to {new_status}",
            reason       = data.get("reason"),
        ))

    if "label" in data:
        period.label = (data["label"] or "").strip() or None
    if "start_date" in data and data["start_date"]:
        try: period.start_date = datetime.strptime(data["start_date"], "%Y-%m-%d").date()
        except ValueError: pass
    if "end_date" in data and data["end_date"]:
        try: period.end_date = datetime.strptime(data["end_date"], "%Y-%m-%d").date()
        except ValueError: pass

    db.session.commit()
    return jsonify({"success": True, "period": period.to_dict()})


@academic_bp.route("/api/periods/<int:period_id>/close", methods=["POST"])
@login_required
@admin_required
def api_close_period(period_id):
    """
    Close a period. Verifies all enrolled students have a final status
    (completed, detained, or withdrawn) before closing.
    """
    period = db.get_or_404(AcademicPeriod, period_id)
    if period.status == PeriodStatus.CLOSED:
        return jsonify({"success": False, "message": "Period is already closed."}), 400

    data   = request.get_json() or {}
    force  = data.get("force", False)

    # Check for students still in 'enrolled' status
    still_enrolled = StudentEnrollment.query.filter_by(
        academic_period_id=period_id,
        status=EnrollmentStatus.ENROLLED
    ).count()

    if still_enrolled > 0 and not force:
        return jsonify({
            "success": False,
            "message": f"{still_enrolled} student(s) still have 'enrolled' status. "
                       "Resolve their status before closing, or use force=true to override.",
            "still_enrolled": still_enrolled
        }), 400

    old_status    = period.status
    period.status = PeriodStatus.CLOSED
    db.session.add(AcademicAuditLog(
        event_type   = "period_closed",
        performed_by = current_user.id,
        source_period_id = period_id,
        description  = f"Period '{period._auto_label()}' closed (was {old_status}). "
                       f"{still_enrolled} student(s) still enrolled (force={force}).",
        reason       = data.get("reason"),
    ))
    db.session.commit()
    return jsonify({"success": True, "period": period.to_dict()})


@academic_bp.route("/api/periods/<int:period_id>", methods=["DELETE"])
@login_required
@admin_required
def api_delete_period(period_id):
    """
    Permanently delete an academic period.

    Safety rules:
      - Cannot delete a period with active enrolled students.
      - Periods with ONLY completed/detained/withdrawn enrollment records CAN be
        deleted — after admin confirmation in the UI.
      - Attendance summaries and internal marks (keyed by student + semester)
        are preserved; only the period row and its enrollment records are removed.
      - The deletion is logged to the audit trail before committing.
    """
    period = db.session.get(AcademicPeriod, period_id)
    if not period:
        return jsonify({"success": False, "message": "Period not found."}), 404

    # Block deletion if any student is still actively enrolled
    active_count = StudentEnrollment.query.filter_by(
        academic_period_id=period_id,
        status=EnrollmentStatus.ENROLLED
    ).count()
    if active_count > 0:
        return jsonify({
            "success": False,
            "message": (
                f"Cannot delete: {active_count} student(s) are actively enrolled in this period. "
                "Change their enrollment status first (complete, detain, or withdraw), "
                "then delete the period."
            ),
            "active_enrolled": active_count,
        }), 400

    label = period._auto_label()
    total_enrollments = StudentEnrollment.query.filter_by(
        academic_period_id=period_id
    ).count()

    try:
        # ── Step 1: Write deletion audit log (no FK to period — use description only)
        db.session.add(AcademicAuditLog(
            event_type       = "period_deleted",
            performed_by     = current_user.id,
            source_period_id = None,   # Cannot set FK — period will be deleted
            description      = (
                f"Admin deleted period '{label}' "
                f"[id={period_id}, batch={period.admission_batch}, "
                f"sem={period.semester_number}, year={period.academic_year}, "
                f"status={period.status}]. "
                f"{total_enrollments} enrollment record(s) removed. "
                "Attendance and marks data preserved."
            ),
        ))
        db.session.flush()

        # ── Step 2: Null-out FK references in existing audit log rows
        # (preserves the audit history text — just removes the broken FK pointer)
        db.session.execute(
            db.text(
                "UPDATE academic_audit_log "
                "SET source_period_id = NULL "
                "WHERE source_period_id = :pid"
            ),
            {"pid": period_id}
        )
        db.session.execute(
            db.text(
                "UPDATE academic_audit_log "
                "SET dest_period_id = NULL "
                "WHERE dest_period_id = :pid"
            ),
            {"pid": period_id}
        )

        # ── Step 3: Delete enrollment records (FK constraint)
        StudentEnrollment.query.filter_by(academic_period_id=period_id).delete()

        # ── Step 4: Delete the period itself
        db.session.delete(period)
        db.session.commit()

    except Exception as exc:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": f"Deletion failed and was rolled back: {exc}"
        }), 500

    return jsonify({
        "success": True,
        "message": f"Period '{label}' deleted. {total_enrollments} enrollment record(s) removed.",
        "deleted_period_id": period_id,
    })


# ── Admin API: Enrollments ─────────────────────────────────────────────────────

@academic_bp.route("/api/periods/<int:period_id>/enrollments", methods=["GET"])
@login_required
@admin_required
def api_period_enrollments(period_id):
    """Return all student enrollments for a given period."""
    period = db.get_or_404(AcademicPeriod, period_id)
    section = request.args.get("section")
    q = StudentEnrollment.query.filter_by(academic_period_id=period_id)
    if section:
        q = q.filter_by(section=section.upper())
    enrollments = q.join(User).order_by(User.name).all()
    return jsonify({
        "success":     True,
        "period":      period.to_dict(),
        "enrollments": [e.to_dict() for e in enrollments],
        "count":       len(enrollments),
    })


@academic_bp.route("/api/enroll", methods=["POST"])
@login_required
@admin_required
def api_enroll_students():
    """
    Enroll one or more students in an academic period.
    Used when creating a new batch (Sem 1) or manually enrolling a student.
    Idempotent: existing enrollments are skipped, not duplicated.
    """
    data = request.get_json() or {}
    period_id   = data.get("period_id")
    student_ids = data.get("student_ids", [])
    section     = (data.get("section") or "").strip().upper() or None

    if not period_id:
        return jsonify({"success": False, "message": "period_id is required."}), 400
    if not student_ids:
        return jsonify({"success": False, "message": "student_ids list is required."}), 400

    period = db.session.get(AcademicPeriod, period_id)
    if not period:
        return jsonify({"success": False, "message": "Academic period not found."}), 404

    if period.status == PeriodStatus.CLOSED:
        return jsonify({"success": False, "message": "Cannot enroll students in a closed period."}), 400

    enrolled = 0
    skipped  = 0
    errors   = []

    for sid in student_ids:
        student = db.session.get(User, sid)
        if not student or not student.is_student:
            errors.append({"student_id": sid, "error": "Student not found."})
            continue
        if student.is_graduated:
            errors.append({"student_id": sid, "usn": student.effective_usn,
                           "error": "Graduated students cannot be enrolled in new periods."})
            continue

        # Idempotency check
        existing = StudentEnrollment.query.filter_by(
            student_id=sid, academic_period_id=period_id
        ).first()
        if existing:
            skipped += 1
            continue

        enrollment = StudentEnrollment(
            student_id         = sid,
            academic_period_id = period_id,
            status             = EnrollmentStatus.ENROLLED,
            section            = section or student.section,
            enrollment_date    = _ist_now().date(),
        )
        db.session.add(enrollment)
        enrolled += 1

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"success": False,
                        "message": "Duplicate enrollment detected. Please retry."}), 409

    return jsonify({
        "success":  True,
        "enrolled": enrolled,
        "skipped":  skipped,
        "errors":   errors,
        "message":  f"Enrolled {enrolled} student(s) in {period._auto_label()}. "
                    f"{skipped} already enrolled. {len(errors)} error(s).",
    })


# ── Admin API: Promotion ───────────────────────────────────────────────────────

# ── Idempotency store (in-process; sufficient for single-process deployments) ──
# Maps promotion_token → result dict. Prevents double-click duplicate executions.
_promotion_tokens: dict = {}

YEAR_BACK_BOUNDARY_SEM = 4   # Students in Sem 4 are checked before moving to Sem 5


def _year_back_warning(source_sem: int) -> dict | None:
    """
    Return a Year Back warning dict if this promotion crosses the confirmed
    boundary (currently Sem 4 → Sem 5). Returns None for other semesters.

    Policy status: the specific first-year subject clearance criteria have NOT
    been formally verified with the college admin. The rule is therefore surfaced
    as an admin WARNING requiring manual review — it does NOT automatically block
    any student from promotion. Authorized admins can still hold back specific
    students using the individual hold-back mechanism.
    """
    if source_sem == YEAR_BACK_BOUNDARY_SEM:
        return {
            "boundary": f"Semester {source_sem} → Semester {source_sem + 1}",
            "message": (
                "⚠ Year Back Review Boundary: Some students may need to clear "
                "first-year subjects before progressing to Semester 5. "
                "Please review each student's backlog status manually. "
                "The exact subject clearance criteria have NOT yet been formally "
                "verified — this is an admin review warning only. "
                "Students are NOT automatically blocked. Use individual hold-back "
                "to detain specific students after your review."
            ),
            "policy_verified": False,
            "auto_blocks": False,
        }
    return None


def _derive_academic_year(source_year: str, source_sem: int) -> str:
    """
    Derive the destination academic_year string from the source period.

    Indian academic calendar convention used in this project:
      - Odd semesters (1, 3, 5, 7) run in the first half of an academic year  (Jul–Nov)
      - Even semesters (2, 4, 6, 8) run in the second half of the same year   (Jan–May)
      - Moving from an even semester to the next odd semester (e.g. 2→3, 4→5)
        advances the academic year by one session.
      - Moving from an odd semester to the next even semester (e.g. 1→2, 3→4)
        stays in the same academic year.

    Examples:
      Sem 1 (2026-27) → Sem 2 stays (2026-27)
      Sem 2 (2026-27) → Sem 3 advances to (2027-28)
      Sem 4 (2024-25) → Sem 5 advances to (2025-26)   ← matches real DB data
    """
    dest_sem = source_sem + 1
    # If moving from even → odd (year rolls over)
    if source_sem % 2 == 0:
        try:
            parts = source_year.split("-")
            start = int(parts[0])
            return f"{start + 1}-{str(start + 2)[-2:]}"
        except Exception:
            return source_year   # Fall back to same year if parsing fails
    # Odd → even: same academic year
    return source_year


def _get_or_create_dest_period(source: "AcademicPeriod") -> tuple:
    """
    Locate the destination AcademicPeriod for a promotion and create it if it
    does not yet exist.

    Returns: (dest_period, was_created, error_message)
      - dest_period:    AcademicPeriod object ready to use (not yet committed)
      - was_created:    True if a new period was just created in this call
      - error_message:  Non-None string if the operation cannot proceed safely

    Rules enforced:
      - Sem 8 → Sem 9 is always refused (graduation workflow handles Sem 8).
      - Batch isolation: destination always has same admission_batch.
      - Duplicate prevention: uses DB unique constraint (batch, sem, year);
        any IntegrityError from a race condition is caught and the existing
        row is returned.
      - No dates are invented — start_date and end_date are left NULL;
        the admin can fill them in Academic Management later if needed.
    """
    from sqlalchemy.exc import IntegrityError as _IE

    next_sem = source.semester_number + 1

    # Hard stop: never create Sem 9
    if next_sem > 8:
        return None, False, (
            "Semester 8 is the final semester. "
            "Use the Graduation workflow instead of promotion."
        )

    dest_year = _derive_academic_year(source.academic_year, source.semester_number)

    # 1. Try to find an existing period (same batch + sem, any academic_year)
    #    Prefer the one with the derived year; fall back to any match.
    dest = AcademicPeriod.query.filter_by(
        admission_batch=source.admission_batch,
        semester_number=next_sem,
    ).order_by(
        # Exact year match first
        db.case(
            (AcademicPeriod.academic_year == dest_year, 0),
            else_=1
        )
    ).first()

    if dest:
        # Found existing — validate it is not closed
        if dest.status == PeriodStatus.CLOSED:
            return None, False, (
                f"The destination period '{dest._auto_label()}' is closed. "
                "Re-open it in Academic Management or create a new period for this session."
            )
        return dest, False, None

    # 2. Create a new period
    new_period = AcademicPeriod(
        admission_batch = source.admission_batch,
        semester_number = next_sem,
        academic_year   = dest_year,
        department      = source.department or "CSE",
        status          = PeriodStatus.PLANNED,
        created_by      = current_user.id,
        # label is left None → _auto_label() generates it dynamically
    )
    db.session.add(new_period)
    try:
        db.session.flush()   # Get the ID; don't commit yet — caller owns the transaction
        return new_period, True, None
    except _IE:
        db.session.rollback()
        # Race condition: another request created it between our SELECT and INSERT
        dest = AcademicPeriod.query.filter_by(
            admission_batch=source.admission_batch,
            semester_number=next_sem,
        ).first()
        if dest:
            return dest, False, None
        return None, False, "Could not create destination period (database constraint error)."


@academic_bp.route("/api/promote/preview", methods=["POST"])
@login_required
@admin_required
def api_promote_preview():
    """
    PHASE 4 — Enhanced promotion preview.

    Returns ALL non-graduated students enrolled in the source period as
    promotion candidates, regardless of enrollment_status.  This aligns with
    the college policy that students may progress even if they have failed
    subjects (Phase 6).  Detained/withdrawn students are flagged separately.

    Auto-determines the next semester (source + 1) and rejects Sem 8 → Sem 9.

    Also detects the Year Back boundary and returns a warning if applicable.
    """
    data             = request.get_json() or {}
    source_period_id = data.get("source_period_id")

    if not source_period_id:
        return jsonify({"success": False, "message": "source_period_id is required."}), 400

    source = db.session.get(AcademicPeriod, source_period_id)
    if not source:
        return jsonify({"success": False, "message": "Source period not found."}), 404

    # Sem 8 guard — graduation handles final semester
    if source.semester_number >= 8:
        return jsonify({
            "success": False,
            "message": (
                "Semester 8 is the final semester. Students cannot be promoted to "
                "Semester 9. Please use the Graduation workflow."
            ),
            "graduation_required": True,
        }), 400

    next_sem_number = source.semester_number + 1

    # ── Auto-resolve destination period (read-only preview) ───────────────────
    # For preview we just derive the dest info; we do NOT flush or commit.
    dest_year = _derive_academic_year(source.academic_year, source.semester_number)
    dest = AcademicPeriod.query.filter_by(
        admission_batch=source.admission_batch,
        semester_number=next_sem_number,
    ).order_by(
        db.case(
            (AcademicPeriod.academic_year == dest_year, 0),
            else_=1
        )
    ).first()

    dest_will_be_created = dest is None
    dest_dict = dest.to_dict() if dest else {
        "id": None,
        "label": f"Batch {source.admission_batch} — Sem {next_sem_number} ({dest_year}) [will be created]",
        "admission_batch": source.admission_batch,
        "semester_number": next_sem_number,
        "academic_year":   dest_year,
        "department":      source.department or "CSE",
        "status":          "planned",
    }

    # If an existing dest is closed we must tell the admin
    if dest and dest.status == "closed":
        return jsonify({
            "success": False,
            "message": (
                f"The destination period '{dest._auto_label()}' is closed. "
                "Re-open it in Academic Management first."
            )
        }), 400

    # ── Fetch all enrollments in source period ─────────────────────────────────
    all_enrollments = (
        StudentEnrollment.query
        .filter_by(academic_period_id=source_period_id)
        .join(User)
        .order_by(User.name)
        .all()
    )

    # Students already in destination period
    already_in_dest = set()
    if dest:
        already_in_dest = {
            e.student_id
            for e in StudentEnrollment.query.filter_by(academic_period_id=dest.id).all()
        }

    # ── Classify students ──────────────────────────────────────────────────────
    # Policy (Phase 6): ALL enrolled students are promotion-eligible by default.
    # Detained/withdrawn students are flagged for admin review.
    promotable     = []   # Will be pre-selected in the UI
    detained_list  = []   # Pre-deselected (special status)
    already_promoted = []
    graduated_skipped = []

    for e in all_enrollments:
        s = e.student
        if not s:
            continue

        info = {
            "student_id":        s.id,
            "name":              s.name,
            "usn":               s.effective_usn,
            "section":           e.section or s.section or "",
            "enrollment_status": e.status,
            "enrollment_id":     e.id,
        }

        if s.status == StudentStatus.GRADUATED:
            graduated_skipped.append({**info, "skip_reason": "Already graduated."})
        elif dest and s.id in already_in_dest:
            already_promoted.append({**info, "skip_reason": "Already in destination period."})
        elif e.status in (EnrollmentStatus.DETAINED, EnrollmentStatus.WITHDRAWN):
            detained_list.append({**info, "skip_reason": f"Status: {e.status}"})
        else:
            # enrolled OR completed — both can be promoted per Phase 6 policy
            promotable.append(info)

    year_back = _year_back_warning(source.semester_number)

    return jsonify({
        "success":            True,
        "source_period":      source.to_dict(),
        "dest_period":        dest_dict,
        "dest_will_be_created": dest_will_be_created,
        "next_sem_number":    next_sem_number,
        "promotable":         promotable,
        "detained":           detained_list,
        "already_promoted":   already_promoted,
        "graduated_skipped":  graduated_skipped,
        "year_back_warning":  year_back,
        "summary": {
            "promotable_count":      len(promotable),
            "detained_count":        len(detained_list),
            "already_promoted_count": len(already_promoted),
            "graduated_count":       len(graduated_skipped),
            "total_in_source":       len(all_enrollments),
        },
    })


@academic_bp.route("/api/promote", methods=["POST"])
@login_required
@admin_required
def api_promote():
    """
    Legacy promote endpoint — kept for backward compatibility.
    Promotes students with 'completed' enrollment status.
    New UI uses /api/promote/v2 which supports per-student hold-back reasons.
    """
    data             = request.get_json() or {}
    source_period_id = data.get("source_period_id")
    dest_period_id   = data.get("dest_period_id")
    student_ids      = data.get("student_ids")   # None = promote all eligible
    dest_section_override = (data.get("section") or "").strip().upper() or None

    if not source_period_id or not dest_period_id:
        return jsonify({"success": False,
                        "message": "source_period_id and dest_period_id are required."}), 400

    source = db.session.get(AcademicPeriod, source_period_id)
    dest   = db.session.get(AcademicPeriod, dest_period_id)

    if not source:
        return jsonify({"success": False, "message": "Source period not found."}), 404
    if not dest:
        return jsonify({"success": False, "message": "Destination period not found."}), 404
    if dest.status == PeriodStatus.CLOSED:
        return jsonify({"success": False, "message": "Cannot promote into a closed period."}), 400
    if dest.admission_batch != source.admission_batch:
        return jsonify({"success": False,
                        "message": "Source and destination must be in the same admission batch."}), 400
    if dest.semester_number != source.semester_number + 1:
        return jsonify({"success": False,
                        "message": f"Destination (Sem {dest.semester_number}) must be one semester "
                                   f"after source (Sem {source.semester_number})."}), 400

    q = StudentEnrollment.query.filter_by(
        academic_period_id=source_period_id,
        status=EnrollmentStatus.COMPLETED
    )
    if student_ids is not None:
        q = q.filter(StudentEnrollment.student_id.in_(student_ids))
    candidates = q.join(User).all()
    if not candidates:
        return jsonify({"success": False,
                        "message": "No completed students found for promotion."}), 400

    promoted = []
    skipped  = []
    errors   = []

    try:
        for src_enrollment in candidates:
            student = src_enrollment.student
            if not student:
                errors.append({"error": "Student record not found.", "enrollment_id": src_enrollment.id})
                continue
            if student.is_graduated:
                skipped.append({"student_id": student.id, "usn": student.effective_usn,
                                "reason": "Already graduated."})
                continue
            existing_dest = StudentEnrollment.query.filter_by(
                student_id=student.id, academic_period_id=dest_period_id
            ).first()
            if existing_dest:
                skipped.append({"student_id": student.id, "usn": student.effective_usn,
                                "reason": "Already enrolled in destination period."})
                continue
            db.session.add(StudentEnrollment(
                student_id         = student.id,
                academic_period_id = dest_period_id,
                status             = EnrollmentStatus.ENROLLED,
                section            = dest_section_override or src_enrollment.section or student.section,
                enrollment_date    = _ist_now().date(),
                notes              = f"Promoted from {source._auto_label()} by {current_user.name}",
            ))
            student.semester = dest.semester_number
            promoted.append({"student_id": student.id, "name": student.name, "usn": student.effective_usn})

        db.session.add(AcademicAuditLog(
            event_type       = "promotion",
            performed_by     = current_user.id,
            source_period_id = source_period_id,
            dest_period_id   = dest_period_id,
            description      = (
                f"[legacy] Promoted {len(promoted)} student(s) from "
                f"'{source._auto_label()}' to '{dest._auto_label()}'. "
                f"Skipped: {len(skipped)}. Errors: {len(errors)}."
            ),
            detail_json = json.dumps({"promoted": promoted, "skipped": skipped, "errors": errors}),
        ))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Promotion failed: {exc}",
                        "promoted": [], "skipped": [], "errors": [{"error": str(exc)}]}), 500

    return jsonify({
        "success":  True,
        "promoted": promoted,
        "skipped":  skipped,
        "errors":   errors,
        "message":  (
            f"Promoted {len(promoted)} student(s) to {dest._auto_label()}. "
            f"{len(skipped)} skipped. {len(errors)} error(s)."
        ),
    })


@academic_bp.route("/api/promote/v2", methods=["POST"])
@login_required
@admin_required
def api_promote_v2():
    """
    PHASE 4 & 5 — Enhanced bulk-promotion with per-student hold-back reasons.

    Supports the full admin workflow:
      1. Promote selected students atomically to the destination period.
      2. Record individual hold-back reasons for un-selected students.
      3. Prevent Sem 8 → Sem 9 transitions.
      4. Prevent double-click duplicate submissions via idempotency_token.
      5. Preserve all source enrollments, attendance, and marks history.

    Phase 6: Students are NOT blocked for failed subjects or low attendance.
    Phase 7: Year Back is an admin warning only — it does NOT auto-block students.

    Expected body:
    {
      "source_period_id": int,
      "dest_period_id":   int,
      "promote_ids":      [int, ...],   // student_ids to promote
      "holdbacks": [
        {"student_id": int, "reason_code": str, "notes": str}
      ],
      "year_back_acknowledged": bool,   // required if year_back_warning applies
      "idempotency_token":       str,   // client-generated UUID; prevents duplicate execution
      "mark_source_completed":   bool   // default: true
    }
    """
    data             = request.get_json() or {}
    source_period_id = data.get("source_period_id")
    # dest_period_id is no longer required from client — server auto-resolves it
    promote_ids      = data.get("promote_ids", [])     # list of student IDs to promote
    holdbacks        = data.get("holdbacks", [])        # [{student_id, reason_code, notes}]
    year_back_ack    = data.get("year_back_acknowledged", False)
    idem_token       = (data.get("idempotency_token") or "").strip()
    mark_completed   = data.get("mark_source_completed", True)

    # ── Idempotency check ──────────────────────────────────────────────────────
    if idem_token:
        if idem_token in _promotion_tokens:
            prev = _promotion_tokens[idem_token]
            return jsonify({**prev, "duplicate": True,
                            "message": "Duplicate request: this promotion was already executed."})

    # ── Basic validation ──────────────────────────────────────────────────────
    if not source_period_id:
        return jsonify({"success": False, "message": "source_period_id is required."}), 400
    if not promote_ids and not holdbacks:
        return jsonify({"success": False,
                        "message": "promote_ids or holdbacks (or both) must be provided."}), 400

    source = db.session.get(AcademicPeriod, source_period_id)
    if not source:
        return jsonify({"success": False, "message": "Source period not found."}), 404

    # ── Sem 8 guard ───────────────────────────────────────────────────────────
    if source.semester_number >= 8:
        return jsonify({"success": False,
                        "message": "Semester 8 is the final semester. Use the Graduation workflow.",
                        "graduation_required": True}), 400

    # ── Auto-resolve / create destination period ───────────────────────────────
    dest, dest_auto_created, dest_error = _get_or_create_dest_period(source)
    if dest_error:
        return jsonify({"success": False, "message": dest_error}), 400

    # ── Year Back acknowledgment check ────────────────────────────────────────
    # If the source is Sem 4 (boundary), require the admin to explicitly
    # acknowledge the Year Back warning before executing.
    if source.semester_number == YEAR_BACK_BOUNDARY_SEM and not year_back_ack:
        return jsonify({
            "success": False,
            "message": (
                "Year Back review is required before promoting Semester 4 students "
                "to Semester 5. Please review students' first-year subject status and "
                "acknowledge the Year Back warning in the UI before proceeding."
            ),
            "year_back_required": True,
        }), 400

    # ── Build validated promote set ────────────────────────────────────────────
    # Re-validate each student_id against the source period enrollment.
    # Do NOT trust client-submitted IDs blindly.
    source_enrollments = {
        e.student_id: e
        for e in StudentEnrollment.query.filter_by(academic_period_id=source_period_id).all()
    }
    dest_student_ids = {
        e.student_id
        for e in StudentEnrollment.query.filter_by(academic_period_id=dest.id).all()
    }

    promoted   = []
    held_back  = []
    skipped    = []
    errors     = []

    try:
        # ── Promotions ────────────────────────────────────────────────────────
        for sid in promote_ids:
            src_enrollment = source_enrollments.get(sid)
            if not src_enrollment:
                errors.append({"student_id": sid,
                               "error": f"Student {sid} is not enrolled in the source period."})
                continue

            student = src_enrollment.student
            if not student:
                errors.append({"student_id": sid, "error": "Student record not found."})
                continue
            if student.status == StudentStatus.GRADUATED:
                skipped.append({"student_id": sid, "usn": student.effective_usn,
                                "reason": "Graduated students cannot be promoted."})
                continue
            if sid in dest_student_ids:
                skipped.append({"student_id": sid, "usn": student.effective_usn,
                                "reason": "Already enrolled in destination period."})
                continue

            # Mark source enrollment as completed
            if mark_completed and src_enrollment.status == EnrollmentStatus.ENROLLED:
                src_enrollment.status = EnrollmentStatus.COMPLETED
                src_enrollment.completion_date = _ist_now().date()

            # Create destination enrollment
            db.session.add(StudentEnrollment(
                student_id         = sid,
                academic_period_id = dest.id,
                status             = EnrollmentStatus.ENROLLED,
                section            = src_enrollment.section or student.section,
                enrollment_date    = _ist_now().date(),
                notes              = f"Promoted from {source._auto_label()} by {current_user.name}",
            ))

            # Update users.semester for convenience (non-authoritative; enrollment is source of truth)
            student.semester = dest.semester_number

            promoted.append({"student_id": sid, "name": student.name, "usn": student.effective_usn})

            # Per-student audit entry
            db.session.add(AcademicAuditLog(
                event_type       = "promotion",
                performed_by     = current_user.id,
                student_id       = sid,
                source_period_id = source_period_id,
                dest_period_id   = dest.id,
                description      = (
                    f"Student {student.effective_usn} ({student.name}) promoted "
                    f"from {source._auto_label()} to {dest._auto_label()}."
                ),
            ))

        # ── Hold-backs ────────────────────────────────────────────────────────
        # Phase 5: Record hold-back decision without touching source enrollment.
        VALID_REASON_CODES = {
            "year_back_review",
            "administrative_decision",
            "enrollment_issue",
            "subject_backlog",
            "fee_clearance",
            "other",
        }
        for hb in holdbacks:
            sid         = hb.get("student_id")
            reason_code = (hb.get("reason_code") or "other").strip()
            notes       = (hb.get("notes") or "").strip()

            if reason_code not in VALID_REASON_CODES:
                reason_code = "other"

            if not notes:
                errors.append({"student_id": sid,
                               "error": "Hold-back requires written notes."})
                continue

            src_enrollment = source_enrollments.get(sid)
            student = src_enrollment.student if src_enrollment else db.session.get(User, sid)
            if not student:
                errors.append({"student_id": sid, "error": "Student not found for hold-back."})
                continue

            # Source enrollment status remains unchanged (enrolled/completed).
            # Just record the hold-back decision in the audit log.
            db.session.add(AcademicAuditLog(
                event_type       = "enrollment_status_change",
                performed_by     = current_user.id,
                student_id       = sid,
                source_period_id = source_period_id,
                dest_period_id   = dest.id,
                description      = (
                    f"Hold-back: {student.effective_usn} ({student.name}) was NOT promoted "
                    f"to {dest._auto_label()}. Reason: [{reason_code}] {notes}"
                ),
                reason = f"[{reason_code}] {notes}",
            ))
            held_back.append({
                "student_id":  sid,
                "name":        student.name,
                "usn":         student.effective_usn,
                "reason_code": reason_code,
                "notes":       notes,
            })

        # ── Operation-level audit entry ───────────────────────────────────────
        auto_note = " [Destination period auto-created]" if dest_auto_created else ""
        db.session.add(AcademicAuditLog(
            event_type       = "promotion",
            performed_by     = current_user.id,
            source_period_id = source_period_id,
            dest_period_id   = dest.id,
            description      = (
                f"Bulk promotion: {len(promoted)} promoted, {len(held_back)} held back, "
                f"{len(skipped)} skipped, {len(errors)} errors. "
                f"{source._auto_label()} → {dest._auto_label()}.{auto_note}"
            ),
            detail_json = json.dumps({
                "promoted":  promoted,
                "held_back": held_back,
                "skipped":   skipped,
                "errors":    errors,
                "idempotency_token": idem_token,
            }),
        ))

        db.session.commit()

    except IntegrityError as exc:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": f"Integrity error (possible duplicate enrollment): {exc}",
        }), 409
    except Exception as exc:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": f"Promotion failed and was fully rolled back: {exc}",
            "promoted": [], "held_back": [], "skipped": [], "errors": [{"error": str(exc)}],
        }), 500

    result = {
        "success":          True,
        "promoted":         promoted,
        "held_back":        held_back,
        "skipped":          skipped,
        "errors":           errors,
        "dest_auto_created": dest_auto_created,
        "message": (
            f"Promotion complete: {len(promoted)} promoted, "
            f"{len(held_back)} held back, {len(skipped)} skipped, "
            f"{len(errors)} error(s). Destination: {dest._auto_label()}."
        ),
    }
    # Cache result for idempotency
    if idem_token:
        _promotion_tokens[idem_token] = result
    return jsonify(result)


@academic_bp.route("/api/enrollment/<int:enrollment_id>/status", methods=["PATCH"])
@login_required
@admin_required
def api_update_enrollment_status(enrollment_id):
    """Update a student's status in a specific enrollment (complete, detain, withdraw)."""
    enrollment = db.get_or_404(StudentEnrollment, enrollment_id)
    data       = request.get_json() or {}
    new_status = data.get("status")
    reason     = (data.get("reason") or "").strip()

    allowed = {EnrollmentStatus.ENROLLED, EnrollmentStatus.COMPLETED,
               EnrollmentStatus.DETAINED, EnrollmentStatus.WITHDRAWN}
    if new_status not in allowed:
        return jsonify({"success": False,
                        "message": f"Invalid status. Allowed: {sorted(allowed)}"}), 400

    old_status           = enrollment.status
    enrollment.status    = new_status
    enrollment.notes     = (enrollment.notes or "") + f"\n[{_ist_now()}] Status → {new_status}: {reason}"
    if new_status == EnrollmentStatus.COMPLETED:
        enrollment.completion_date = _ist_now().date()

    db.session.add(AcademicAuditLog(
        event_type   = "enrollment_status_change",
        performed_by = current_user.id,
        student_id   = enrollment.student_id,
        source_period_id = enrollment.academic_period_id,
        description  = (f"Enrollment #{enrollment_id} status changed from "
                        f"'{old_status}' to '{new_status}' for student "
                        f"{enrollment.student.effective_usn if enrollment.student else enrollment.student_id}"),
        reason       = reason,
    ))
    db.session.commit()
    return jsonify({"success": True, "enrollment": enrollment.to_dict()})


# ── Admin API: Graduation ──────────────────────────────────────────────────────

@academic_bp.route("/api/graduation/check/<int:student_id>", methods=["GET"])
@login_required
@admin_required
def api_graduation_check(student_id):
    """
    Verify a student's readiness for graduation.
    Returns all semester enrollments and whether Sem 8 is completed.
    Does NOT automatically graduate — admin must explicitly confirm.
    """
    student = db.session.get(User, student_id)
    if not student or not student.is_student:
        return jsonify({"success": False, "message": "Student not found."}), 404

    if student.is_graduated:
        return jsonify({
            "success":       True,
            "already_graduated": True,
            "graduated_at":  student.graduated_at.isoformat() if student.graduated_at else None,
            "student":       student.to_dict(),
        })

    # Fetch all semester enrollments
    enrollments = (StudentEnrollment.query
                   .filter_by(student_id=student_id)
                   .join(AcademicPeriod)
                   .order_by(AcademicPeriod.semester_number)
                   .all())

    sem8_completed = any(
        e.status == EnrollmentStatus.COMPLETED and e.period.semester_number == 8
        for e in enrollments
    )

    return jsonify({
        "success":       True,
        "student":       student.to_dict(),
        "enrollments":   [e.to_dict() for e in enrollments],
        "sem8_completed": sem8_completed,
        "total_semesters_completed": sum(
            1 for e in enrollments if e.status == EnrollmentStatus.COMPLETED
        ),
        "graduation_requirements": {
            "sem8_completed": {
                "met":         sem8_completed,
                "description": "Student must have Semester 8 enrollment marked as Completed.",
                "note":        "Verify manually: University exams, fee clearance, project submission, etc."
            }
        }
    })


@academic_bp.route("/api/graduation/confirm", methods=["POST"])
@login_required
@admin_required
def api_confirm_graduation():
    """
    Confirm graduation for a student.
    Requires explicit admin confirmation.
    Preserves all historical records — does NOT delete any data.
    Prevents duplicate graduation.
    """
    data       = request.get_json() or {}
    student_id = data.get("student_id")
    reason     = (data.get("reason") or "Admin confirmed graduation.").strip()

    if not student_id:
        return jsonify({"success": False, "message": "student_id is required."}), 400

    student = db.session.get(User, student_id)
    if not student or not student.is_student:
        return jsonify({"success": False, "message": "Student not found."}), 404

    # Prevent duplicate graduation
    if student.is_graduated:
        return jsonify({
            "success": False,
            "message": f"Student {student.effective_usn} is already marked as graduated "
                       f"(on {student.graduated_at.strftime('%d %b %Y') if student.graduated_at else 'unknown date'})."
        }), 409

    # Record graduation
    now = _ist_now()
    student.status       = StudentStatus.GRADUATED
    student.graduated_at = now

    db.session.add(AcademicAuditLog(
        event_type   = "graduation",
        performed_by = current_user.id,
        student_id   = student_id,
        description  = f"Student {student.effective_usn} ({student.name}) graduated.",
        reason       = reason,
    ))

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Graduation failed: {exc}"}), 500

    return jsonify({
        "success":      True,
        "message":      f"Student {student.name} ({student.effective_usn}) has been marked as graduated.",
        "student":      student.to_dict(),
        "graduated_at": now.strftime("%d %b %Y, %I:%M %p IST"),
    })


# ── Admin API: New Batch ────────────────────────────────────────────────────────

@academic_bp.route("/api/batch/create", methods=["POST"])
@login_required
@admin_required
def api_create_batch():
    """
    Create a new admission batch:
    1. Create Semester 1 academic period for the batch.
    2. Register students with unique USNs.
    3. Enroll them in Sem 1.
    Does NOT touch existing batches or their records.
    """
    data         = request.get_json() or {}
    batch        = (data.get("admission_batch") or "").strip()
    academic_year = (data.get("academic_year") or "").strip()
    dept         = (data.get("department") or "CSE").strip()
    students_data = data.get("students", [])   # list of {usn, name, email, section, ...}
    start_date   = data.get("start_date")
    end_date     = data.get("end_date")

    if not batch or not academic_year:
        return jsonify({"success": False,
                        "message": "admission_batch and academic_year are required."}), 400

    if not students_data:
        # Create period only, no students yet
        pass

    # ── Step 1: Create Sem 1 period (or find existing) ─────────────────────────
    period = AcademicPeriod.query.filter_by(
        admission_batch=batch, semester_number=1, academic_year=academic_year
    ).first()
    period_created = False
    if not period:
        try:
            sd = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None
            ed = datetime.strptime(end_date,   "%Y-%m-%d").date() if end_date   else None
        except (ValueError, TypeError):
            sd = ed = None

        period = AcademicPeriod(
            admission_batch = batch,
            semester_number = 1,
            academic_year   = academic_year,
            department      = dept,
            status          = PeriodStatus.ACTIVE,
            start_date      = sd,
            end_date        = ed,
            created_by      = current_user.id,
        )
        db.session.add(period)
        db.session.flush()   # get period.id without final commit
        period_created = True
        db.session.add(AcademicAuditLog(
            event_type   = "batch_created",
            performed_by = current_user.id,
            description  = f"Created new batch: Batch {batch} Sem 1 ({academic_year})",
        ))

    # ── Step 2: Register students ───────────────────────────────────────────────
    registered = []
    skipped    = []
    errors     = []

    for s_data in students_data:
        raw_usn = (s_data.get("usn") or s_data.get("username") or "").strip()
        if not raw_usn:
            errors.append({"data": s_data, "error": "USN is required."})
            continue

        usn = normalize_usn(raw_usn)

        # Global USN uniqueness check (includes graduated students)
        if User.query.filter(
            (User.usn == usn) | (User.username == usn)
        ).first():
            errors.append({"usn": usn, "error": f"USN {usn} is already assigned to another student."})
            continue

        name    = (s_data.get("name") or "").strip()
        email   = (s_data.get("email") or "").strip()
        section = (s_data.get("section") or "A").strip().upper()
        password = s_data.get("password") or usn  # default password = USN (student must change)

        if not name or not email:
            errors.append({"usn": usn, "error": "name and email are required."})
            continue
        if User.query.filter_by(email=email).first():
            errors.append({"usn": usn, "error": f"Email {email} is already registered."})
            continue

        from extensions import bcrypt
        new_student = User(
            username        = usn,
            email           = email,
            name            = name,
            usn             = usn,
            roll_number     = usn,
            role            = "student",
            semester        = 1,
            section         = section,
            department      = dept,
            admission_batch = batch,
            status          = StudentStatus.ACTIVE,
            password_hash   = bcrypt.generate_password_hash(password).decode("utf-8"),
        )
        db.session.add(new_student)
        db.session.flush()   # get new_student.id

        # Enroll in Sem 1 period
        enrollment = StudentEnrollment(
            student_id          = new_student.id,
            academic_period_id  = period.id,
            status              = EnrollmentStatus.ENROLLED,
            section             = section,
            enrollment_date     = _ist_now().date(),
        )
        db.session.add(enrollment)
        registered.append({"usn": usn, "name": name, "student_id": new_student.id})

    # Audit
    db.session.add(AcademicAuditLog(
        event_type   = "batch_created",
        performed_by = current_user.id,
        description  = (
            f"Batch {batch} ({academic_year}) student registration: "
            f"{len(registered)} registered, {len(skipped)} skipped, {len(errors)} errors."
        ),
        detail_json  = json.dumps({"registered": registered, "errors": errors}),
    ))

    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Registration failed (duplicate USN or email): {exc}"}), 409
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Registration failed: {exc}"}), 500

    return jsonify({
        "success":        True,
        "period":         period.to_dict(),
        "period_created": period_created,
        "registered":     registered,
        "skipped":        skipped,
        "errors":         errors,
        "message": (
            f"Batch {batch} ({academic_year}): {len(registered)} student(s) registered "
            f"and enrolled in Sem 1. {len(errors)} error(s)."
        ),
    }), 201


# ── Admin API: USN Correction ─────────────────────────────────────────────────

@academic_bp.route("/api/usn-correction", methods=["POST"])
@login_required
@admin_required
def api_usn_correction():
    """
    Admin-only USN correction with full audit trail.
    - Validates new USN.
    - Checks uniqueness.
    - Updates usn + username + roll_number atomically.
    - Logs old value, new value, admin, timestamp, and reason.
    """
    data       = request.get_json() or {}
    student_id = data.get("student_id")
    new_usn_raw = (data.get("new_usn") or "").strip()
    reason     = (data.get("reason") or "").strip()

    if not student_id:
        return jsonify({"success": False, "message": "student_id is required."}), 400
    if not new_usn_raw:
        return jsonify({"success": False, "message": "new_usn is required."}), 400
    if not reason:
        return jsonify({"success": False, "message": "A reason for the USN correction is required."}), 400

    new_usn = normalize_usn(new_usn_raw)
    student = db.session.get(User, student_id)
    if not student or not student.is_student:
        return jsonify({"success": False, "message": "Student not found."}), 404

    old_usn = student.usn or student.username

    # Uniqueness check
    conflict = User.query.filter(
        (User.usn == new_usn) | (User.username == new_usn)
    ).filter(User.id != student_id).first()
    if conflict:
        return jsonify({
            "success": False,
            "message": f"USN '{new_usn}' is already assigned to another user (id={conflict.id})."
        }), 409

    # Apply correction atomically
    student.usn         = new_usn
    student.username    = new_usn
    student.roll_number = new_usn

    db.session.add(AcademicAuditLog(
        event_type   = "usn_correction",
        performed_by = current_user.id,
        student_id   = student_id,
        old_usn      = old_usn,
        new_usn      = new_usn,
        description  = f"USN corrected: {old_usn} → {new_usn} for student '{student.name}'",
        reason       = reason,
    ))

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"success": False, "message": "USN correction failed (conflict)."}), 409

    return jsonify({
        "success": True,
        "old_usn": old_usn,
        "new_usn": new_usn,
        "student": student.to_dict(),
        "message": f"USN corrected from '{old_usn}' to '{new_usn}' successfully.",
    })


# ── Admin API: Search and Graduated Students ───────────────────────────────────

@academic_bp.route("/api/students/search", methods=["GET"])
@login_required
@admin_required
def api_student_search():
    """Search students by USN, name, batch, or status."""
    q_str  = (request.args.get("q") or "").strip()
    batch  = request.args.get("batch")
    status = request.args.get("status")
    page   = request.args.get("page", 1, type=int)
    per_pg = min(request.args.get("per_page", 50, type=int), 200)

    q = User.query.filter_by(role="student")
    if q_str:
        q = q.filter(
            User.name.ilike(f"%{q_str}%") |
            User.usn.ilike(f"%{q_str}%") |
            User.username.ilike(f"%{q_str}%") |
            User.roll_number.ilike(f"%{q_str}%")
        )
    if batch:  q = q.filter_by(admission_batch=batch)
    if status: q = q.filter_by(status=status)

    paginated = q.order_by(User.name).paginate(page=page, per_page=per_pg, error_out=False)
    return jsonify({
        "success": True,
        "students": [s.to_dict() for s in paginated.items],
        "total": paginated.total,
        "page":  paginated.page,
        "pages": paginated.pages,
    })


@academic_bp.route("/api/students/<int:student_id>/history", methods=["GET"])
@login_required
@admin_required
def api_admin_student_history(student_id):
    """Admin: Full academic history for any student."""
    student = db.session.get(User, student_id)
    if not student or not student.is_student:
        return jsonify({"success": False, "message": "Student not found."}), 404
    return jsonify({"success": True, "student": student.to_dict(),
                    "history": _build_history(student)})


# ── Admin API: Audit Log ───────────────────────────────────────────────────────

@academic_bp.route("/api/audit", methods=["GET"])
@login_required
@admin_required
def api_audit_log():
    """Return paginated audit log records."""
    page     = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 50, type=int), 200)
    event    = request.args.get("event_type")
    q = AcademicAuditLog.query.order_by(AcademicAuditLog.created_at.desc())
    if event:
        q = q.filter_by(event_type=event)
    paginated = q.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        "success": True,
        "logs":  [log.to_dict() for log in paginated.items],
        "total": paginated.total,
        "page":  paginated.page,
        "pages": paginated.pages,
    })


# ── Student API: My Academic History ─────────────────────────────────────────

@academic_bp.route("/api/my-history", methods=["GET"])
@login_required
def api_my_history():
    """
    Student: Return own academic history across all semesters.
    Security:
      - Only the authenticated student can call this.
      - Uses current_user.id — never a submitted student ID or USN.
      - Each academic period is validated against the student's enrollments.
    """
    if not current_user.is_student:
        return jsonify({"success": False, "message": "Students only."}), 403

    history = _build_history(current_user)
    return jsonify({
        "success": True,
        "student": current_user.to_dict(),
        "history": history,
    })


@academic_bp.route("/api/my-history/period/<int:period_id>", methods=["GET"])
@login_required
def api_my_history_period(period_id):
    """
    Student: Detailed academic data for one of their own semester periods.
    Security: validates that this student is enrolled in the requested period.
              A student cannot access another student's period by manipulating the URL.
    """
    if not current_user.is_student:
        return jsonify({"success": False, "message": "Students only."}), 403

    # ── Authorization: student must have an enrollment in this period ──────────
    enrollment = StudentEnrollment.query.filter_by(
        student_id=current_user.id,
        academic_period_id=period_id
    ).first()

    if not enrollment:
        return jsonify({
            "success": False,
            "message": "You do not have an enrollment record for this academic period."
        }), 403

    period = enrollment.period
    if not period:
        return jsonify({"success": False, "message": "Academic period not found."}), 404

    # ── Attendance ─────────────────────────────────────────────────────────────
    att_summaries = AttendanceSummary.query.filter_by(
        student_id=current_user.id,
        semester=period.semester_number,
    ).all()

    # ── Internal Marks ─────────────────────────────────────────────────────────
    marks = InternalMarks.query.filter_by(
        student_id=current_user.id,
        semester=period.semester_number,
    ).all()

    return jsonify({
        "success":    True,
        "period":     period.to_dict(),
        "enrollment": enrollment.to_dict(),
        "attendance": [s.to_dict() for s in att_summaries],
        "marks":      [m.to_dict() for m in marks],
        "read_only":  period.status == PeriodStatus.CLOSED,
    })


# ── Helper: Build Full Academic History ────────────────────────────────────────

def _build_history(student: User) -> list:
    """
    Build a list of semester history entries for a student.
    Each entry covers one AcademicPeriod the student was enrolled in,
    plus a summary of their attendance and marks.

    Legacy records (no enrollment rows) are shown as the current semester
    based on users.semester, with records fetched by semester number only.
    """
    history = []

    # ── Enrolled periods (new system) ──────────────────────────────────────────
    enrollments = (StudentEnrollment.query
                   .filter_by(student_id=student.id)
                   .join(AcademicPeriod)
                   .order_by(AcademicPeriod.semester_number, AcademicPeriod.academic_year)
                   .all())

    enrolled_semester_numbers = set()

    for enrollment in enrollments:
        period = enrollment.period
        if not period:
            continue
        enrolled_semester_numbers.add(period.semester_number)

        # Attendance: query by student + semester number
        att_summaries = AttendanceSummary.query.filter_by(
            student_id=student.id, semester=period.semester_number
        ).all()

        # Marks: query by student + semester number
        marks = InternalMarks.query.filter_by(
            student_id=student.id, semester=period.semester_number
        ).all()

        avg_att = (
            round(sum(s.attendance_pct for s in att_summaries) / len(att_summaries), 1)
            if att_summaries else None
        )

        history.append({
            "semester_number":    period.semester_number,
            "academic_year":      period.academic_year,
            "admission_batch":    period.admission_batch,
            "period_id":          period.id,
            "period_label":       period._auto_label(),
            "period_status":      period.status,
            "enrollment_status":  enrollment.status,
            "section":            enrollment.section or student.section,
            "is_current":         enrollment.status == EnrollmentStatus.ENROLLED,
            "is_completed":       enrollment.status == EnrollmentStatus.COMPLETED,
            "read_only":          period.status == PeriodStatus.CLOSED,
            "has_attendance":     bool(att_summaries),
            "has_marks":          bool(marks),
            "avg_attendance":     avg_att,
            "subject_count":      len(att_summaries),
            "source":             "enrollment",
        })

    # ── Legacy fallback: show current semester records even without enrollment ──
    current_sem = student.semester
    if current_sem and current_sem not in enrolled_semester_numbers:
        att_summaries = AttendanceSummary.query.filter_by(
            student_id=student.id, semester=current_sem
        ).all()
        marks = InternalMarks.query.filter_by(
            student_id=student.id, semester=current_sem
        ).all()
        avg_att = (
            round(sum(s.attendance_pct for s in att_summaries) / len(att_summaries), 1)
            if att_summaries else None
        )
        if att_summaries or marks:
            history.append({
                "semester_number":   current_sem,
                "academic_year":     None,
                "admission_batch":   student.admission_batch,
                "period_id":         None,
                "period_label":      f"Semester {current_sem} (Legacy / Pre-migration)",
                "period_status":     "legacy",
                "enrollment_status": "legacy",
                "section":           student.section,
                "is_current":        True,
                "is_completed":      False,
                "read_only":         False,
                "has_attendance":    bool(att_summaries),
                "has_marks":         bool(marks),
                "avg_attendance":    avg_att,
                "subject_count":     len(att_summaries),
                "source":            "legacy",
            })

    history.sort(key=lambda x: x["semester_number"])
    return history
