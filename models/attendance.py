"""
Attendance Models — Individual records + Summary per subject.
"""

from extensions import db
from datetime import datetime, timezone, timedelta

# ── IST helper ────────────────────────────────────────────────────────────────
_IST = timezone(timedelta(hours=5, minutes=30))

def _ist_now():
    """Return current datetime in IST (UTC+5:30)."""
    return datetime.now(_IST).replace(tzinfo=None)


class AttendanceRecord(db.Model):
    """One row per student per subject per date."""
    __tablename__ = "attendance_records"

    id          = db.Column(db.Integer, primary_key=True)
    student_id  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    subject     = db.Column(db.String(100), nullable=False)
    date        = db.Column(db.Date, nullable=False)
    status      = db.Column(db.String(10), nullable=False)  # 'present' | 'absent' | 'od'
    semester    = db.Column(db.Integer, nullable=True)
    section     = db.Column(db.String(5), nullable=True)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at  = db.Column(db.DateTime, default=_ist_now)

    def to_dict(self):
        return {
            "id":         self.id,
            "student_id": self.student_id,
            "subject":    self.subject,
            "date":       self.date.isoformat(),
            "status":     self.status,
            "semester":   self.semester,
            "section":    self.section,
        }


class AttendanceSummary(db.Model):
    """Aggregated attendance % per student per subject per semester."""
    __tablename__ = "attendance_summary"

    id              = db.Column(db.Integer, primary_key=True)
    student_id      = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    subject         = db.Column(db.String(100), nullable=False)
    semester        = db.Column(db.Integer, nullable=False)
    section         = db.Column(db.String(5), nullable=True)
    total_classes   = db.Column(db.Integer, default=0)
    classes_attended= db.Column(db.Integer, default=0)
    attendance_pct  = db.Column(db.Float, default=0.0)
    is_defaulter    = db.Column(db.Boolean, default=False)
    required_classes= db.Column(db.Integer, default=0)  # To reach 85%
    last_updated    = db.Column(db.DateTime, default=_ist_now, onupdate=_ist_now)

    student = db.relationship("User", backref="attendance_summaries",
                              foreign_keys=[student_id])

    def calculate_percentage(self):
        if self.total_classes == 0:
            return 0.0
        return round((self.classes_attended / self.total_classes) * 100, 2)

    def calculate_required_classes(self, threshold=85.0):
        """How many consecutive classes needed to reach threshold."""
        if self.attendance_pct >= threshold:
            return 0
        # Formula: (threshold * T - 100 * A) / (100 - threshold)
        T = self.total_classes
        A = self.classes_attended
        needed = (threshold * T - 100 * A) / (100 - threshold)
        return max(0, int(needed) + 1)

    def to_dict(self):
        return {
            "id":               self.id,
            "student_id":       self.student_id,
            "subject":          self.subject,
            "semester":         self.semester,
            "section":          self.section,
            "total_classes":    self.total_classes,
            "classes_attended": self.classes_attended,
            "attendance_pct":   self.attendance_pct,
            "is_defaulter":     self.is_defaulter,
            "required_classes": self.required_classes,
            "last_updated":     self.last_updated.strftime("%d %b %Y, %I:%M %p IST") if self.last_updated else None,
        }


class SheetRegistry(db.Model):
    """Tracks every attendance Excel sheet uploaded by faculty/HOD."""
    __tablename__ = "sheet_registry"

    id           = db.Column(db.Integer, primary_key=True)
    subject      = db.Column(db.String(100), nullable=False)
    semester     = db.Column(db.Integer, nullable=False)
    section      = db.Column(db.String(5), nullable=True)
    filename     = db.Column(db.String(255), nullable=False)
    filepath     = db.Column(db.String(512), nullable=False)
    uploaded_by  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    total_rows   = db.Column(db.Integer, default=0)
    uploaded_at  = db.Column(db.DateTime, default=_ist_now)

    uploader = db.relationship("User", foreign_keys=[uploaded_by])

    def to_dict(self):
        return {
            "id":          self.id,
            "subject":     self.subject,
            "semester":    self.semester,
            "section":     self.section,
            "filename":    self.filename,
            "uploaded_by": self.uploader.name if self.uploader else "Unknown",
            "total_rows":  self.total_rows,
            "uploaded_at": self.uploaded_at.strftime("%d %b %Y, %I:%M %p IST"),
        }
