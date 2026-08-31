"""User Model — Student, Faculty, HOD, Admin (single table, role column)."""

from extensions import db, login_manager
from flask_login import UserMixin
from datetime import datetime, timezone, timedelta

_IST = timezone(timedelta(hours=5, minutes=30))
def _ist_now():
    return datetime.now(_IST).replace(tzinfo=None)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id            = db.Column(db.Integer,     primary_key=True)
    username      = db.Column(db.String(50),  unique=True, nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    name          = db.Column(db.String(100), nullable=False)

    # 'student' | 'faculty' | 'hod' | 'admin'
    role          = db.Column(db.String(20),  nullable=False, default="student")

    # Student fields
    semester      = db.Column(db.Integer,    nullable=True)
    section       = db.Column(db.String(5),  nullable=True)
    roll_number   = db.Column(db.String(20), nullable=True)

    # Staff fields
    department    = db.Column(db.String(100), nullable=True)
    designation   = db.Column(db.String(100), nullable=True)

    is_active     = db.Column(db.Boolean,  default=True)
    created_at    = db.Column(db.DateTime, default=_ist_now)
    last_login    = db.Column(db.DateTime, nullable=True)

    # Mentor assignment — each student is assigned to one faculty/hod as their mentor
    mentor_id     = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    # Relationships
    attendance_records = db.relationship(
        "AttendanceRecord", backref="student", lazy=True,
        foreign_keys="AttendanceRecord.student_id")
    leave_requests = db.relationship(
        "LeaveRequest", backref="student", lazy=True,
        foreign_keys="LeaveRequest.student_id")
    placement_profile = db.relationship(
        "PlacementProfile", backref="student", uselist=False, lazy=True)
    projects = db.relationship(
        "Project", backref="student", lazy=True,
        foreign_keys="Project.student_id")
    # Mentor self-referential relationship
    mentor = db.relationship(
        "User", foreign_keys=[mentor_id], backref="mentees",
        primaryjoin="User.mentor_id == User.id", remote_side="User.id")

    # ── Role helpers ─────────────────────────────────────────────────────
    @property
    def is_student(self): return self.role == "student"

    @property
    def is_faculty(self): return self.role == "faculty"

    @property
    def is_hod(self):     return self.role == "hod"

    @property
    def is_admin(self):   return self.role == "admin"

    def to_dict(self):
        return {
            "id":          self.id,
            "username":    self.username,
            "email":       self.email,
            "name":        self.name,
            "role":        self.role,
            "semester":    self.semester,
            "section":     self.section,
            "roll_number": self.roll_number,
            "department":  self.department,
            "designation": self.designation,
            "is_active":   self.is_active,
            "last_login":  self.last_login.strftime("%Y-%m-%d %H:%M") if self.last_login else None,
            "mentor_id":   self.mentor_id,
            "mentor_name": self.mentor.name if self.mentor else None,
        }

    def __repr__(self):
        return f"<User {self.username} [{self.role}]>"
