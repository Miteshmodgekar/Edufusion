"""User Model — Student, Faculty, HOD, Admin (single table, role column).

USN Policy:
  - USN is the permanent, unique institutional identifier for every student.
  - It is stored in the `usn` column with a UNIQUE constraint.
  - For backward compatibility, `username` still doubles as the login ID and
    is pre-populated with the USN for student accounts.
  - `roll_number` is retained for legacy compatibility but must not be used
    as a global unique key.
  - USN must never be changed during ordinary operation. Corrections require
    admin action and are recorded in AcademicAuditLog.
"""

from extensions import db, login_manager
from flask_login import UserMixin
from datetime import datetime, timezone, timedelta


def normalize_usn(raw_usn: str) -> str:
    """Normalize a USN: strip whitespace and uppercase.
    VTU USNs are conventionally uppercase (e.g. 1RV21CS001).
    Adjust this function if the college uses a different convention.
    """
    if not raw_usn:
        return ""
    return raw_usn.strip().upper()

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

    # ── USN — permanent, unique business identifier for students ────────────
    # Nullable for non-student accounts (faculty, hod, admin have no USN).
    # For students: populated on registration; treated as immutable thereafter.
    # Unique constraint enforced at DB level to prevent duplicates.
    usn           = db.Column(db.String(30), nullable=True, unique=True, index=True)

    # Student fields
    semester      = db.Column(db.Integer,    nullable=True)
    section       = db.Column(db.String(5),  nullable=True)
    roll_number   = db.Column(db.String(20), nullable=True)

    # Admission batch / cohort year, e.g. "2021" — used to group students
    # who entered together. Different from academic_year of each semester.
    admission_batch = db.Column(db.String(20), nullable=True)

    # Student lifecycle status: active | graduated | withdrawn | detained
    # Faculty/HOD/Admin: use is_active instead.
    status        = db.Column(db.String(20), nullable=True, default="active")

    # Graduation date — set by admin when marking a student as graduated.
    # Preserved alongside all historical records.
    graduated_at  = db.Column(db.DateTime, nullable=True)

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

    @property
    def is_graduated(self): return self.status == "graduated"

    @property
    def effective_usn(self):
        """Return the best available USN for display: usn > username > roll_number."""
        return self.usn or self.username or self.roll_number or ""

    def to_dict(self):
        return {
            "id":              self.id,
            "username":        self.username,
            "email":           self.email,
            "name":            self.name,
            "role":            self.role,
            "usn":             self.usn,
            "effective_usn":   self.effective_usn,
            "semester":        self.semester,
            "section":         self.section,
            "roll_number":     self.roll_number,
            "admission_batch": self.admission_batch,
            "status":          self.status or "active",
            "graduated_at":    self.graduated_at.strftime("%Y-%m-%d") if self.graduated_at else None,
            "department":      self.department,
            "designation":     self.designation,
            "is_active":       self.is_active,
            "last_login":      self.last_login.strftime("%Y-%m-%d %H:%M") if self.last_login else None,
            "mentor_id":       self.mentor_id,
            "mentor_name":     self.mentor.name if self.mentor else None,
        }

    def __repr__(self):
        return f"<User {self.username} [{self.role}]>"
