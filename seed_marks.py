"""Seed dummy internal/external marks for all students."""
import random
from app import create_app
from extensions import db
from models.user import User
from models.placement import StudentPerformance
from models.attendance import AttendanceSummary

app = create_app()

GRADES = [
    (90, 100, "O"),
    (80, 89,  "A+"),
    (70, 79,  "A"),
    (60, 69,  "B+"),
    (50, 59,  "B"),
    (40, 49,  "C"),
    (0,  39,  "F"),
]

def get_grade(total_out_of_150):
    pct = (total_out_of_150 / 150) * 100
    for lo, hi, g in GRADES:
        if lo <= pct <= hi:
            return g, "PASS" if pct >= 40 else "FAIL"
    return "F", "FAIL"

with app.app_context():
    students = User.query.filter_by(role="student", is_active=True).all()
    print(f"Found {len(students)} students")

    inserted = 0
    for student in students:
        # Get subjects from their attendance summary
        summaries = AttendanceSummary.query.filter_by(student_id=student.id).all()
        subjects = [s.subject for s in summaries]

        if not subjects:
            # Fallback default subjects
            subjects = ["Mathematics", "Physics", "Chemistry"]

        sem = student.semester or 4

        for subject in subjects:
            # Check if record already exists
            existing = StudentPerformance.query.filter_by(
                student_id=student.id, subject=subject, semester=sem
            ).first()
            if existing:
                continue

            # Generate realistic marks
            internal = round(random.uniform(28, 45), 1)   # out of 50
            external = round(random.uniform(45, 85), 1)   # out of 100
            total    = round(internal + external, 1)       # out of 150
            grade, result = get_grade(total)

            perf = StudentPerformance(
                student_id     = student.id,
                subject        = subject,
                semester       = sem,
                internal_marks = internal,
                external_marks = external,
                total_marks    = total,
                grade          = grade,
                result         = result,
            )
            db.session.add(perf)
            inserted += 1

    db.session.commit()
    print(f"✅ Inserted {inserted} mark records.")

    # Show sample
    perfs = StudentPerformance.query.limit(5).all()
    for p in perfs:
        student = User.query.get(p.student_id)
        print(f"  {student.name} | {p.subject} | Int:{p.internal_marks} | Ext:{p.external_marks} | {p.grade} | {p.result}")
