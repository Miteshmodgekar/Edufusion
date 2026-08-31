"""
Attendance Blueprint — Excel Upload, Analysis, Defaulter Detection,
What-If Simulator.
"""

import io
import os
import calendar
import pandas as pd
from datetime import datetime, date as _date, timezone, timedelta
from flask import Blueprint, request, jsonify, current_app, render_template, send_file
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from sqlalchemy import func
from extensions import db
from models.attendance import AttendanceRecord, AttendanceSummary, SheetRegistry
from models.user import User
from config import Config
from utils.live_attendance import (
    read_live_attendance_for_student, get_live_attendance_path,
    get_missed_subjects_on_date, get_missed_dates_for_student,
    get_month_attendance_for_student,
)

attendance_bp = Blueprint("attendance", __name__)

ALLOWED = {"xlsx", "xls", "csv"}

_IST = timezone(timedelta(hours=5, minutes=30))

def _ist_now():
    """Return current datetime in IST (UTC+5:30)."""
    return datetime.now(_IST).replace(tzinfo=None)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED


# ── Upload & Analyze ─────────────────────────────────────────────────────────

@attendance_bp.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    """Faculty uploads an Excel attendance sheet."""
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Access denied."}), 403

    if request.method == "GET":
        return render_template("attendance/upload.html")

    if "file" not in request.files:
        return jsonify({"success": False, "message": "No file provided."}), 400

    file = request.files["file"]
    if file.filename == "" or not allowed_file(file.filename):
        return jsonify({"success": False, "message": "Invalid file type. Use .xlsx or .csv"}), 400

    filename  = secure_filename(file.filename)
    save_path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
    file.save(save_path)

    subject    = request.form.get("subject", "Unknown")
    semester   = int(request.form.get("semester", 1))
    section    = request.form.get("section", "A")
    sheet_name = request.form.get("sheet_name") or None   # NEW — which tab to read

    result = process_attendance_excel(save_path, subject, semester, section,
                                      sheet_name=sheet_name)

    # ── Register this sheet in SheetRegistry ─────────────────────────────────
    if result.get("success"):
        try:
            reg = SheetRegistry.query.filter_by(
                subject=subject, semester=semester, section=section
            ).first()
            if not reg:
                reg = SheetRegistry(
                    subject=subject, semester=semester, section=section,
                    filename=filename, filepath=save_path,
                    uploaded_by=current_user.id,
                )
                db.session.add(reg)
            else:
                reg.filename    = filename
                reg.filepath    = save_path
                reg.uploaded_by = current_user.id
                from datetime import datetime
                reg.uploaded_at = _ist_now()
            reg.total_rows = result.get("total_students", 0)
            db.session.commit()
            result["sheet_info"] = reg.to_dict()

            # ── Trigger Notifications for Defaulters ─────────────────────────
            try:
                from utils.notifications import notify_low_attendance
                from sqlalchemy import func as sqlfunc
                for def_data in result.get("defaulters", []):
                    student = User.query.filter(
                        sqlfunc.upper(User.roll_number) == def_data["roll_number"].upper(),
                        User.role == "student"
                    ).first()
                    if student:
                        summary = AttendanceSummary.query.filter_by(
                            student_id=student.id, subject=subject, semester=semester
                        ).first()
                        if summary:
                            notify_low_attendance(student, summary)
            except Exception as e:
                import logging
                logging.error(f"Error triggering notifications: {e}")
                
        except Exception:
            pass

    return jsonify(result)


@attendance_bp.route("/api/preview-sheets", methods=["POST"])
@login_required
def api_preview_sheets():
    """
    Upload a file temporarily and return its sheet names + column preview.
    Faculty uses this to pick the right sheet tab before full processing.
    """
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Access denied."}), 403

    if "file" not in request.files:
        return jsonify({"success": False, "message": "No file."}), 400

    file = request.files["file"]
    if not allowed_file(file.filename):
        return jsonify({"success": False, "message": "Invalid file type."}), 400

    filename  = secure_filename(file.filename)
    save_path = os.path.join(current_app.config["UPLOAD_FOLDER"], "_preview_" + filename)
    file.save(save_path)

    try:
        ext = filename.rsplit(".", 1)[1].lower()
        if ext == "csv":
            # CSV has no sheets
            df      = pd.read_csv(save_path)
            sheets  = [{"name": "(CSV — single sheet)",
                        "rows": len(df),
                        "cols": list(df.columns[:8]),
                        "looks_like_attendance": True}]
            return jsonify({"success": True, "sheets": sheets,
                            "filepath": save_path, "is_csv": True})

        # Excel — inspect every sheet tab
        xl      = pd.ExcelFile(save_path)
        sheets  = []
        for sname in xl.sheet_names:
            try:
                df   = xl.parse(sname, nrows=5)      # peek first 5 rows
                cols = [str(c).strip() for c in df.columns]
                # Heuristic: does this sheet look like attendance?
                has_roll = any(k in c.lower() for c in cols
                               for k in ["roll", "usn", "id", "reg"])
                has_name = any("name" in c.lower() for c in cols)
                looks    = has_roll or has_name
                sheets.append({
                    "name":                   sname,
                    "rows":                   int(xl.parse(sname).shape[0]),
                    "cols":                   cols[:10],
                    "looks_like_attendance":  looks,
                })
            except Exception as e:
                sheets.append({"name": sname, "rows": 0,
                               "cols": [], "error": str(e),
                               "looks_like_attendance": False})

        # Auto-suggest: prefer sheets flagged as attendance
        suggested = next(
            (s["name"] for s in sheets if s["looks_like_attendance"]), 
            sheets[0]["name"] if sheets else None
        )
        return jsonify({"success": True, "sheets": sheets,
                        "suggested": suggested, "filepath": save_path})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ── Live Sheet: faculty overwrite the single shared workbook ────────────────

LIVE_SHEET_NAME = "live_attendance.xlsx"


@attendance_bp.route("/live-upload", methods=["GET", "POST"])
@login_required
def live_upload():
    """
    Faculty/HOD/Admin push a fresh copy of the ONE shared attendance
    workbook. No processing happens here — the file is simply saved
    (overwriting the previous version) and students read it live the
    next time they open 'My Attendance'.
    """
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Access denied."}), 403

    if request.method == "GET":
        live_path = os.path.join(current_app.config["UPLOAD_FOLDER"], LIVE_SHEET_NAME)
        last_synced = None
        if os.path.exists(live_path):
            last_synced = datetime.fromtimestamp(
                os.path.getmtime(live_path)
            ).strftime("%d %b %Y, %I:%M %p")
        return render_template("attendance/live_upload.html", last_synced=last_synced)

    if "file" not in request.files:
        return jsonify({"success": False, "message": "No file provided."}), 400

    file = request.files["file"]
    if file.filename == "" or not allowed_file(file.filename):
        return jsonify({"success": False, "message": "Invalid file type. Use .xlsx, .xls or .csv"}), 400
    if file.filename.rsplit(".", 1)[1].lower() not in ("xlsx", "xls"):
        return jsonify({"success": False, "message": "The live sheet must be an Excel workbook (.xlsx/.xls) — "
                                                       "it needs multiple subject tabs."}), 400

    save_path = os.path.join(current_app.config["UPLOAD_FOLDER"], LIVE_SHEET_NAME)
    file.save(save_path)

    # Quick sanity check: make sure at least one sheet parses as a register.
    from utils.live_attendance import _parse_subject_sheet
    try:
        xl = pd.ExcelFile(save_path)
        found_any = False
        subjects_seen = []
        for sname in xl.sheet_names:
            rows = _parse_subject_sheet(xl, sname, Config.ATTENDANCE_THRESHOLD)
            if rows:
                found_any = True
                subjects_seen.append(sname)
                # Track who/when this subject tab was last synced — used by the
                # student dashboard widget to show "Updated by ...".
                reg = SheetRegistry.query.filter_by(
                    subject=sname, filename=LIVE_SHEET_NAME
                ).first()
                if reg:
                    reg.uploaded_by = current_user.id
                    reg.total_rows  = len(rows)
                    reg.uploaded_at = _ist_now()
                else:
                    reg = SheetRegistry(
                        subject     = sname,
                        semester    = 0,     # sentinel: live sheet matches by USN, not semester
                        section     = None,
                        filename    = LIVE_SHEET_NAME,
                        filepath    = save_path,
                        uploaded_by = current_user.id,
                        total_rows  = len(rows),
                    )
                    db.session.add(reg)
        db.session.commit()

        if not found_any:
            return jsonify({"success": False,
                             "message": "Uploaded, but no sheet tab looks like an attendance register "
                                        "(need a Roll No/USN column and a Student Name column)."}), 200
        return jsonify({"success": True,
                         "message": f"Live sheet updated — {len(subjects_seen)} subject tab(s) detected.",
                         "subjects": subjects_seen})
    except Exception as e:
        return jsonify({"success": False, "message": f"File saved but could not be read: {e}"}), 200


def process_attendance_excel(filepath, subject, semester, section, sheet_name=None):
    """
    Reads Excel/CSV and computes attendance per student.
    sheet_name: specific Excel tab to read (None = auto-detect best tab)
    """
    try:
        ext = filepath.rsplit(".", 1)[1].lower()
        if ext == "csv":
            df = pd.read_csv(filepath)
        else:
            # ── Sheet selection logic ────────────────────────────────────
            xl = pd.ExcelFile(filepath)
            if sheet_name and sheet_name in xl.sheet_names:
                # Use the exact tab the faculty selected
                df = xl.parse(sheet_name)
            else:
                # Auto-detect: find the first tab that has roll/name columns
                df = None
                for sname in xl.sheet_names:
                    try:
                        _df  = xl.parse(sname)
                        cols = [str(c).lower().strip() for c in _df.columns]
                        if any(k in c for c in cols for k in ["roll","usn","id","reg","name"]):
                            df = _df
                            break
                    except Exception:
                        continue
                if df is None:
                    df = xl.parse(xl.sheet_names[0])  # fall back to first sheet

        # ── Normalise column names ──────────────────────────────────────────
        df.columns = df.columns.str.strip()

        def _find_col(keywords, fallback=None):
            for kw in keywords:
                for c in df.columns:
                    if kw in c.lower():
                        return c
            return fallback

        roll_col  = _find_col(["roll", "id", "usn", "reg"],  df.columns[0])
        name_col  = _find_col(["name", "student"],           df.columns[1] if len(df.columns) > 1 else df.columns[0])
        email_col = _find_col(["email", "mail"])
        sem_col   = _find_col(["sem", "semester"])
        sec_col   = _find_col(["sec", "section", "div"])
        dept_col  = _find_col(["dept", "department", "branch"])

        # Everything that is NOT an info column is treated as a date/class column
        info_cols = {c for c in [roll_col, name_col, email_col, sem_col, sec_col, dept_col] if c}
        date_cols = [c for c in df.columns if c not in info_cols]

        total_classes = len(date_cols)
        results    = []
        defaulters = []
        auto_created = []
        skipped_rows = []

        for idx, row in df.iterrows():
            try:
                roll = str(row[roll_col]).strip()
                name = str(row[name_col]).strip()

                # Skip completely empty or header-like rows
                if not roll or roll.lower() in ("nan", "roll no", "roll_no", "rollno", "usn", "id"):
                    continue

                # Count attendance
                attended = sum(
                    1 for d in date_cols
                    if str(row.get(d, "")).strip().upper() in ["P", "OD", "1", "YES"]
                )
                pct = round((attended / total_classes) * 100, 2) if total_classes > 0 else 0.0
                threshold = Config.ATTENDANCE_THRESHOLD
                is_def    = pct < threshold

                needed = 0
                if is_def and total_classes > 0:
                    needed = max(0, int((threshold * total_classes - 100 * attended) / (100 - threshold)) + 1)

                entry = {
                    "roll_number":      roll,
                    "name":             name,
                    "total_classes":    total_classes,
                    "classes_attended": attended,
                    "attendance_pct":   pct,
                    "is_defaulter":     is_def,
                    "required_classes": needed,
                    "status":           "⚠ DEFAULTER" if is_def else "✓ OK",
                    "db_saved":         False,
                    "auto_created":     False,
                }

                # ── Lookup student in DB ────────────────────────────────────
                from sqlalchemy import func as sqlfunc
                student = User.query.filter(
                    sqlfunc.upper(User.roll_number) == roll.upper(),
                    User.role == "student"
                ).first()

                # ── Auto-create student if not found ───────────────────────
                if not student:
                    try:
                        from extensions import bcrypt as _bcrypt

                        # Pull extra info from sheet row if available
                        row_email = str(row[email_col]).strip() if email_col and pd.notna(row.get(email_col)) else None
                        row_sem   = int(row[sem_col])           if sem_col  and pd.notna(row.get(sem_col))   else semester
                        row_sec   = str(row[sec_col]).strip()   if sec_col  and pd.notna(row.get(sec_col))   else section
                        row_dept  = str(row[dept_col]).strip()  if dept_col and pd.notna(row.get(dept_col))  else "CSE"

                        # Build a unique username / email
                        username  = roll.replace(" ", "").upper()
                        email     = row_email or f"{username.lower()}@student.cse.edu"

                        # Skip if username/email already taken (different roll stored)
                        if not User.query.filter(
                            (User.username == username) | (User.email == email)
                        ).first():
                            default_pwd = _bcrypt.generate_password_hash("student123").decode("utf-8")
                            student = User(
                                username      = username,
                                email         = email,
                                name          = name,
                                role          = "student",
                                roll_number   = roll,
                                semester      = row_sem,
                                section       = row_sec,
                                department    = row_dept,
                                password_hash = default_pwd,
                                is_active     = True,
                            )
                            db.session.add(student)
                            db.session.flush()   # get student.id without full commit
                            entry["auto_created"] = True
                            auto_created.append(roll)
                    except Exception:
                        pass   # auto-create failed silently; attendance still recorded in results

                # ── Save attendance summary ─────────────────────────────────
                if student:
                    try:
                        summary = AttendanceSummary.query.filter_by(
                            student_id=student.id, subject=subject, semester=semester
                        ).first()
                        if not summary:
                            summary = AttendanceSummary(
                                student_id=student.id,
                                subject=subject,
                                semester=semester,
                                section=section,
                            )
                            db.session.add(summary)
                        summary.total_classes    = total_classes
                        summary.classes_attended = attended
                        summary.attendance_pct   = pct
                        summary.is_defaulter     = is_def
                        summary.required_classes = needed
                        db.session.commit()
                        entry["db_saved"] = True
                    except Exception:
                        db.session.rollback()

                results.append(entry)
                if is_def:
                    defaulters.append(entry)

            except Exception as row_err:
                # Bad row — log and skip without crashing the whole upload
                skipped_rows.append({"row": int(idx) + 2, "reason": str(row_err)})
                continue

        db_saved_count = sum(1 for r in results if r.get("db_saved"))
        not_found      = [r["roll_number"] for r in results if not r.get("db_saved")]

        return {
            "success":          True,
            "subject":          subject,
            "semester":         semester,
            "section":          section,
            "total_students":   len(results),
            "total_defaulters": len(defaulters),
            "total_classes":    total_classes,
            "db_saved":         db_saved_count,
            "auto_created":     auto_created,       # NEW — rolls auto-registered
            "not_found_rolls":  not_found,
            "skipped_rows":     skipped_rows,        # NEW — rows that had errors
            "records":          results,
            "defaulters":       defaulters,
        }

    except Exception as e:
        return {"success": False, "message": f"Error processing file: {str(e)}"}



# ── Mark Attendance: faculty tick students directly, no Excel at all ────────
#
# This is the "default" replacement for daily Excel uploads. Faculty pick a
# subject/semester/section/date, get the class roster, tap Present/Absent/OD
# per student, and hit Save. It writes straight into AttendanceRecord (one
# row per student per date) and recomputes AttendanceSummary — the exact
# table /attendance/my already reads from as its DB fallback, so students
# see it immediately with zero file upload involved.

def _recompute_summary(student_id, subject, semester, section=None):
    """Rebuild AttendanceSummary for one student+subject+semester from
    every AttendanceRecord row currently on file for them."""
    records = AttendanceRecord.query.filter_by(
        student_id=student_id, subject=subject, semester=semester
    ).all()

    # OD days are excluded entirely — they don't count as present OR as a class
    counted  = [r for r in records if r.status in ("present", "absent")]
    total    = len(counted)
    attended = sum(1 for r in counted if r.status == "present")
    pct      = round((attended / total) * 100, 2) if total else 0.0
    threshold = Config.ATTENDANCE_THRESHOLD
    is_def    = total > 0 and pct < threshold

    summary = AttendanceSummary.query.filter_by(
        student_id=student_id, subject=subject, semester=semester
    ).first()
    if not summary:
        summary = AttendanceSummary(
            student_id=student_id, subject=subject, semester=semester,
            section=section,
        )
        db.session.add(summary)

    summary.section          = section or summary.section
    summary.total_classes    = total
    summary.classes_attended = attended
    summary.attendance_pct   = pct
    summary.is_defaulter     = is_def
    summary.required_classes = summary.calculate_required_classes(threshold) if total else 0
    return summary


@attendance_bp.route("/api/subjects", methods=["GET"])
@login_required
def api_subjects():
    """
    Distinct subject names already on file, so faculty pick from a list
    instead of retyping (retyping is how 'Biology' and 'bio' end up as
    two different subjects). Add a brand-new subject via the '+ Add new'
    option in the picker.
    """
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Access denied."}), 403

    from_summary = {s[0] for s in db.session.query(AttendanceSummary.subject).distinct()}
    from_records = {s[0] for s in db.session.query(AttendanceRecord.subject).distinct()}
    subjects = sorted({s.strip() for s in (from_summary | from_records) if s and s.strip()},
                       key=str.lower)

    return jsonify({"success": True, "subjects": subjects})


# ── Excel-style monthly grid: one subject, whole month, click cells ─────────

def _pct_band(pct):
    if pct is None:
        return "none"
    if pct >= 85:
        return "safe"
    if pct >= 75:
        return "warn"
    if pct >= 65:
        return "risk"
    return "critical"


@attendance_bp.route("/mark/grid", methods=["GET"])
@login_required
def mark_grid_page():
    """Excel-style monthly attendance grid — same data as Mark Attendance,
    just laid out like the spreadsheet faculty are used to."""
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Access denied."}), 403
    return render_template("attendance/grid.html")


@attendance_bp.route("/api/month-grid", methods=["GET"])
@login_required
def api_month_grid():
    """
    Returns a full month's roster x days grid for one subject/semester/section:
    { days: [{day, dow, is_weekend}], students: [{id, roll_number, name,
    cells: {day: 'present'|'absent'|'od'}, total_present, total_absent,
    total_od, marked_days, attendance_pct, band}] }
    attendance_pct/band here are scoped to THIS month only, matching the
    spreadsheet's own Total Present/Absent columns.
    """
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Access denied."}), 403

    subject  = request.args.get("subject", "").strip()
    semester = request.args.get("semester", type=int)
    section  = request.args.get("section", "").strip()
    try:
        year  = int(request.args.get("year", ""))
        month = int(request.args.get("month", ""))
        if not (1 <= month <= 12):
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Please provide a valid month and year."}), 400

    if not (subject and semester and section):
        return jsonify({"success": False, "message": "Subject, semester and section are required."}), 400

    today = _ist_now().date()
    if (year, month) > (today.year, today.month):
        return jsonify({"success": False, "message": "That month is in the future."}), 400

    num_days = calendar.monthrange(year, month)[1]
    days = []
    for d in range(1, num_days + 1):
        dow = _date(year, month, d).weekday()  # 0=Mon .. 6=Sun
        days.append({
            "day": d,
            "dow": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][dow],
            "is_weekend": dow >= 5,
        })

    students = (User.query.filter_by(role="student", semester=semester, section=section)
                .order_by(User.roll_number).all())

    start = _date(year, month, 1)
    end   = _date(year, month, num_days)
    records = AttendanceRecord.query.filter(
        AttendanceRecord.subject == subject,
        AttendanceRecord.date >= start,
        AttendanceRecord.date <= end,
        AttendanceRecord.student_id.in_([s.id for s in students]),
    ).all()

    by_student = {}
    for r in records:
        by_student.setdefault(r.student_id, {})[r.date.day] = r.status

    out_students = []
    for s in students:
        cells = by_student.get(s.id, {})
        present = sum(1 for v in cells.values() if v == "present")
        od      = sum(1 for v in cells.values() if v == "od")
        absent  = sum(1 for v in cells.values() if v == "absent")
        # OD days excluded from total and from attendance count
        marked  = present + absent          # OD not included
        pct = round((present / marked) * 100, 1) if marked else None
        out_students.append({
            "id": s.id, "roll_number": s.roll_number or "", "name": s.name,
            "cells": cells,
            "total_present": present, "total_absent": absent, "total_od": od,
            "marked_days": marked, "attendance_pct": pct, "band": _pct_band(pct),
        })

    return jsonify({"success": True, "days": days, "students": out_students,
                     "subject": subject, "semester": semester, "section": section,
                     "year": year, "month": month})


@attendance_bp.route("/api/grid-toggle", methods=["POST"])
@login_required
def api_grid_toggle():
    """
    Set (or clear) a single roster cell in the monthly grid.
    Body: { student_id, subject, semester, section, date, status }
    status is one of: present | absent | od | clear
    Returns the affected student's updated month totals so the UI can
    repaint that row without re-fetching the whole grid.
    """
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Access denied."}), 403

    data = request.get_json(silent=True) or {}
    student_id = data.get("student_id")
    subject    = (data.get("subject") or "").strip()
    semester   = data.get("semester")
    section    = (data.get("section") or "").strip()
    date_str   = (data.get("date") or "").strip()
    status     = (data.get("status") or "").strip().lower()

    if not (student_id and subject and semester and section and date_str):
        return jsonify({"success": False, "message": "Missing required fields."}), 400
    if status not in ("present", "absent", "od", "clear"):
        return jsonify({"success": False, "message": "Invalid status."}), 400

    try:
        semester = int(semester)
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid semester or date."}), 400

    if target_date > _ist_now().date():
        return jsonify({"success": False, "message": "Can't mark a future date."}), 400

    try:
        rec = AttendanceRecord.query.filter_by(
            student_id=student_id, subject=subject, date=target_date
        ).first()

        if status == "clear":
            if rec:
                db.session.delete(rec)
        elif rec:
            rec.status = status
            rec.semester = semester
            rec.section = section
            rec.uploaded_by = current_user.id
        else:
            rec = AttendanceRecord(
                student_id=student_id, subject=subject, date=target_date,
                status=status, semester=semester, section=section,
                uploaded_by=current_user.id,
            )
            db.session.add(rec)

        db.session.flush()

        # Recompute the running (all-time) summary too, same as Mark Attendance
        summary = _recompute_summary(student_id, subject, semester, section)

        # Month-scoped totals for the grid row itself
        start = _date(target_date.year, target_date.month, 1)
        end   = _date(target_date.year, target_date.month,
                      calendar.monthrange(target_date.year, target_date.month)[1])
        month_records = AttendanceRecord.query.filter(
            AttendanceRecord.student_id == student_id,
            AttendanceRecord.subject == subject,
            AttendanceRecord.date >= start,
            AttendanceRecord.date <= end,
        ).all()
        present = sum(1 for r in month_records if r.status == "present")
        od      = sum(1 for r in month_records if r.status == "od")
        absent  = sum(1 for r in month_records if r.status == "absent")
        # OD excluded — doesn't count as a class day
        marked    = present + absent
        month_pct = round((present / marked) * 100, 1) if marked else None

        db.session.commit()

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error saving: {e}"}), 500

    return jsonify({
        "success": True,
        "status": None if status == "clear" else status,
        "total_present": present, "total_absent": absent, "total_od": od,
        "marked_days": marked, "attendance_pct": month_pct, "band": _pct_band(month_pct),
        "overall_pct": summary.attendance_pct,
    })


@attendance_bp.route("/mark", methods=["GET"])
@login_required
def mark_attendance_page():
    """Faculty page: pick subject/semester/section/date, tick the roster."""
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Access denied."}), 403
    return render_template("attendance/mark.html")


@attendance_bp.route("/api/roster", methods=["GET"])
@login_required
def api_roster():
    """
    Returns the class roster (semester + section) plus, if a subject and
    date are given, whatever attendance is already saved for that day so
    the faculty can edit it instead of starting blank.
    """
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Access denied."}), 403

    semester = request.args.get("semester", type=int)
    section  = request.args.get("section", "").strip()
    subject  = request.args.get("subject", "").strip()
    date_str = request.args.get("date", "").strip()

    if not semester or not section:
        return jsonify({"success": False, "message": "Semester and section are required."}), 400

    query = User.query.filter_by(role="student", semester=semester, section=section)
    students = query.order_by(User.roll_number).all()

    existing = {}
    if subject and date_str:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"success": False, "message": "Invalid date format."}), 400
        rows = AttendanceRecord.query.filter(
            AttendanceRecord.subject == subject,
            AttendanceRecord.date == target_date,
            AttendanceRecord.student_id.in_([s.id for s in students]),
        ).all()
        existing = {r.student_id: r.status for r in rows}

    # ── Running attendance % so far, for context while marking ──────────────
    pct_map = {}
    if subject:
        summaries = AttendanceSummary.query.filter(
            AttendanceSummary.subject == subject,
            AttendanceSummary.student_id.in_([s.id for s in students]),
        ).all()
        pct_map = {s.student_id: {"pct": s.attendance_pct, "total": s.total_classes,
                                   "attended": s.classes_attended} for s in summaries}

    return jsonify({
        "success":  True,
        "students": [
            {
                "id":          s.id,
                "roll_number": s.roll_number or "",
                "name":        s.name,
                "status":      existing.get(s.id, "present"),   # default: present
                "already_marked": s.id in existing,
                "attendance_pct": pct_map.get(s.id, {}).get("pct"),
                "classes_attended": pct_map.get(s.id, {}).get("attended"),
                "total_classes": pct_map.get(s.id, {}).get("total"),
            }
            for s in students
        ],
        "total": len(students),
    })


@attendance_bp.route("/api/marked-dates", methods=["GET"])
@login_required
def api_marked_dates():
    """Recent dates already marked for this subject/semester/section — lets
    faculty quickly jump back and correct a previous day."""
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Access denied."}), 403

    semester = request.args.get("semester", type=int)
    section  = request.args.get("section", "").strip()
    subject  = request.args.get("subject", "").strip()
    if not (semester and section and subject):
        return jsonify({"success": True, "dates": []})

    rows = (db.session.query(AttendanceRecord.date,
                              func.count(AttendanceRecord.id).label("marked"))
            .filter(AttendanceRecord.subject == subject,
                    AttendanceRecord.semester == semester,
                    AttendanceRecord.section == section)
            .group_by(AttendanceRecord.date)
            .order_by(AttendanceRecord.date.desc())
            .limit(15)
            .all())

    return jsonify({
        "success": True,
        "dates": [{"date": d.isoformat(), "marked": c} for d, c in rows],
    })


@attendance_bp.route("/api/mark-save", methods=["POST"])
@login_required
def api_mark_save():
    """
    Saves/updates one day's attendance directly to the database.
    Body: { subject, semester, section, date, entries: [{student_id, status}] }
    status is one of: present | absent | od
    """
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Access denied."}), 403

    data = request.get_json(silent=True) or {}
    subject  = (data.get("subject") or "").strip()
    semester = data.get("semester")
    section  = (data.get("section") or "").strip()
    date_str = (data.get("date") or "").strip()
    entries  = data.get("entries") or []

    if not subject or not semester or not section or not date_str:
        return jsonify({"success": False, "message": "Subject, semester, section and date are required."}), 400
    if not entries:
        return jsonify({"success": False, "message": "No students to mark."}), 400

    try:
        semester = int(semester)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid semester."}), 400

    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"success": False, "message": "Invalid date format. Use YYYY-MM-DD."}), 400

    if target_date > _ist_now().date():
        return jsonify({"success": False, "message": "Can't mark attendance for a future date."}), 400

    valid_status = {"present", "absent", "od"}
    saved, defaulters = 0, []

    try:
        for entry in entries:
            sid    = entry.get("student_id")
            status = (entry.get("status") or "").strip().lower()
            if not sid or status not in valid_status:
                continue

            rec = AttendanceRecord.query.filter_by(
                student_id=sid, subject=subject, date=target_date
            ).first()
            if rec:
                rec.status      = status
                rec.semester    = semester
                rec.section     = section
                rec.uploaded_by = current_user.id
            else:
                rec = AttendanceRecord(
                    student_id=sid, subject=subject, date=target_date,
                    status=status, semester=semester, section=section,
                    uploaded_by=current_user.id,
                )
                db.session.add(rec)
            saved += 1

        db.session.flush()

        # ── Recompute rolling summaries for every student touched today ────
        for entry in entries:
            sid = entry.get("student_id")
            if not sid:
                continue
            summary = _recompute_summary(sid, subject, semester, section)
            if summary.is_defaulter:
                student = User.query.get(sid)
                if student:
                    defaulters.append({
                        "roll_number": student.roll_number, "name": student.name,
                        "attendance_pct": summary.attendance_pct,
                    })

        # ── Log this in SheetRegistry too, so "last synced by / at" widgets
        #    on the student dashboard keep working exactly as with Excel ────
        reg = SheetRegistry.query.filter_by(
            subject=subject, semester=semester, section=section
        ).first()
        if not reg:
            reg = SheetRegistry(
                subject=subject, semester=semester, section=section,
                filename="Marked directly (no file)",
                filepath="db://direct-entry",
                uploaded_by=current_user.id,
            )
            db.session.add(reg)
        else:
            reg.filename    = "Marked directly (no file)"
            reg.filepath    = "db://direct-entry"
            reg.uploaded_by = current_user.id
            reg.uploaded_at = _ist_now()
        reg.total_rows = saved

        db.session.commit()

        # ── Notify newly-flagged defaulters, same as the Excel path ────────
        try:
            from utils.notifications import notify_low_attendance
            for d in defaulters:
                student = User.query.filter(
                    func.upper(User.roll_number) == (d["roll_number"] or "").upper(),
                    User.role == "student"
                ).first()
                summary = AttendanceSummary.query.filter_by(
                    student_id=student.id, subject=subject, semester=semester
                ).first() if student else None
                if student and summary:
                    notify_low_attendance(student, summary)
        except Exception as e:
            import logging
            logging.error(f"Error triggering notifications: {e}")

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error saving attendance: {e}"}), 500

    return jsonify({
        "success":    True,
        "message":    f"Attendance saved for {saved} student(s) on {date_str}.",
        "saved":      saved,
        "defaulters": defaulters,
    })



# ── Download attendance sheet as Excel ───────────────────────────────────────

@attendance_bp.route("/api/download-sheet", methods=["GET"])
@login_required
def api_download_sheet():
    """
    Export attendance records for a given subject/semester/section as an
    Excel workbook. Faculty can download this from the Mark Attendance or
    Grid pages.
    Query params: subject, semester, section
    Optional: year, month  (to scope to one month; omit for all-time)
    """
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Access denied."}), 403

    subject  = request.args.get("subject", "").strip()
    semester = request.args.get("semester", type=int)
    section  = request.args.get("section", "").strip()
    year     = request.args.get("year",  type=int)
    month    = request.args.get("month", type=int)

    if not (subject and semester and section):
        return jsonify({"success": False, "message": "Subject, semester and section are required."}), 400

    # Fetch students for this class
    students = (User.query.filter_by(role="student", semester=semester, section=section)
                .order_by(User.roll_number).all())

    if not students:
        return jsonify({"success": False, "message": "No students found for this class."}), 404

    # Build date filter
    q = AttendanceRecord.query.filter(
        AttendanceRecord.subject  == subject,
        AttendanceRecord.semester == semester,
        AttendanceRecord.section  == section,
        AttendanceRecord.student_id.in_([s.id for s in students]),
    )
    if year and month and 1 <= month <= 12:
        num_days = calendar.monthrange(year, month)[1]
        q = q.filter(
            AttendanceRecord.date >= _date(year, month, 1),
            AttendanceRecord.date <= _date(year, month, num_days),
        )
    records = q.order_by(AttendanceRecord.date).all()

    # Collect all unique dates (as strings) across these records
    all_dates = sorted({r.date.isoformat() for r in records})

    # Build a lookup: student_id -> {date_str: status}
    stu_cells = {s.id: {} for s in students}
    for r in records:
        stu_cells[r.student_id][r.date.isoformat()] = r.status.upper()[0]  # P / A / O

    # Build DataFrame
    rows = []
    for s in students:
        cells = stu_cells.get(s.id, {})
        present = sum(1 for v in cells.values() if v == "P")
        od      = sum(1 for v in cells.values() if v == "O")
        absent  = sum(1 for v in cells.values() if v == "A")
        marked  = present + od + absent
        pct     = round(((present + od) / marked) * 100, 1) if marked else 0.0
        row = {
            "Roll No": s.roll_number or "",
            "Name":    s.name,
        }
        for d in all_dates:
            row[d] = cells.get(d, "")
        row["Total Present"] = present
        row["Total Absent"]  = absent
        row["OD"]            = od
        row["Attendance %"]  = f"{pct}%"
        row["Status"]        = "DEFAULTER" if pct < Config.ATTENDANCE_THRESHOLD else "OK"
        rows.append(row)

    df = pd.DataFrame(rows)

    # Write to BytesIO as Excel
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Attendance")
        ws = writer.sheets["Attendance"]
        # Auto-size columns
        for col in ws.columns:
            max_len = max((len(str(cell.value)) if cell.value else 0) for cell in col)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 30)
    output.seek(0)

    scope = f"{year}-{month:02d}" if (year and month) else "all-time"
    filename = f"attendance_{subject}_{semester}_{section}_{scope}.xlsx".replace(" ", "_")

    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ── Debug: inspect what is saved in DB ───────────────────────────────────────

@attendance_bp.route("/debug/summary")
@login_required
def debug_summary():
    """Shows all AttendanceSummary rows + all student roll numbers. Admin/Faculty only."""
    if current_user.is_student:
        return jsonify({"error": "Access denied"}), 403

    summaries = AttendanceSummary.query.all()
    students  = User.query.filter_by(role="student").all()
    return jsonify({
        "students_in_db": [
            {"id": s.id, "name": s.name, "roll_number": s.roll_number}
            for s in students
        ],
        "attendance_summary_rows": [
            {
                "id":         r.id,
                "student_id": r.student_id,
                "subject":    r.subject,
                "semester":   r.semester,
                "pct":        r.attendance_pct,
            }
            for r in summaries
        ],
        "total_summaries": len(summaries),
    })


# ── Simulator ────────────────────────────────────────────────────────────────

@attendance_bp.route("/simulator", methods=["GET"])
@login_required
def simulator():
    return render_template("attendance/simulator.html")


@attendance_bp.route("/api/simulate", methods=["POST"])
@login_required
def api_simulate():
    """
    What-If Attendance Simulator.
    Formula: ((A + X) / (T + X)) * 100 >= threshold
    Returns: classes needed AND projected % for a range of X.
    """
    data = request.get_json()
    attended = int(data.get("attended", 0))
    total    = int(data.get("total", 0))
    threshold = float(data.get("threshold", 85.0))

    if total <= 0:
        return jsonify({"success": False, "message": "Total classes must be > 0"}), 400

    current_pct = round((attended / total) * 100, 2)

    # Classes needed to reach threshold
    if current_pct >= threshold:
        needed = 0
        message = f"You already meet the {threshold}% requirement."
    else:
        needed = int((threshold * total - 100 * attended) / (100 - threshold)) + 1
        message = f"You need to attend {needed} more consecutive classes."

    # Projection table: next 20 classes
    projection = []
    for x in range(0, 21):
        proj_pct = round(((attended + x) / (total + x)) * 100, 2)
        projection.append({"extra_classes": x, "projected_pct": proj_pct})

    # Classes to skip safely (still stay above threshold)
    can_skip = 0
    t = total
    a = attended
    while True:
        new_pct = ((a) / (t + 1)) * 100
        if new_pct < threshold:
            break
        t += 1
        can_skip += 1
        if can_skip > 50:
            break

    return jsonify({
        "success":       True,
        "current_pct":   current_pct,
        "attended":      attended,
        "total":         total,
        "threshold":     threshold,
        "needed":        needed,
        "can_skip":      can_skip,
        "message":       message,
        "projection":    projection,
    })


# ── Defaulter List ───────────────────────────────────────────────────────────

@attendance_bp.route("/defaulters")
@login_required
def defaulters():
    if current_user.is_student:
        return jsonify({"success": False, "message": "Access denied."}), 403
    return render_template("attendance/defaulters.html")


@attendance_bp.route("/api/defaulters")
@login_required
def api_defaulters():
    if current_user.is_student:
        return jsonify({"success": False, "message": "Access denied."}), 403

    semester = request.args.get("semester", type=int)
    section  = request.args.get("section")
    subject  = request.args.get("subject")
    # Faculty/HOD default to their own department; admin sees all unless they filter.
    department = request.args.get("department") or (
        None if current_user.is_admin else current_user.department
    )

    from utils.live_attendance import get_live_defaulters
    live_path = get_live_attendance_path(current_app.config)
    live_defaulters, live_available = get_live_defaulters(
        live_path, threshold=Config.ATTENDANCE_THRESHOLD,
        department=department, semester=semester, section=section,
    )
    if subject:
        live_defaulters = [d for d in live_defaulters if d["subject"] == subject]

    if live_available:
        return jsonify({
            "success":    True,
            "source":     "live",
            "count":      len(live_defaulters),
            "defaulters": live_defaulters,
        })

    # Fallback: DB-processed summaries
    # Enrich with student name + roll_number.
    # AttendanceSummary.to_dict() only returns student_id,
    # causing roll_number to show as "undefined" in the browser table.
    # Also skip orphaned summaries (student deleted) and bad subject rows.
    query = AttendanceSummary.query.filter_by(is_defaulter=True)
    if semester: query = query.filter_by(semester=semester)
    if section:  query = query.filter_by(section=section)
    if subject:  query = query.filter_by(subject=subject)
    records = query.all()

    enriched = []
    for r in records:
        student = User.query.get(r.student_id)
        if not student:
            continue   # orphaned summary row - skip to avoid undefined in UI
        subj = (r.subject or "").strip()
        if subj.lower() in ("", "unknown", "nan"):
            continue   # bad subject data - skip
        d = r.to_dict()
        d["roll_number"] = student.roll_number or "No USN"
        d["name"]        = student.name        or "Unknown"
        enriched.append(d)

    return jsonify({
        "success":    True,
        "source":     "db",
        "count":      len(enriched),
        "defaulters": enriched,
    })


# ── Student's Own Attendance ─────────────────────────────────────────────────

@attendance_bp.route("/my")
@login_required
def my_attendance():
    live_path = get_live_attendance_path(current_app.config)
    live = read_live_attendance_for_student(
        live_path, current_user, threshold=Config.ATTENDANCE_THRESHOLD
    )

    if live["success"] and live["subjects"]:
        # ── Live from the shared Excel sheet ─────────────────────────────
        summaries = live["subjects"]   # list of dicts — template reads s.attendance_pct etc.
        for s in summaries:
            s.setdefault("semester", current_user.semester)
            s.setdefault("section", current_user.section)
        overall = live["overall"]
        live_info = {"is_live": True, "last_synced": live["last_synced"]}
    else:
        # ── Fallback: previously processed DB summaries ─────────────────
        db_summaries = AttendanceSummary.query.filter_by(
            student_id=current_user.id
        ).all()
        summaries = [s.to_dict() for s in db_summaries]
        overall = round(
            sum(s["attendance_pct"] for s in summaries) / len(summaries), 2
        ) if summaries else 0
        live_info = {"is_live": False, "last_synced": None,
                     "message": live.get("message")}

    return render_template(
        "attendance/my_attendance.html",
        summaries=summaries,
        overall=overall,
        live_info=live_info,
    )


@attendance_bp.route("/api/my")
@login_required
def api_my_attendance():
    """JSON endpoint for AJAX calls — live read, same fallback as /my."""
    live_path = get_live_attendance_path(current_app.config)
    live = read_live_attendance_for_student(
        live_path, current_user, threshold=Config.ATTENDANCE_THRESHOLD
    )

    if live["success"] and live["subjects"]:
        return jsonify({
            "success":     True,
            "subjects":    live["subjects"],
            "overall":     live["overall"],
            "is_live":     True,
            "last_synced": live["last_synced"],
        })

    summaries = AttendanceSummary.query.filter_by(student_id=current_user.id).all()
    return jsonify({
        "success":  True,
        "subjects": [s.to_dict() for s in summaries],
        "overall":  round(
            sum(s.attendance_pct for s in summaries) / len(summaries), 2
        ) if summaries else 0,
        "is_live":  False,
    })


@attendance_bp.route("/api/live")
@login_required
def api_live_attendance():
    """
    Pure live-read endpoint: always re-opens the shared Excel file and
    returns exactly what's in it for this student's USN right now —
    no DB fallback. Useful for a manual "Refresh" button.
    """
    if not current_user.is_student:
        return jsonify({"success": False, "message": "Students only."}), 403

    live_path = get_live_attendance_path(current_app.config)
    result = read_live_attendance_for_student(
        live_path, current_user, threshold=Config.ATTENDANCE_THRESHOLD
    )
    return jsonify(result)


@attendance_bp.route("/api/my-full")
@login_required
def api_my_full():
    """
    Rich attendance data for the student dashboard widget.
    Returns per-subject summary + the sheet metadata (who uploaded, when).
    Prefers the live Excel sheet; falls back to old DB-processed summaries.
    """
    live_path = get_live_attendance_path(current_app.config)
    live = read_live_attendance_for_student(
        live_path, current_user, threshold=Config.ATTENDANCE_THRESHOLD
    )

    if live["success"] and live["subjects"]:
        subjects_data = []
        for s in live["subjects"]:
            item = dict(s)
            reg = SheetRegistry.query.filter_by(
                subject=s["subject"], filename=LIVE_SHEET_NAME
            ).order_by(SheetRegistry.uploaded_at.desc()).first()
            item["sheet_info"] = reg.to_dict() if reg else None
            subjects_data.append(item)

        latest_reg = SheetRegistry.query.filter_by(
            filename=LIVE_SHEET_NAME
        ).order_by(SheetRegistry.uploaded_at.desc()).first()

        return jsonify({
            "success":        True,
            "overall":        live["overall"],
            "subjects":       subjects_data,
            "total_subjects": len(subjects_data),
            "defaulters":     sum(1 for s in subjects_data if s["is_defaulter"]),
            "last_sync":      latest_reg.to_dict() if latest_reg else
                              {"uploaded_by": "faculty", "uploaded_at": live["last_synced"]},
        })

    # ── Fallback: previously processed DB summaries ─────────────────────────
    summaries = AttendanceSummary.query.filter_by(
        student_id=current_user.id
    ).order_by(AttendanceSummary.subject).all()

    subjects_data = []
    for s in summaries:
        item = s.to_dict()
        reg = SheetRegistry.query.filter_by(
            subject=s.subject,
            semester=s.semester,
        ).order_by(SheetRegistry.uploaded_at.desc()).first()
        item["sheet_info"] = reg.to_dict() if reg else None
        subjects_data.append(item)

    overall = round(
        sum(s.attendance_pct for s in summaries) / len(summaries), 2
    ) if summaries else 0

    latest_sheet = SheetRegistry.query.filter_by(
        semester=current_user.semester
    ).order_by(SheetRegistry.uploaded_at.desc()).first()

    return jsonify({
        "success":      True,
        "overall":      overall,
        "subjects":     subjects_data,
        "total_subjects": len(subjects_data),
        "defaulters":   sum(1 for s in summaries if s.is_defaulter),
        "last_sync":    latest_sheet.to_dict() if latest_sheet else None,
    })


# ── Missed Classes By Date (Student) ─────────────────────────────────────────

@attendance_bp.route("/missed")
@login_required
def missed_classes_page():
    """Renders the 'pick a date, see what you missed' page for students."""
    return render_template("attendance/missed.html")


@attendance_bp.route("/api/missed-dates")
@login_required
def api_missed_dates():
    """
    Returns every date the current student was marked absent, pulled
    straight from the uploaded live attendance sheet (falls back to the
    per-date DB records if the live sheet isn't available).
    """
    live_path = get_live_attendance_path(current_app.config)
    live = get_missed_dates_for_student(live_path, current_user)

    if live["success"]:
        if live["missed_by_date"]:
            return jsonify({"success": True, "source": "live", "missed_by_date": live["missed_by_date"]})
        # Live sheet read fine but found nothing — still worth checking DB
        # records in case the student's data lives there instead.

    db_rows = (AttendanceRecord.query
               .filter_by(student_id=current_user.id, status="absent")
               .order_by(AttendanceRecord.date.desc())
               .all())
    if db_rows:
        by_date = {}
        for r in db_rows:
            by_date.setdefault(r.date.isoformat(), set()).add(r.subject)
        missed_by_date = [{"date": d, "subjects": sorted(subs)} for d, subs in by_date.items()]
        missed_by_date.sort(key=lambda x: x["date"], reverse=True)
        return jsonify({"success": True, "source": "db", "missed_by_date": missed_by_date})

    return jsonify({
        "success": True,
        "source": "none",
        "missed_by_date": [],
        "message": live.get("message") or "No missed classes found — either you have a clean record, or attendance hasn't been uploaded yet.",
    })


@attendance_bp.route("/api/missed-month")
@login_required
def api_missed_month():
    """
    Given ?month=1-12&year=YYYY, return a full day-by-day breakdown for the
    current student for that calendar month (status per day: P/A/PARTIAL/NC),
    pulled from the live attendance sheet.
    """
    try:
        month = int(request.args.get("month", ""))
        year = int(request.args.get("year", ""))
        if not (1 <= month <= 12):
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Please provide a valid month (1-12) and year."}), 400

    today = _ist_now().date()
    if (year, month) > (today.year, today.month):
        return jsonify({"success": False, "message": "That month is in the future."}), 400

    live_path = get_live_attendance_path(current_app.config)
    live = get_month_attendance_for_student(live_path, current_user, year, month)

    if live["success"]:
        return jsonify({
            "success": True,
            "source": "live",
            "month": month,
            "year": year,
            "days": live["days"],
            "summary": live["summary"],
        })

    # ── Fallback: build the same shape from per-date DB records ─────────────
    num_days = calendar.monthrange(year, month)[1]
    start = _date(year, month, 1)
    end = _date(year, month, num_days)
    db_rows = (AttendanceRecord.query
               .filter(AttendanceRecord.student_id == current_user.id,
                       AttendanceRecord.date >= start,
                       AttendanceRecord.date <= end)
               .all())

    if db_rows:
        day_map = {d: {"present": set(), "absent": set()} for d in range(1, num_days + 1)}
        for r in db_rows:
            bucket = "absent" if r.status == "absent" else "present"
            day_map[r.date.day][bucket].add(r.subject)

        days_out = []
        days_present = days_absent = days_partial = classes_missed = 0
        for d in range(1, num_days + 1):
            present = sorted(day_map[d]["present"])
            absent  = sorted(day_map[d]["absent"])
            if not present and not absent:
                status = "NC"
            elif absent and not present:
                status = "A"; days_absent += 1; classes_missed += len(absent)
            elif present and not absent:
                status = "P"; days_present += 1
            else:
                status = "PARTIAL"; days_partial += 1; classes_missed += len(absent)
            days_out.append({
                "day": d, "date": _date(year, month, d).isoformat(),
                "present": present, "absent": absent, "status": status,
            })

        return jsonify({
            "success": True, "source": "db", "month": month, "year": year,
            "days": days_out,
            "summary": {"days_present": days_present, "days_absent": days_absent,
                        "days_partial": days_partial, "classes_missed": classes_missed},
        })

    return jsonify({
        "success": True, "source": "none", "month": month, "year": year,
        "days": [], "summary": {},
        "message": live.get("message") or "No attendance data found for this month.",
    })


@attendance_bp.route("/api/missed")
@login_required
def api_missed_classes():
    """
    Given ?date=YYYY-MM-DD, return the subjects the current student missed
    (marked absent) that day. Prefers the live Excel sheet; falls back to
    per-date AttendanceRecord rows in the DB if the live sheet has no
    column for that date.
    """
    date_str = request.args.get("date", "").strip()
    if not date_str:
        return jsonify({"success": False, "message": "Please provide a date (YYYY-MM-DD)."}), 400

    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"success": False, "message": "Invalid date format. Use YYYY-MM-DD."}), 400

    if target_date > _ist_now().date():
        return jsonify({"success": False, "message": "That date is in the future."}), 400

    live_path = get_live_attendance_path(current_app.config)
    live = get_missed_subjects_on_date(live_path, current_user, target_date,
                                       threshold=Config.ATTENDANCE_THRESHOLD)

    if live["success"] and live["date_has_data"]:
        return jsonify({
            "success":  True,
            "source":   "live",
            "date":     date_str,
            "missed":   live["missed"],
            "attended": live["attended"],
        })

    # ── Fallback: per-date DB records (from processed Excel uploads) ────────
    db_records = AttendanceRecord.query.filter_by(
        student_id=current_user.id, date=target_date
    ).all()

    if db_records:
        missed   = [{"subject": r.subject, "status": r.status}
                    for r in db_records if r.status == "absent"]
        attended = [{"subject": r.subject, "status": r.status}
                    for r in db_records if r.status != "absent"]
        return jsonify({
            "success":  True,
            "source":   "db",
            "date":     date_str,
            "missed":   missed,
            "attended": attended,
        })

    return jsonify({
        "success":       True,
        "source":        "none",
        "date":          date_str,
        "missed":        [],
        "attended":      [],
        "message": (live.get("message") or
                    "No attendance data found for that date — "
                    "it may not have been uploaded yet, or there was no class recorded."),
    })


# ── Faculty: Sheet Registry API ──────────────────────────────────────────────

@attendance_bp.route("/api/sheets")
@login_required
def api_sheets():
    """List all uploaded sheets. Faculty/HOD/Admin only."""
    if current_user.is_student:
        return jsonify({"success": False, "message": "Access denied."}), 403

    sheets = SheetRegistry.query.order_by(
        SheetRegistry.uploaded_at.desc()
    ).all()
    return jsonify({
        "success": True,
        "sheets":  [s.to_dict() for s in sheets],
    })

