"""
Live Attendance Fetcher
========================
Reads attendance straight out of ONE shared Excel workbook that faculty
keep updated on the server (no "upload & process" step, no DB write).

Expected workbook shape (matches the register format faculty already use):
  - One sheet per subject (e.g. "Biology", "Chemistry", "English"...).
  - A header row somewhere in the first few rows containing a Roll No /
    USN column and a Student Name column.
  - Daily P/1 present columns, optionally a "Total Present", "Total Absent"
    and/or "Attendance %" column.
  - Sheets that are rollups (name contains "summary", "dashboard",
    "risk", "overview") are skipped — we only read the per-subject
    registers, one live row per student.

The student is matched purely by their USN (User.roll_number), so this
works the moment faculty save a new version of the file — nothing needs
to be "processed" for the student to see it.
"""

import os
import calendar
import re
import pandas as pd
from datetime import datetime

# Sheet names that are rollups/dashboards, not per-subject registers.
SKIP_SHEET_KEYWORDS = ["summary", "dashboard", "risk", "overview", "readme", "instructions"]

# Values in a daily column that count as "present".
PRESENT_VALUES = {"P", "OD", "1", "YES", "Y", "PRESENT"}  # OD treated as present

# Column-name keyword groups.
ROLL_KEYWORDS  = ["roll", "usn", "reg", "id"]
NAME_KEYWORDS  = ["name"]
PCT_KEYWORDS   = ["attendance %", "attendance%", "att %", "att%", "percentage"]
PRES_KEYWORDS  = ["total present", "classes attended", "present total", "attended"]
ABS_KEYWORDS   = ["total absent", "absent total"]
NON_DATE_HINTS = ["overall", "risk", "status", "sem", "section", "sec", "dept",
                  "department", "email", "mail", "#"]


def get_live_attendance_path(app_config):
    """Canonical path of the single shared, faculty-maintained workbook."""
    return os.path.join(app_config["UPLOAD_FOLDER"], "live_attendance.xlsx")


def _norm(cell):
    return str(cell).strip().lower() if cell is not None else ""


def _find_header_row(raw_df, max_scan=8):
    """Scan the first few raw rows for one containing both a roll/usn
    cell and a name cell. Returns the row index, or None if not found."""
    for i in range(min(max_scan, len(raw_df))):
        row_vals = [_norm(v) for v in raw_df.iloc[i].tolist()]
        has_roll = any(any(k in v for k in ROLL_KEYWORDS) for v in row_vals)
        has_name = any(any(k in v for k in NAME_KEYWORDS) for v in row_vals)
        if has_roll and has_name:
            return i
    return None


def _find_col(columns, keywords):
    for kw in keywords:
        for c in columns:
            if kw in str(c).lower():
                return c
    return None


def _is_rollup_sheet(sheet_name, columns):
    lname = sheet_name.lower()
    if any(k in lname for k in SKIP_SHEET_KEYWORDS):
        return True
    lcols = [str(c).lower() for c in columns]
    # A per-subject register won't have both "overall %" and multiple
    # subject-looking columns at once — that pattern belongs to a rollup.
    if any("overall" in c for c in lcols) and any("risk" in c for c in lcols):
        return True
    return False


def _row_looks_valid(roll_val):
    if roll_val is None:
        return False
    s = str(roll_val).strip()
    if not s or s.lower() in ("nan", "none", "roll no", "roll_no", "rollno", "usn", "id"):
        return False
    return True


MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_YEAR_RE = re.compile(
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{4})",
    re.IGNORECASE,
)


def _infer_month_year_from_raw(raw_df, max_scan=6):
    """
    Many registers only state the month/year once, in a title like
    'ATTENDANCE REGISTER · BIOLOGY · MAY 2026', and then use bare day
    numbers (1, 2, 3...31) as the daily column headers. Scan the first
    few rows of the sheet for a 'Month YYYY' pattern so those bare
    numbers can be resolved into real dates. Returns (year, month) or
    (None, None) if nothing is found.
    """
    try:
        rows_to_scan = raw_df.head(max_scan)
    except Exception:
        return None, None

    for _, row in rows_to_scan.iterrows():
        for cell in row:
            if cell is None:
                continue
            text = str(cell)
            m = _MONTH_YEAR_RE.search(text)
            if m:
                month = MONTH_NAMES.get(m.group(1).lower()[:3])
                year = int(m.group(2))
                if month:
                    return year, month
    return None, None


def _parse_header_and_cols(xl, sheet_name):
    """
    Shared header-detection logic used by both the summary parser and the
    per-date lookup. Returns (df, roll_col, name_col, pct_col, pres_col,
    abs_col, date_cols, ctx_year, ctx_month) or None if this sheet isn't a
    per-subject register. ctx_year/ctx_month (may be None) come from a
    'Month YYYY' style title elsewhere in the sheet, used to resolve bare
    day-number column headers (1, 2, 3...31) into real dates.
    """
    raw = xl.parse(sheet_name, header=None)
    header_idx = _find_header_row(raw)
    if header_idx is None:
        return None

    ctx_year, ctx_month = _infer_month_year_from_raw(raw)

    df = xl.parse(sheet_name, header=header_idx)
    df.columns = [str(c).strip() for c in df.columns]

    if _is_rollup_sheet(sheet_name, df.columns):
        return None

    roll_col = _find_col(df.columns, ROLL_KEYWORDS)
    name_col = _find_col(df.columns, NAME_KEYWORDS)
    if not roll_col or not name_col:
        return None

    pct_col  = _find_col(df.columns, PCT_KEYWORDS)
    pres_col = _find_col(df.columns, PRES_KEYWORDS)
    abs_col  = _find_col(df.columns, ABS_KEYWORDS)

    info_cols = {roll_col, name_col, pct_col, pres_col, abs_col}
    info_cols.discard(None)
    date_cols = [
        c for c in df.columns
        if c not in info_cols and not any(h in str(c).lower() for h in NON_DATE_HINTS)
    ]

    return df, roll_col, name_col, pct_col, pres_col, abs_col, date_cols, ctx_year, ctx_month


def _try_parse_date(col_name, ctx_year=None, ctx_month=None):
    """Best-effort: turn a date-like column header into a date object.
    Handles full dates ('05-Aug-24', '2024-08-05', Excel Timestamps) AND
    bare day numbers ('1', '2', ..., '31') when ctx_year/ctx_month are
    supplied (resolved from the sheet's title, e.g. 'MAY 2026'). Returns
    None if the header doesn't resolve to a date (e.g. 'Class 1')."""
    s = str(col_name).strip()
    if not s or s.lower() in ("nan", "none"):
        return None

    # Bare day-of-month number, e.g. a column literally named "1".."31".
    if ctx_year and ctx_month:
        try:
            day_num = int(float(s))
            if 1 <= day_num <= calendar.monthrange(ctx_year, ctx_month)[1]:
                return datetime(ctx_year, ctx_month, day_num).date()
        except (ValueError, TypeError):
            pass

    try:
        parsed = pd.to_datetime(s, dayfirst=True, errors="raise")
        return parsed.date()
    except Exception:
        return None


def _parse_subject_sheet(xl, sheet_name, threshold):
    """Parse one subject sheet, return list of {roll, name, ...} rows, or None
    if this sheet isn't a per-subject attendance register."""
    parsed = _parse_header_and_cols(xl, sheet_name)
    if parsed is None:
        return None
    df, roll_col, name_col, pct_col, pres_col, abs_col, date_cols, ctx_year, ctx_month = parsed
    rows = []
    for _, row in df.iterrows():
        roll_val = row.get(roll_col)
        if not _row_looks_valid(roll_val):
            continue
        roll = str(roll_val).strip()
        name = str(row.get(name_col, "")).strip()

        # Prefer explicit Total Present / Total Absent if the sheet has them.
        if pres_col and pd.notna(row.get(pres_col)):
            attended = int(row.get(pres_col))
            total = attended + (int(row.get(abs_col)) if abs_col and pd.notna(row.get(abs_col)) else len(date_cols))
        else:
            attended = sum(
                1 for d in date_cols
                if _norm(row.get(d)).upper() in PRESENT_VALUES
            )
            total = len(date_cols)

        if pct_col and pd.notna(row.get(pct_col)):
            raw_pct = float(row.get(pct_col))
            pct = round(raw_pct * 100, 2) if raw_pct <= 1 else round(raw_pct, 2)
        else:
            pct = round((attended / total) * 100, 2) if total > 0 else 0.0

        is_def = pct < threshold
        needed = 0
        if is_def and total > 0:
            needed = max(0, int((threshold * total - 100 * attended) / (100 - threshold)) + 1)

        rows.append({
            "roll_number":      roll,
            "name":             name,
            "subject":          sheet_name,
            "total_classes":    total,
            "classes_attended": attended,
            "attendance_pct":   pct,
            "is_defaulter":     is_def,
            "required_classes": needed,
        })

    return rows


def read_live_attendance_for_student(filepath, student, threshold=85.0):
    """
    Open the shared workbook and return every subject-row that matches
    this student's USN/roll number. Returns:
      {"success": bool, "message": str|None, "subjects": [...],
       "overall": float, "last_synced": str|None}
    """
    if not student.roll_number:
        return {"success": False, "message": "Your profile has no USN/roll number on file.",
                "subjects": [], "overall": 0, "last_synced": None}

    if not os.path.exists(filepath):
        return {"success": False, "message": "No live attendance sheet has been uploaded yet.",
                "subjects": [], "overall": 0, "last_synced": None}

    my_roll = student.roll_number.strip().upper()
    subjects = []

    try:
        xl = pd.ExcelFile(filepath)
        for sheet_name in xl.sheet_names:
            try:
                sheet_rows = _parse_subject_sheet(xl, sheet_name, threshold)
            except Exception:
                continue
            if not sheet_rows:
                continue
            for r in sheet_rows:
                if r["roll_number"].strip().upper() == my_roll:
                    subjects.append(r)
                    break
    except Exception as e:
        return {"success": False, "message": f"Could not read the live sheet: {e}",
                "subjects": [], "overall": 0, "last_synced": None}

    overall = round(sum(s["attendance_pct"] for s in subjects) / len(subjects), 2) if subjects else 0.0
    last_synced = datetime.fromtimestamp(os.path.getmtime(filepath)).strftime("%d %b %Y, %I:%M %p")

    return {
        "success":     True,
        "message":     None if subjects else "No row for your USN was found in the live sheet yet.",
        "subjects":    subjects,
        "overall":     overall,
        "last_synced": last_synced,
    }


def get_missed_dates_for_student(filepath, student):
    """
    Scan every subject sheet in the live workbook and build a list of every
    date the student was marked absent, together with which subject(s)
    they missed that day. This powers the "here are the days you missed
    class" list, so students don't have to guess a date first.

    Returns:
      {"success": bool, "message": str|None,
       "missed_by_date": [{"date": "YYYY-MM-DD", "subjects": [...]}, ...]}
      sorted most-recent-first.
    """
    result = {"success": False, "message": None, "missed_by_date": []}

    if not student.roll_number:
        result["message"] = "Your profile has no USN/roll number on file."
        return result
    if not os.path.exists(filepath):
        result["message"] = "No live attendance sheet has been uploaded yet."
        return result

    my_roll = student.roll_number.strip().upper()
    missed_map = {}  # date -> set of subjects

    try:
        xl = pd.ExcelFile(filepath)
        for sheet_name in xl.sheet_names:
            try:
                parsed = _parse_header_and_cols(xl, sheet_name)
            except Exception:
                continue
            if parsed is None:
                continue
            df, roll_col, name_col, pct_col, pres_col, abs_col, date_cols, ctx_year, ctx_month = parsed

            my_row = None
            for _, row in df.iterrows():
                roll_val = row.get(roll_col)
                if _row_looks_valid(roll_val) and str(roll_val).strip().upper() == my_roll:
                    my_row = row
                    break
            if my_row is None:
                continue

            for col in date_cols:
                d = _try_parse_date(col, ctx_year, ctx_month)
                if d is None:
                    continue
                val = _norm(my_row.get(col)).upper()
                if val not in PRESENT_VALUES:
                    missed_map.setdefault(d, set()).add(sheet_name)

    except Exception as e:
        result["message"] = f"Could not read the live sheet: {e}"
        return result

    result["success"] = True
    result["missed_by_date"] = [
        {"date": d.isoformat(), "subjects": sorted(subs)}
        for d, subs in sorted(missed_map.items(), reverse=True)
    ]
    return result


def get_month_attendance_for_student(filepath, student, year, month):
    """
    Build a day-by-day breakdown for one calendar month, across every
    subject sheet in the live workbook — the data behind a "pick a
    month, see every day's status" table.

    Returns:
      {"success": bool, "message": str|None,
       "days": [
         {"day": 1, "date": "2026-04-01", "present": [...], "absent": [...],
          "status": "A" | "P" | "PARTIAL" | "NC"}   # NC = no class recorded
         ...
       ],
       "summary": {"days_present": int, "days_absent": int,
                    "days_partial": int, "classes_missed": int}}

    `status` per day:
      - "NC"      no subject had a date column for this day (no class held,
                   holiday, or just not part of any register)
      - "P"       every subject that had class that day was attended
      - "A"       every subject that had class that day was missed
      - "PARTIAL" attended some, missed some
    """
    result = {"success": False, "message": None, "days": [], "summary": {}}

    if not student.roll_number:
        result["message"] = "Your profile has no USN/roll number on file."
        return result
    if not os.path.exists(filepath):
        result["message"] = "No live attendance sheet has been uploaded yet."
        return result

    try:
        num_days = calendar.monthrange(year, month)[1]
    except Exception:
        result["message"] = "Invalid month/year."
        return result

    my_roll = student.roll_number.strip().upper()
    # day_number -> {"present": set(subjects), "absent": set(subjects)}
    day_map = {d: {"present": set(), "absent": set()} for d in range(1, num_days + 1)}

    try:
        xl = pd.ExcelFile(filepath)
        for sheet_name in xl.sheet_names:
            try:
                parsed = _parse_header_and_cols(xl, sheet_name)
            except Exception:
                continue
            if parsed is None:
                continue
            df, roll_col, name_col, pct_col, pres_col, abs_col, date_cols, ctx_year, ctx_month = parsed

            my_row = None
            for _, row in df.iterrows():
                roll_val = row.get(roll_col)
                if _row_looks_valid(roll_val) and str(roll_val).strip().upper() == my_roll:
                    my_row = row
                    break
            if my_row is None:
                continue

            for col in date_cols:
                d = _try_parse_date(col, ctx_year, ctx_month)
                if d is None or d.year != year or d.month != month:
                    continue
                val = _norm(my_row.get(col)).upper()
                if val in PRESENT_VALUES:
                    day_map[d.day]["present"].add(sheet_name)
                else:
                    day_map[d.day]["absent"].add(sheet_name)

    except Exception as e:
        result["message"] = f"Could not read the live sheet: {e}"
        return result

    days_out = []
    days_present = days_absent = days_partial = classes_missed = 0

    for d in range(1, num_days + 1):
        present = sorted(day_map[d]["present"])
        absent  = sorted(day_map[d]["absent"])
        date_obj = datetime(year, month, d).date()

        if not present and not absent:
            status = "NC"
        elif absent and not present:
            status = "A"
            days_absent += 1
            classes_missed += len(absent)
        elif present and not absent:
            status = "P"
            days_present += 1
        else:
            status = "PARTIAL"
            days_partial += 1
            classes_missed += len(absent)

        days_out.append({
            "day": d,
            "date": date_obj.isoformat(),
            "present": present,
            "absent": absent,
            "status": status,
        })

    result["success"] = True
    result["days"] = days_out
    result["summary"] = {
        "days_present":  days_present,
        "days_absent":   days_absent,
        "days_partial":  days_partial,
        "classes_missed": classes_missed,
    }
    return result


def get_missed_subjects_on_date(filepath, student, target_date, threshold=85.0):
    """
    Scan every subject tab in the live sheet for this student's row, and
    report which subjects have `target_date` marked as anything other than
    present (i.e. classes actually missed that day).

    Returns:
      {"success": bool, "message": str|None, "date_has_data": bool,
       "missed": [{"subject": ..., "status": ...}],
       "attended": [{"subject": ..., "status": ...}]}

    "date_has_data" is False when the sheet exists but none of the subject
    tabs have a date column matching `target_date` — i.e. we genuinely don't
    know (no class recorded that day / not yet uploaded), as opposed to the
    student having a clean attendance record.
    """
    empty = {"success": False, "message": None, "date_has_data": False,
             "missed": [], "attended": []}

    if not student.roll_number:
        empty["message"] = "Your profile has no USN/roll number on file."
        return empty
    if not os.path.exists(filepath):
        empty["message"] = "No live attendance sheet has been uploaded yet."
        return empty

    my_roll = student.roll_number.strip().upper()
    missed, attended = [], []
    date_has_data = False

    try:
        xl = pd.ExcelFile(filepath)
        for sheet_name in xl.sheet_names:
            try:
                parsed = _parse_header_and_cols(xl, sheet_name)
            except Exception:
                continue
            if parsed is None:
                continue
            df, roll_col, name_col, pct_col, pres_col, abs_col, date_cols, ctx_year, ctx_month = parsed

            # Which of this sheet's date columns actually match target_date?
            matching_cols = [c for c in date_cols if _try_parse_date(c, ctx_year, ctx_month) == target_date]
            if not matching_cols:
                continue

            my_row = None
            for _, row in df.iterrows():
                roll_val = row.get(roll_col)
                if _row_looks_valid(roll_val) and str(roll_val).strip().upper() == my_roll:
                    my_row = row
                    break
            if my_row is None:
                continue

            date_has_data = True
            # If a subject has more than one column matching the same date
            # (rare), treat it as missed if ANY of them show absent.
            cell_vals = [_norm(my_row.get(c)).upper() for c in matching_cols]
            if any(v in PRESENT_VALUES for v in cell_vals):
                attended.append({"subject": sheet_name, "status": "present"})
            else:
                missed.append({"subject": sheet_name, "status": "absent"})

    except Exception as e:
        empty["message"] = f"Could not read the live sheet: {e}"
        return empty

    return {
        "success":       True,
        "message":       None,
        "date_has_data": date_has_data,
        "missed":        missed,
        "attended":      attended,
    }


def get_live_defaulters(filepath, threshold=85.0, department=None, semester=None, section=None):
    """
    Scan every subject tab in the live sheet and return every row that's
    below the attendance threshold, enriched with the matching User record
    (name, department, semester, section) so faculty can filter/search it.

    Returns (defaulters: list[dict], available: bool). available=False means
    the live sheet doesn't exist yet / couldn't be read at all — callers
    should fall back to the old DB-based defaulter list in that case.
    """
    if not os.path.exists(filepath):
        return [], False

    from models.user import User

    defaulters = []
    try:
        xl = pd.ExcelFile(filepath)
        for sheet_name in xl.sheet_names:
            try:
                rows = _parse_subject_sheet(xl, sheet_name, threshold)
            except Exception:
                continue
            if not rows:
                continue
            for r in rows:
                if not r["is_defaulter"]:
                    continue
                student = User.query.filter(
                    User.roll_number.isnot(None),
                    db_upper(User.roll_number) == r["roll_number"].strip().upper(),
                    User.role == "student",
                ).first()

                if department and (not student or (student.department or "").upper() != department.upper()):
                    continue
                if semester and (not student or student.semester != semester):
                    continue
                if section and (not student or (student.section or "").upper() != section.upper()):
                    continue

                defaulters.append({
                    "student_id":       student.id if student else None,
                    "roll_number":      r["roll_number"],
                    "name":             student.name if student else r["name"],
                    "department":       student.department if student else None,
                    "semester":         student.semester if student else None,
                    "section":          student.section if student else None,
                    "subject":          r["subject"],
                    "attendance_pct":   r["attendance_pct"],
                    "total_classes":    r["total_classes"],
                    "classes_attended": r["classes_attended"],
                    "required_classes": r["required_classes"],
                })
    except Exception:
        return [], False

    return defaulters, True


def db_upper(col):
    """Small helper so this module doesn't need a top-level sqlalchemy import
    just for one filter — kept local to avoid surprising import-time errors
    if sqlalchemy isn't reachable in some minimal test context."""
    from sqlalchemy import func
    return func.upper(col)


def get_avg_attendance_for_student(filepath, student, threshold=85.0):
    """
    Convenience wrapper: returns the student's average attendance %.
    Prefers live Excel sheet, falls back to DB AttendanceSummary rows.
    Returns (avg_attendance_pct: float, source: "live"|"db"|"none")
    """
    try:
        live = read_live_attendance_for_student(filepath, student, threshold=threshold)
        if live["success"] and live["subjects"]:
            return live["overall"], "live"
    except Exception:
        pass

    # Fallback to DB.
    try:
        from models.attendance import AttendanceSummary
        summaries = AttendanceSummary.query.filter_by(student_id=student.id).all()
        if summaries:
            avg = round(sum(s.attendance_pct for s in summaries) / len(summaries), 2)
            return avg, "db"
    except Exception:
        pass

    return 0.0, "none"
