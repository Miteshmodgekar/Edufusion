"""Leave Request Model — Multi-level approval flow.

Flow: Student applies → Mentor reviews → HOD approves → Attendance auto-adjusted.
"""

from extensions import db
from datetime import datetime, timezone, timedelta

_IST = timezone(timedelta(hours=5, minutes=30))
def _ist_now():
    return datetime.now(_IST).replace(tzinfo=None)


class LeaveRequest(db.Model):
    __tablename__ = "leave_requests"

    id               = db.Column(db.Integer, primary_key=True)
    student_id       = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    from_date        = db.Column(db.Date, nullable=False)
    to_date          = db.Column(db.Date, nullable=False)
    reason           = db.Column(db.Text, nullable=False)
    leave_type       = db.Column(db.String(30), default="personal")

    # Overall pipeline status
    status           = db.Column(db.String(30), default="pending")

    # ── Mentor level ─────────────────────────────────────────────────────
    mentor_id        = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    mentor_status    = db.Column(db.String(20), default="pending")
    mentor_comment   = db.Column(db.Text, nullable=True, default="")
    mentor_at        = db.Column(db.DateTime, nullable=True)

    # ── HOD level ────────────────────────────────────────────────────────
    hod_id           = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    hod_status       = db.Column(db.String(20), default="pending")
    hod_comment      = db.Column(db.Text, nullable=True, default="")
    hod_at           = db.Column(db.DateTime, nullable=True)

    # ── Attendance auto-update flag ───────────────────────────────────────
    attendance_adjusted  = db.Column(db.Boolean,  default=False)
    adjust_after         = db.Column(db.DateTime, nullable=True)
    adjustment_note      = db.Column(db.Text,     nullable=True)

    # ── Proof document ───────────────────────────────────────────────────────
    proof_filename      = db.Column(db.String(255), nullable=True)   # stored filename (UUID)
    proof_original_name = db.Column(db.String(255), nullable=True)   # original filename shown to mentor

    created_at           = db.Column(db.DateTime, default=_ist_now)

    # Relationships
    mentor = db.relationship("User", foreign_keys=[mentor_id], backref="mentor_leaves")
    hod    = db.relationship("User", foreign_keys=[hod_id],    backref="hod_leaves")

    @property
    def days(self):
        return (self.to_date - self.from_date).days + 1

    def to_dict(self):
        """Return a complete dict including joined student/mentor data."""
        # Lazy-import to avoid circular dependencies
        from models.user import User
        from models.attendance import AttendanceSummary

        student = User.query.get(self.student_id)

        # Overall attendance % (average across subjects)
        attendance_pct = 0.0
        if student:
            summaries = AttendanceSummary.query.filter_by(
                student_id=self.student_id).all()
            if summaries:
                attendance_pct = round(
                    sum(s.attendance_pct for s in summaries) / len(summaries), 1)

        mentor_name = self.mentor.name if self.mentor else "—"
        hod_name    = self.hod.name    if self.hod    else "—"

        return {
            # Leave core
            "id":             self.id,
            "student_id":     self.student_id,
            "from_date":      self.from_date.isoformat(),
            "to_date":        self.to_date.isoformat(),
            "days":           self.days,
            "reason":         self.reason,
            "leave_type":     self.leave_type,

            # Pipeline status
            "status":         self.status,
            "mentor_status":  self.mentor_status,
            "mentor_comment": self.mentor_comment or "",
            "mentor_name":    mentor_name,
            "hod_status":     self.hod_status,
            "hod_comment":    self.hod_comment or "",
            "hod_name":       hod_name,
            "attendance_adjusted": self.attendance_adjusted,
            "adjust_after":        self.adjust_after.isoformat() if self.adjust_after else None,
            "adjustment_note":     self.adjustment_note or "",

            # Student info (for mentor/HOD dashboards)
            "student":        student.name        if student else "Unknown",
            "roll":           student.roll_number if student else "—",
            "semester":       student.semester    if student else 0,
            "section":        student.section     if student else "—",

            # Attendance snapshot
            "attendance_pct": attendance_pct,

            # Timestamps
            "created_at":     self.created_at.isoformat() if self.created_at else "",
            "mentor_at":      self.mentor_at.isoformat()  if self.mentor_at  else "",
            "hod_at":         self.hod_at.isoformat()     if self.hod_at     else "",

            # Proof document
            "has_proof":           bool(self.proof_filename),
            "proof_original_name": self.proof_original_name or "",
        }
