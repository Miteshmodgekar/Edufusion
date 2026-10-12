"""
Academic Lifecycle Models — Academic Periods, Student Enrollments, Audit Log.

AcademicPeriod : Represents a unique (cohort + semester + academic_year) period.
                 A semester number alone does not identify an academic period.
                 Batch A Sem 3 and Batch B Sem 3 are different academic periods.

StudentEnrollment : Links a student to a specific AcademicPeriod with a status.
                    Preserves full semester history without overwriting previous records.
                    Statuses: enrolled | completed | detained | withdrawn

AcademicAuditLog : Immutable record of every promotion, graduation, USN correction,
                   batch creation, and period closure performed by administrators.
"""

from extensions import db
from datetime import datetime, timezone, timedelta
from sqlalchemy import UniqueConstraint, Index

_IST = timezone(timedelta(hours=5, minutes=30))


def _ist_now():
    """Return current datetime in IST (UTC+5:30)."""
    return datetime.now(_IST).replace(tzinfo=None)


# ── Status constants ──────────────────────────────────────────────────────────

class PeriodStatus:
    PLANNED   = "planned"
    ACTIVE    = "active"
    COMPLETED = "completed"
    CLOSED    = "closed"


class EnrollmentStatus:
    ENROLLED  = "enrolled"
    COMPLETED = "completed"
    DETAINED  = "detained"
    WITHDRAWN = "withdrawn"


class StudentStatus:
    ACTIVE    = "active"
    GRADUATED = "graduated"
    WITHDRAWN = "withdrawn"
    DETAINED  = "detained"


# ── Models ────────────────────────────────────────────────────────────────────

class AcademicPeriod(db.Model):
    """
    Represents one unique academic period for a cohort.

    A semester number alone is not unique — Sem 3 for Batch 2021 and
    Sem 3 for Batch 2023 are different academic periods with different
    students, attendance, and marks.

    Uniqueness constraint: (admission_batch, semester_number, academic_year)
    prevents accidental duplicate periods for the same cohort.
    """
    __tablename__ = "academic_periods"
    __table_args__ = (
        UniqueConstraint(
            "admission_batch", "semester_number", "academic_year",
            name="uq_period_batch_sem_year"
        ),
        Index("ix_period_status", "status"),
        Index("ix_period_batch", "admission_batch"),
    )

    id               = db.Column(db.Integer, primary_key=True)

    # Human-readable label, e.g. "2021-25 Sem 3 (2023-24)"
    label            = db.Column(db.String(200), nullable=True)

    # Admission batch / cohort, e.g. "2021", "2023"
    admission_batch  = db.Column(db.String(20), nullable=False)

    # Semester number 1–8
    semester_number  = db.Column(db.Integer, nullable=False)

    # Academic year/session, e.g. "2023-24"
    academic_year    = db.Column(db.String(12), nullable=False)

    # Department, e.g. "CSE" — one batch may have multiple departments
    department       = db.Column(db.String(100), nullable=True, default="CSE")

    # Period dates (informational; actual dates may vary)
    start_date       = db.Column(db.Date, nullable=True)
    end_date         = db.Column(db.Date, nullable=True)

    # Status: planned | active | completed | closed
    status           = db.Column(db.String(20), nullable=False, default=PeriodStatus.PLANNED)

    # Admin who created this period
    created_by       = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    created_at       = db.Column(db.DateTime, default=_ist_now)
    updated_at       = db.Column(db.DateTime, default=_ist_now, onupdate=_ist_now)

    # Relationships
    enrollments = db.relationship(
        "StudentEnrollment", backref="period", lazy="dynamic",
        cascade="all, delete-orphan"
    )
    creator = db.relationship("User", foreign_keys=[created_by])

    def to_dict(self):
        return {
            "id":              self.id,
            "label":           self.label or self._auto_label(),
            "admission_batch": self.admission_batch,
            "semester_number": self.semester_number,
            "academic_year":   self.academic_year,
            "department":      self.department or "CSE",
            "start_date":      self.start_date.isoformat() if self.start_date else None,
            "end_date":        self.end_date.isoformat()   if self.end_date   else None,
            "status":          self.status,
            "enrollment_count": self.enrollments.count(),
            "created_at":      self.created_at.strftime("%d %b %Y") if self.created_at else None,
        }

    def _auto_label(self):
        return f"Batch {self.admission_batch} — Sem {self.semester_number} ({self.academic_year})"

    def __repr__(self):
        return f"<AcademicPeriod batch={self.admission_batch} sem={self.semester_number} yr={self.academic_year}>"


class StudentEnrollment(db.Model):
    """
    Links a student to a specific AcademicPeriod.

    Preserves full semester history without overwriting previous records.
    A student who repeats a semester will have two enrollment rows for
    the same semester_number but in different academic_period_ids.

    Uniqueness: one enrollment per student per academic period.
    """
    __tablename__ = "student_enrollments"
    __table_args__ = (
        UniqueConstraint(
            "student_id", "academic_period_id",
            name="uq_enrollment_student_period"
        ),
        Index("ix_enrollment_student", "student_id"),
        Index("ix_enrollment_period", "academic_period_id"),
        Index("ix_enrollment_status", "status"),
    )

    id                = db.Column(db.Integer, primary_key=True)

    student_id        = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    academic_period_id = db.Column(db.Integer, db.ForeignKey("academic_periods.id"), nullable=False)

    # Status: enrolled | completed | detained | withdrawn
    status            = db.Column(db.String(20), nullable=False, default=EnrollmentStatus.ENROLLED)

    # Section for this specific enrollment (may differ from user.section for transfers)
    section           = db.Column(db.String(5), nullable=True)

    enrollment_date   = db.Column(db.Date, nullable=True)
    completion_date   = db.Column(db.Date, nullable=True)

    # Admin notes, e.g. "Promoted from Sem 3 Batch 2021"
    notes             = db.Column(db.Text, nullable=True)

    created_at        = db.Column(db.DateTime, default=_ist_now)
    updated_at        = db.Column(db.DateTime, default=_ist_now, onupdate=_ist_now)

    # Relationships
    student = db.relationship(
        "User", foreign_keys=[student_id],
        backref=db.backref("enrollments", lazy="dynamic")
    )

    def to_dict(self):
        return {
            "id":                 self.id,
            "student_id":         self.student_id,
            "student_name":       self.student.name if self.student else "—",
            "student_usn":        (self.student.usn or self.student.username) if self.student else "—",
            "academic_period_id": self.academic_period_id,
            "period_label":       self.period.to_dict()["label"] if self.period else "—",
            "semester_number":    self.period.semester_number if self.period else None,
            "academic_year":      self.period.academic_year   if self.period else None,
            "status":             self.status,
            "section":            self.section or (self.student.section if self.student else None),
            "enrollment_date":    self.enrollment_date.isoformat()  if self.enrollment_date  else None,
            "completion_date":    self.completion_date.isoformat()  if self.completion_date  else None,
            "notes":              self.notes or "",
        }

    def __repr__(self):
        return f"<StudentEnrollment student={self.student_id} period={self.academic_period_id} status={self.status}>"


class AcademicAuditLog(db.Model):
    """
    Immutable audit trail for all academic lifecycle operations.

    Recorded events: promotion, graduation, batch_created, period_created,
    period_closed, usn_correction, enrollment_status_change.

    Never deleted. Read-only after creation.
    """
    __tablename__ = "academic_audit_log"
    __table_args__ = (
        Index("ix_audit_event_type", "event_type"),
        Index("ix_audit_performed_by", "performed_by"),
        Index("ix_audit_student_id", "student_id"),
        Index("ix_audit_created_at", "created_at"),
    )

    id             = db.Column(db.Integer, primary_key=True)

    # Event type: promotion | graduation | batch_created | period_created |
    #             period_closed | usn_correction | enrollment_status_change
    event_type     = db.Column(db.String(50), nullable=False)

    # Administrator who performed the action
    performed_by   = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    # Student affected (nullable for batch/period events)
    student_id     = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    # Source academic period (for promotion: the period being left)
    source_period_id = db.Column(db.Integer, db.ForeignKey("academic_periods.id"), nullable=True)

    # Destination academic period (for promotion: the period being entered)
    dest_period_id   = db.Column(db.Integer, db.ForeignKey("academic_periods.id"), nullable=True)

    # USN correction: old and new values
    old_usn        = db.Column(db.String(30), nullable=True)
    new_usn        = db.Column(db.String(30), nullable=True)

    # Free-text description of the event
    description    = db.Column(db.Text, nullable=True)

    # Admin-supplied reason
    reason         = db.Column(db.Text, nullable=True)

    # JSON-serialized outcome/detail (success count, errors, etc.)
    detail_json    = db.Column(db.Text, nullable=True)

    created_at     = db.Column(db.DateTime, default=_ist_now, nullable=False)

    # Relationships (read-only navigation)
    performer    = db.relationship("User", foreign_keys=[performed_by])
    student      = db.relationship("User", foreign_keys=[student_id])
    source_period = db.relationship("AcademicPeriod", foreign_keys=[source_period_id])
    dest_period   = db.relationship("AcademicPeriod", foreign_keys=[dest_period_id])

    def to_dict(self):
        return {
            "id":             self.id,
            "event_type":     self.event_type,
            "performed_by":   self.performer.name if self.performer else "System",
            "student_id":     self.student_id,
            "student_name":   self.student.name if self.student else None,
            "student_usn":    (self.student.usn or self.student.username) if self.student else None,
            "source_period":  self.source_period.to_dict()["label"] if self.source_period else None,
            "dest_period":    self.dest_period.to_dict()["label"]   if self.dest_period   else None,
            "old_usn":        self.old_usn,
            "new_usn":        self.new_usn,
            "description":    self.description or "",
            "reason":         self.reason or "",
            "detail_json":    self.detail_json,
            "created_at":     self.created_at.strftime("%d %b %Y, %I:%M %p IST") if self.created_at else "",
        }

    def __repr__(self):
        return f"<AcademicAuditLog [{self.event_type}] by={self.performed_by} at={self.created_at}>"
