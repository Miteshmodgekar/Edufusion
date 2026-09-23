"""
Placement Profile, Internship, Project, and Performance models.
"""

from extensions import db
from datetime import datetime, timezone, timedelta

_IST = timezone(timedelta(hours=5, minutes=30))
def _ist_now():
    return datetime.now(_IST).replace(tzinfo=None)


class PlacementProfile(db.Model):
    __tablename__ = "placement_profiles"

    id              = db.Column(db.Integer, primary_key=True)
    student_id      = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False,
                                unique=True)
    resume_path     = db.Column(db.String(255), nullable=True)
    cgpa            = db.Column(db.Float, nullable=True)
    backlogs        = db.Column(db.Integer, default=0)
    skills          = db.Column(db.Text, nullable=True)          # comma-separated
    interests       = db.Column(db.Text, nullable=True)          # comma-separated, e.g. "Web Dev,AI/ML"
    certifications  = db.Column(db.Text, nullable=True)
    readiness_score = db.Column(db.Float, default=0.0)           # 0–100
    is_eligible     = db.Column(db.Boolean, default=False)
    updated_at      = db.Column(db.DateTime, default=_ist_now,
                                onupdate=_ist_now)

    internships = db.relationship("Internship", backref="profile", lazy=True)

    def calculate_readiness(self, attendance_pct):
        """
        Readiness = 50% CGPA weight + 30% skills + 10% certifications + 10% attendance (info only)
        Attendance no longer gates eligibility.
        """
        cgpa_score      = min((self.cgpa or 0) / 10.0, 1.0) * 50
        skill_count     = len(self.skills.split(",")) if self.skills else 0
        skills_score    = min(skill_count / 10.0, 1.0) * 30
        cert_count      = len(self.certifications.split(",")) if self.certifications else 0
        cert_score      = min(cert_count / 5.0, 1.0) * 10
        attend_score    = min(attendance_pct / 100.0, 1.0) * 10
        self.readiness_score = round(cgpa_score + skills_score + cert_score + attend_score, 1)
        self.is_eligible = (
            (self.cgpa or 0) >= 6.0 and
            self.backlogs == 0
        )
        return self.readiness_score

    def to_dict(self):
        return {
            "id":               self.id,
            "student_id":       self.student_id,
            "cgpa":             self.cgpa,
            "backlogs":         self.backlogs,
            "skills":           self.skills,
            "interests":        self.interests,
            "certifications":   self.certifications,
            "readiness_score":  self.readiness_score,
            "is_eligible":      self.is_eligible,
            "internships":      [i.to_dict() for i in self.internships],
        }


class Internship(db.Model):
    __tablename__ = "internships"

    id              = db.Column(db.Integer, primary_key=True)
    profile_id      = db.Column(db.Integer, db.ForeignKey("placement_profiles.id"),
                                nullable=False)
    company         = db.Column(db.String(100), nullable=False)
    role            = db.Column(db.String(100), nullable=True)
    duration_weeks  = db.Column(db.Integer, nullable=True)
    stipend         = db.Column(db.Float, nullable=True)
    start_date      = db.Column(db.Date, nullable=True)
    end_date        = db.Column(db.Date, nullable=True)
    status          = db.Column(db.String(20), default="ongoing")   # ongoing | completed
    certificate_path= db.Column(db.String(255), nullable=True)
    created_at      = db.Column(db.DateTime, default=_ist_now)

    def to_dict(self):
        return {
            "id":              self.id,
            "company":         self.company,
            "role":            self.role,
            "duration_weeks":  self.duration_weeks,
            "stipend":         self.stipend,
            "status":          self.status,
        }


class Project(db.Model):
    __tablename__ = "projects"

    id              = db.Column(db.Integer, primary_key=True)
    student_id      = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    guide_id        = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    title           = db.Column(db.String(200), nullable=False)
    description     = db.Column(db.Text, nullable=True)
    domain          = db.Column(db.String(100), nullable=True)
    status          = db.Column(db.String(30), default="allocated")
    # allocated | in_progress | review | completed
    progress_pct    = db.Column(db.Integer, default=0)
    semester        = db.Column(db.Integer, nullable=True)
    created_at      = db.Column(db.DateTime, default=_ist_now)

    guide    = db.relationship("User", foreign_keys=[guide_id], backref="guided_projects")
    updates  = db.relationship("ProjectUpdate", backref="project", lazy=True)
    members  = db.relationship("ProjectMember", backref="project", lazy=True,
                               cascade="all, delete-orphan")

    def to_dict(self):
        member_list = [
            {
                "student_id":   m.student_id,
                "name":         m.student.name if m.student else "",
                "usn":          m.student.roll_number if m.student else "",
                "is_lead":      m.is_lead,
            }
            for m in self.members
        ]
        return {
            "id":           self.id,
            "student_id":   self.student_id,
            "guide_id":     self.guide_id,
            "guide_name":   self.guide.name if self.guide else "Not assigned",
            "title":        self.title,
            "description":  self.description,
            "domain":       self.domain or "",
            "status":       self.status,
            "progress_pct": self.progress_pct,
            "semester":     self.semester,
            "members":      member_list,
            "updates":      [u.to_dict() for u in self.updates],
        }


class ProjectUpdate(db.Model):
    __tablename__ = "project_updates"

    id          = db.Column(db.Integer, primary_key=True)
    project_id  = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    week_number = db.Column(db.Integer, nullable=False)
    update_text = db.Column(db.Text, nullable=False)
    guide_approved = db.Column(db.Boolean, default=False)
    guide_comment  = db.Column(db.Text, nullable=True)
    submitted_at   = db.Column(db.DateTime, default=_ist_now)

    def to_dict(self):
        return {
            "id":            self.id,
            "project_id":    self.project_id,
            "week":          self.week_number,
            "text":          self.update_text,
            "approved":      self.guide_approved,
            "comment":       self.guide_comment or "",
            "submitted_at":  self.submitted_at.strftime("%d %b %Y") if self.submitted_at else "",
        }


class ProjectMember(db.Model):
    """Junction table — multiple students (2-4) per project."""
    __tablename__ = "project_members"

    id          = db.Column(db.Integer, primary_key=True)
    project_id  = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    student_id  = db.Column(db.Integer, db.ForeignKey("users.id"),   nullable=False)
    is_lead     = db.Column(db.Boolean, default=False)   # first member = project lead
    joined_at   = db.Column(db.DateTime, default=_ist_now)

    student = db.relationship("User", foreign_keys=[student_id])

    def to_dict(self):
        return {
            "project_id": self.project_id,
            "student_id": self.student_id,
            "name":       self.student.name if self.student else "",
            "usn":        self.student.roll_number if self.student else "",
            "is_lead":    self.is_lead,
        }


class StudentPerformance(db.Model):
    """Marks & internal assessment per subject per semester."""
    __tablename__ = "student_performance"

    id              = db.Column(db.Integer, primary_key=True)
    student_id      = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    subject         = db.Column(db.String(100), nullable=False)
    semester        = db.Column(db.Integer, nullable=False)
    internal_marks  = db.Column(db.Float, nullable=True)   # out of 50
    external_marks  = db.Column(db.Float, nullable=True)   # out of 100
    total_marks     = db.Column(db.Float, nullable=True)
    grade           = db.Column(db.String(5), nullable=True)
    result          = db.Column(db.String(10), nullable=True)   # PASS | FAIL
    updated_at      = db.Column(db.DateTime, default=_ist_now)

    student = db.relationship("User", backref="performances", foreign_keys=[student_id])

    def to_dict(self):
        return {
            "id":               self.id,
            "student_id":       self.student_id,
            "subject":          self.subject,
            "semester":         self.semester,
            "internal_marks":   self.internal_marks,
            "external_marks":   self.external_marks,
            "grade":            self.grade,
            "result":           self.result,
        }
