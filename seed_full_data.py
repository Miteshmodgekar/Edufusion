"""
Full Demo Data Seeder
=====================
Seeds the database with realistic data for a full demo:
  - 15 students, 3 faculty, 1 HOD, 1 admin
  - Attendance records (3 subjects × 15 students)
  - Leave requests (varied statuses)
  - Placement profiles + internships
  - Projects + weekly updates
  - Student performance records

Run ONCE after first startup:
    python seed_full_data.py

WARNING: This will CLEAR existing data and re-seed.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from extensions import db, bcrypt

STUDENTS = [
    ("CS21001","Arjun Sharma",    4,"A",78,38,42,45, "arjun@cse.edu"),
    ("CS21002","Priya Singh",     4,"A",85,38,68,80, "priya@cse.edu"),
    ("CS21003","Rahul Verma",     4,"A",62,18,30,25, "rahul@cse.edu"),
    ("CS21004","Sneha Nair",      4,"A",100,46,88,95,"sneha@cse.edu"),
    ("CS21005","Vikram Das",      4,"A",55,12,22,15, "vikram@cse.edu"),
    ("CS21006","Ananya Roy",      4,"B",90,40,72,85, "ananya@cse.edu"),
    ("CS21007","Karthik M",       4,"B",75,25,48,50, "karthik@cse.edu"),
    ("CS21008","Divya Pillai",    4,"B",97,44,82,90, "divya@cse.edu"),
    ("CS21009","Arun Kumar",      4,"B",68,20,35,30, "arun@cse.edu"),
    ("CS21010","Meera Iyer",      4,"B",92,42,78,88, "meera@cse.edu"),
    ("CS21011","Suresh P",        4,"C",82,30,55,60, "suresh@cse.edu"),
    ("CS21012","Lakshmi V",       4,"C",100,48,92,98,"lakshmi@cse.edu"),
    ("CS21013","Nikhil G",        4,"C",45,10,18,10, "nikhil@cse.edu"),
    ("CS21014","Pooja M",         4,"C",87,35,62,70, "pooja@cse.edu"),
    ("CS21015","Ravi S",          4,"C",77,27,50,55, "ravi@cse.edu"),
]

SUBJECTS = [
    ("Data Structures",        40),
    ("Computer Networks",      36),
    ("Operating Systems",      38),
]


def seed():
    app = create_app()
    with app.app_context():
        print("=" * 55)
        print("CSE Smart System — Full Demo Data Seeder")
        print("=" * 55)

        # Clear existing data
        from models.attendance import AttendanceRecord, AttendanceSummary
        from models.leave import LeaveRequest
        from models.placement import (PlacementProfile, Internship,
                                      Project, ProjectUpdate,
                                      StudentPerformance)
        from models.user import User
        from models.notification import PushSubscription, NotificationLog

        print("\n[1] Clearing existing data...")
        for model in [NotificationLog, PushSubscription, ProjectUpdate,
                      Project, Internship, PlacementProfile,
                      StudentPerformance, LeaveRequest,
                      AttendanceSummary, AttendanceRecord, User]:
            db.session.query(model).delete()
        db.session.commit()
        print("    Done.")

        # ── Users ─────────────────────────────────────────────────────────
        print("\n[2] Creating users...")
        pw_student = bcrypt.generate_password_hash("student123").decode("utf-8")
        pw_faculty = bcrypt.generate_password_hash("faculty123").decode("utf-8")
        pw_hod     = bcrypt.generate_password_hash("hod123").decode("utf-8")
        pw_admin   = bcrypt.generate_password_hash("admin123").decode("utf-8")

        students = []
        for roll, name, sem, sec, att, int_m, ext_m, proj, email in STUDENTS:
            u = User(username=roll, email=email, name=name, role="student",
                     roll_number=roll, semester=sem, section=sec,
                     department="CSE", password_hash=pw_student)
            db.session.add(u)
            students.append((u, att, int_m, ext_m, proj))

        fac1 = User(username="FAC001", email="faculty@cse.edu",
                    name="Dr. Priya Nair", role="faculty",
                    department="CSE", designation="Assistant Professor",
                    password_hash=pw_faculty)
        fac2 = User(username="FAC002", email="faculty2@cse.edu",
                    name="Dr. Rajesh Kumar", role="faculty",
                    department="CSE", designation="Associate Professor",
                    password_hash=pw_faculty)
        fac3 = User(username="FAC003", email="faculty3@cse.edu",
                    name="Prof. Anitha S", role="faculty",
                    department="CSE", designation="Assistant Professor",
                    password_hash=pw_faculty)
        hod = User(username="HOD001", email="hod@cse.edu",
                   name="Prof. K. Ramesh", role="hod",
                   department="CSE", designation="Head of Department",
                   password_hash=pw_hod)
        admin = User(username="admin", email="admin@cse.edu",
                     name="System Administrator", role="admin",
                     password_hash=pw_admin)

        db.session.add_all([fac1, fac2, fac3, hod, admin])
        db.session.commit()
        print(f"    Created {len(students)} students + 3 faculty + 1 HOD + 1 admin")

        # ── Attendance records ────────────────────────────────────────────
        print("\n[3] Seeding attendance summaries...")
        for user, att_pct, int_m, ext_m, proj in students:
            for subject, total_cls in SUBJECTS:
                attended = int(total_cls * att_pct / 100)
                pct      = round((attended / total_cls) * 100, 2)
                is_def   = pct < 85.0
                needed   = 0
                if is_def:
                    needed = max(0, int((85 * total_cls - 100 * attended) / 15) + 1)
                summary = AttendanceSummary(
                    student_id      = user.id,
                    subject         = subject,
                    semester        = user.semester,
                    section         = user.section,
                    total_classes   = total_cls,
                    classes_attended= attended,
                    attendance_pct  = pct,
                    is_defaulter    = is_def,
                    required_classes= needed,
                )
                db.session.add(summary)
        db.session.commit()
        print(f"    Created {len(students) * len(SUBJECTS)} attendance summaries")

        # ── Performance records ───────────────────────────────────────────
        print("\n[4] Seeding performance records...")
        for user, att_pct, int_m, ext_m, proj in students:
            for subject, _ in SUBJECTS:
                total  = int_m + ext_m
                result = "PASS" if (att_pct >= 75 and int_m >= 20
                                    and ext_m >= 35 and total >= 50) else "FAIL"
                perf = StudentPerformance(
                    student_id     = user.id,
                    subject        = subject,
                    semester       = user.semester,
                    internal_marks = float(int_m),
                    external_marks = float(ext_m),
                    total_marks    = float(total),
                    result         = result,
                    grade          = _get_grade(total),
                )
                db.session.add(perf)
        db.session.commit()
        print(f"    Created {len(students) * len(SUBJECTS)} performance records")

        # ── Leave requests ────────────────────────────────────────────────
        print("\n[5] Seeding leave requests...")
        from datetime import date
        leave_data = [
            (students[0][0].id,  "2024-03-10","2024-03-12","medical",
             "Fever","approved","approved","Approved — get well.",fac1.id,hod.id),
            (students[0][0].id,  "2024-04-01","2024-04-02","event",
             "Hackathon at NIT","mentor_approved","approved","pending",
             "Valid reason, forwarded.",fac1.id,hod.id),
            (students[1][0].id,  "2024-04-05","2024-04-07","medical",
             "Surgery follow-up","pending","pending","pending","",fac1.id,hod.id),
            (students[2][0].id,  "2024-03-15","2024-03-15","personal",
             "Family function","rejected","rejected","pending",
             "Low attendance — cannot approve.",fac1.id,hod.id),
            (students[3][0].id,  "2024-04-08","2024-04-09","od",
             "Industrial visit organised by dept","mentor_approved",
             "approved","pending","OD approved.",fac1.id,hod.id),
        ]
        for row in leave_data:
            sid,fd,td,lt,reason,status,ms,hs,mc,mid,hid = row
            lr = LeaveRequest(
                student_id    = sid,
                from_date     = date.fromisoformat(fd),
                to_date       = date.fromisoformat(td),
                leave_type    = lt,
                reason        = reason,
                status        = status,
                mentor_status = ms,
                hod_status    = hs,
                mentor_comment= mc,
                mentor_id     = mid,
                hod_id        = hid,
                attendance_adjusted = (status == "approved"),
            )
            db.session.add(lr)
        db.session.commit()
        print(f"    Created {len(leave_data)} leave requests")

        # ── Placement profiles ────────────────────────────────────────────
        print("\n[6] Seeding placement profiles...")
        placement_data = [
            (students[0][0],  7.8, 0, "Python,DSA,SQL,Flask,Git",
             "NPTEL Python,AWS Cloud"),
            (students[1][0],  8.5, 0, "Python,Java,ML,React,Git,Docker",
             "Google ML,NPTEL DS"),
            (students[2][0],  5.9, 2, "C++,SQL",            ""),
            (students[3][0],  9.2, 0, "Python,Java,ML,React,SQL,AWS,Docker,Git,Linux,Node.js",
             "AWS Cloud,Google ML,NPTEL OS,Oracle DB,Microsoft Azure"),
            (students[4][0],  5.2, 3, "C",                  ""),
            (students[5][0],  8.1, 0, "Python,Java,DSA,SQL,Git,React",
             "NPTEL Python,Google Cloud"),
            (students[7][0],  9.0, 0, "Python,Java,ML,React,SQL,AWS,Git,Linux",
             "AWS Cloud,Google ML,NPTEL"),
            (students[9][0],  8.8, 0, "Python,Java,DSA,SQL,React,Git",
             "NPTEL DS,Google Cloud"),
            (students[11][0], 9.5, 0, "Python,Java,ML,React,SQL,AWS,Docker,Git,Linux,MongoDB",
             "AWS Cloud,Google ML,NPTEL,Oracle,Microsoft Azure"),
        ]
        for user, cgpa, backlogs, skills, certs in placement_data:
            summaries = AttendanceSummary.query.filter_by(student_id=user.id).all()
            avg_att   = (sum(s.attendance_pct for s in summaries) / len(summaries)
                         if summaries else 0)
            p = PlacementProfile(
                student_id     = user.id,
                cgpa           = cgpa,
                backlogs       = backlogs,
                skills         = skills,
                certifications = certs,
            )
            db.session.add(p)
            db.session.flush()
            p.calculate_readiness(avg_att)

            # Add internships for top students
            if cgpa >= 7.5:
                intern = Internship(
                    profile_id     = p.id,
                    company        = "TCS iON",
                    role           = "Python Dev Intern",
                    duration_weeks = 8,
                    stipend        = 10000.0,
                    status         = "completed",
                )
                db.session.add(intern)
                if cgpa >= 8.5:
                    intern2 = Internship(
                        profile_id     = p.id,
                        company        = "Infosys Springboard",
                        role           = "ML Research Intern",
                        duration_weeks = 12,
                        stipend        = 15000.0,
                        status         = "ongoing",
                    )
                    db.session.add(intern2)
        db.session.commit()
        print(f"    Created {len(placement_data)} placement profiles + internships")

        # ── Projects ──────────────────────────────────────────────────────
        print("\n[7] Seeding projects...")
        project_data = [
            (students[0][0],  fac1, "AI-Based Attendance System",
             "ML",  "in_progress", 65),
            (students[1][0],  fac1, "E-Commerce Recommendation Engine",
             "Data Mining", "review", 90),
            (students[2][0],  fac2, "Network Intrusion Detection",
             "Security",    "in_progress", 40),
            (students[3][0],  fac2, "Smart Traffic Management System",
             "IoT",         "completed", 100),
            (students[4][0],  fac3, "Online Voting System",
             "Web Dev",     "allocated", 10),
            (students[5][0],  fac1, "Sentiment Analysis Dashboard",
             "NLP",         "in_progress", 55),
            (students[6][0],  fac2, "Blockchain Certificate Verifier",
             "Blockchain",  "in_progress", 45),
            (students[7][0],  fac3, "Healthcare Chatbot",
             "AI",          "review", 85),
        ]
        for user, guide, title, domain, status, progress in project_data:
            proj = Project(
                student_id   = user.id,
                guide_id     = guide.id,
                title        = title,
                domain       = domain,
                status       = status,
                progress_pct = progress,
                semester     = 4,
            )
            db.session.add(proj)
            db.session.flush()

            # Add weekly updates
            weeks = progress // 15 + 1
            update_texts = [
                "Project scope finalised and tools selected.",
                "Literature survey completed and dataset collected.",
                "Core algorithm implemented with 70% accuracy.",
                "Testing completed and documentation in progress.",
                "Final demo prepared and submitted for review.",
            ]
            for w in range(1, min(weeks + 1, 4)):
                upd = ProjectUpdate(
                    project_id     = proj.id,
                    week_number    = w,
                    update_text    = update_texts[min(w-1, len(update_texts)-1)],
                    guide_approved = True,
                    guide_comment  = "Good progress, keep it up.",
                )
                db.session.add(upd)
        db.session.commit()
        print(f"    Created {len(project_data)} projects with weekly updates")

        # ── Summary ───────────────────────────────────────────────────────
        print("\n" + "=" * 55)
        print("SEED COMPLETE — Full demo data ready!")
        print("=" * 55)
        print(f"  Users:        {User.query.count()}")
        print(f"  Students:     {User.query.filter_by(role='student').count()}")
        print(f"  Att summaries:{AttendanceSummary.query.count()}")
        print(f"  Defaulters:   {AttendanceSummary.query.filter_by(is_defaulter=True).count()}")
        print(f"  Perf records: {StudentPerformance.query.count()}")
        print(f"  Leaves:       {LeaveRequest.query.count()}")
        print(f"  Placements:   {PlacementProfile.query.count()}")
        print(f"  Projects:     {Project.query.count()}")
        print("=" * 55)
        print()
        print("  Login credentials:")
        print("  Student → CS21001 / student123")
        print("  Faculty → FAC001  / faculty123")
        print("  HOD     → HOD001  / hod123")
        print("  Admin   → admin   / admin123")
        print("=" * 55)


def _get_grade(total):
    if total >= 90: return "O"
    if total >= 80: return "A+"
    if total >= 70: return "A"
    if total >= 60: return "B+"
    if total >= 50: return "B"
    if total >= 40: return "C"
    return "F"


if __name__ == "__main__":
    confirm = input("This will CLEAR all existing data. Continue? (yes/no): ")
    if confirm.strip().lower() == "yes":
        seed()
    else:
        print("Aborted.")
