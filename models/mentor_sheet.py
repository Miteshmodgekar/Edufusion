"""
MentorSheet Model — Stores the weekly attendance sheet data filled in by mentors.

Each row = one student entry for a given week/period, recorded by the assigned mentor.
"""

from extensions import db
from datetime import datetime, timezone, timedelta, date

_IST = timezone(timedelta(hours=5, minutes=30))


def _ist_now():
    """Return current datetime in IST (UTC+5:30)."""
    return datetime.now(_IST).replace(tzinfo=None)


class MentorSheet(db.Model):
    """One row per student per week, filled in by the student's assigned mentor."""
    __tablename__ = "mentor_sheet"

    id                     = db.Column(db.Integer, primary_key=True)
    student_id             = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    mentor_id              = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # Period label, e.g. "Week 1", "15 Sep – 20 Sep 2025", etc.
    week_label             = db.Column(db.String(100), nullable=True)

    # Attendance — stored as a free-text string so mentors can write "75%", "4/5", etc.
    weekly_attendance      = db.Column(db.String(50), nullable=True)

    reason_for_low         = db.Column(db.Text, nullable=True)
    date_of_communication  = db.Column(db.Date, nullable=True)
    remarks                = db.Column(db.Text, nullable=True)

    updated_at             = db.Column(db.DateTime, default=_ist_now, onupdate=_ist_now)

    # Relationships
    student = db.relationship(
        "User", foreign_keys=[student_id],
        backref=db.backref("mentor_sheet_entries", lazy="dynamic")
    )
    mentor = db.relationship(
        "User", foreign_keys=[mentor_id],
        backref=db.backref("mentor_sheet_authored", lazy="dynamic")
    )

    def to_dict(self):
        return {
            "id":                    self.id,
            "student_id":            self.student_id,
            "mentor_id":             self.mentor_id,
            "student_name":          self.student.name if self.student else "",
            "student_usn":           self.student.roll_number if self.student else "",
            "week_label":            self.week_label or "",
            "weekly_attendance":     self.weekly_attendance or "",
            "reason_for_low":        self.reason_for_low or "",
            "date_of_communication": self.date_of_communication.isoformat()
                                     if self.date_of_communication else "",
            "remarks":               self.remarks or "",
            "updated_at":            self.updated_at.strftime("%d %b %Y, %I:%M %p")
                                     if self.updated_at else "",
        }

    def __repr__(self):
        return f"<MentorSheet student={self.student_id} mentor={self.mentor_id}>"
