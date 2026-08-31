"""
Placement Drive Models.

PlacementDrive  — one company drive posting (admin creates)
DriveApplication — one student application per drive
"""

from extensions import db
from datetime import datetime, timezone, timedelta

_IST = timezone(timedelta(hours=5, minutes=30))
def _ist_now():
    return datetime.now(_IST).replace(tzinfo=None)


class PlacementDrive(db.Model):
    """A company placement / internship drive posted by admin."""
    __tablename__ = "placement_drives"

    id               = db.Column(db.Integer, primary_key=True)
    company_name     = db.Column(db.String(120), nullable=False)
    role             = db.Column(db.String(120), nullable=False)
    package_lpa      = db.Column(db.Float, nullable=True)          # CTC in LPA
    location         = db.Column(db.String(200), nullable=True)
    description      = db.Column(db.Text, nullable=True)
    deadline         = db.Column(db.Date, nullable=True)           # Last date to apply

    # ── AI Matching Criteria ──────────────────────────────────────────────
    min_cgpa         = db.Column(db.Float, default=6.0)
    required_skills  = db.Column(db.Text, nullable=True)           # comma-separated
    max_backlogs     = db.Column(db.Integer, default=0)
    min_attendance   = db.Column(db.Float, default=75.0)
    eligible_branches= db.Column(db.String(200), nullable=True)    # "CSE,IT" or blank=all

    # ── Meta ─────────────────────────────────────────────────────────────
    posted_by        = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    status           = db.Column(db.String(20), default="open")    # open | closed
    notifications_sent = db.Column(db.Integer, default=0)
    eligible_count   = db.Column(db.Integer, default=0)
    created_at       = db.Column(db.DateTime, default=_ist_now)

    poster       = db.relationship("User", foreign_keys=[posted_by], backref="posted_drives")
    applications = db.relationship("DriveApplication", backref="drive", lazy=True,
                                   cascade="all, delete-orphan")

    @property
    def skills_list(self):
        if not self.required_skills:
            return []
        return [s.strip().lower() for s in self.required_skills.split(",") if s.strip()]

    def to_dict(self):
        return {
            "id":               self.id,
            "company_name":     self.company_name,
            "role":             self.role,
            "package_lpa":      self.package_lpa,
            "location":         self.location,
            "description":      self.description,
            "deadline":         self.deadline.isoformat() if self.deadline else None,
            "min_cgpa":         self.min_cgpa,
            "required_skills":  self.required_skills or "",
            "max_backlogs":     self.max_backlogs,
            "min_attendance":   self.min_attendance,
            "eligible_branches":self.eligible_branches or "All",
            "status":           self.status,
            "notifications_sent": self.notifications_sent,
            "eligible_count":   self.eligible_count,
            "created_at":       self.created_at.isoformat(),
        }


class DriveApplication(db.Model):
    """A student applying for a placement drive."""
    __tablename__ = "drive_applications"

    id           = db.Column(db.Integer, primary_key=True)
    drive_id     = db.Column(db.Integer, db.ForeignKey("placement_drives.id"), nullable=False)
    student_id   = db.Column(db.Integer, db.ForeignKey("users.id"),           nullable=False)
    match_score  = db.Column(db.Float, default=0.0)                # 0–100 AI score
    status       = db.Column(db.String(20), default="applied")     # applied|shortlisted|selected|rejected
    applied_at   = db.Column(db.DateTime, default=_ist_now)

    student = db.relationship("User", foreign_keys=[student_id], backref="drive_applications")

    __table_args__ = (
        db.UniqueConstraint("drive_id", "student_id", name="uq_drive_student"),
    )

    def to_dict(self):
        from models.user import User
        student = User.query.get(self.student_id)
        return {
            "id":          self.id,
            "drive_id":    self.drive_id,
            "student_id":  self.student_id,
            "student":     student.name        if student else "Unknown",
            "roll":        student.roll_number if student else "—",
            "semester":    student.semester    if student else 0,
            "section":     student.section     if student else "—",
            "match_score": self.match_score,
            "status":      self.status,
            "applied_at":  self.applied_at.isoformat(),
        }
