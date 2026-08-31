"""
FacultySubjectAssignment — HOD assigns subjects to faculty.
Each row = one faculty handles one subject for a given semester/section.
"""
from extensions import db
from datetime import datetime, timezone, timedelta

_IST = timezone(timedelta(hours=5, minutes=30))

def _ist_now():
    return datetime.now(_IST).replace(tzinfo=None)


class FacultySubjectAssignment(db.Model):
    __tablename__ = "faculty_subject_assignments"

    id          = db.Column(db.Integer, primary_key=True)
    faculty_id  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    subject     = db.Column(db.String(100), nullable=False)
    semester    = db.Column(db.Integer, nullable=False)
    section     = db.Column(db.String(5),  nullable=True)
    department  = db.Column(db.String(100), nullable=True)
    assigned_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)  # HOD id
    assigned_at = db.Column(db.DateTime, default=_ist_now)

    faculty  = db.relationship("User", foreign_keys=[faculty_id],  backref="subject_assignments")
    assigner = db.relationship("User", foreign_keys=[assigned_by])

    def to_dict(self):
        return {
            "id":          self.id,
            "faculty_id":  self.faculty_id,
            "faculty_name": self.faculty.name if self.faculty else "—",
            "subject":     self.subject,
            "semester":    self.semester,
            "section":     self.section or "All",
            "department":  self.department,
            "assigned_at": self.assigned_at.strftime("%d %b %Y") if self.assigned_at else "—",
        }
