"""
Full project diagnostic script — checks routes, templates, DB, and data integrity.
"""
import os, sys, re

# ── 1. Python syntax check ────────────────────────────────────────────────────
import py_compile, glob

print("=" * 60)
print("1. PYTHON SYNTAX CHECK")
print("=" * 60)
py_files = glob.glob("routes/*.py") + glob.glob("models/*.py") + glob.glob("utils/*.py") + ["app.py", "config.py", "extensions.py"]
syntax_errors = []
for f in py_files:
    if os.path.exists(f):
        try:
            py_compile.compile(f, doraise=True)
            print(f"  OK   {f}")
        except py_compile.PyCompileError as e:
            syntax_errors.append((f, str(e)))
            print(f"  FAIL {f} -> {e}")

print(f"\nSyntax: {len(py_files) - len(syntax_errors)} OK, {len(syntax_errors)} ERRORS")

# ── 2. Template Jinja syntax check ────────────────────────────────────────────
print("\n" + "=" * 60)
print("2. TEMPLATE JINJA SYNTAX CHECK")
print("=" * 60)
from jinja2 import Environment, FileSystemLoader, TemplateSyntaxError

template_dir = os.path.join(os.getcwd(), "templates")
env = Environment(loader=FileSystemLoader(template_dir))
tmpl_errors = []
tmpl_ok = []
for root, dirs, files in os.walk(template_dir):
    for f in files:
        if f.endswith(".html"):
            rel = os.path.relpath(os.path.join(root, f), template_dir).replace("\\", "/")
            try:
                env.parse(open(os.path.join(root, f), encoding="utf-8").read())
                tmpl_ok.append(rel)
            except TemplateSyntaxError as e:
                tmpl_errors.append((rel, str(e)))
                print(f"  FAIL {rel} -> {e}")

if not tmpl_errors:
    print(f"  All {len(tmpl_ok)} templates OK")
print(f"\nTemplates: {len(tmpl_ok)} OK, {len(tmpl_errors)} ERRORS")

# ── 3. App import and DB check ────────────────────────────────────────────────
print("\n" + "=" * 60)
print("3. APP IMPORT + DATABASE CHECK")
print("=" * 60)
try:
    from app import create_app
    app = create_app()
    print("  App creation: OK")

    with app.app_context():
        from extensions import db
        result = db.session.execute(db.text("SELECT 1")).fetchone()
        print("  DB connection: OK")

        tables = [t[0] for t in db.session.execute(db.text("SHOW TABLES")).fetchall()]
        print(f"  Tables found ({len(tables)}): {tables}")

        # Check required columns
        required = {
            "users":          ["id","username","email","role","mentor_id","is_active"],
            "leave_requests": ["id","student_id","mentor_id","mentor_status","hod_status","proof_filename","proof_original_name","adjust_after"],
        }
        print("\n  COLUMN CHECK:")
        for table, req_cols in required.items():
            cols = [c[0] for c in db.session.execute(db.text(f"SHOW COLUMNS FROM {table}")).fetchall()]
            missing = [c for c in req_cols if c not in cols]
            if missing:
                print(f"    MISSING in {table}: {missing}")
            else:
                print(f"    {table}: all required columns present")

        # ── 4. Data integrity ─────────────────────────────────────────────────
        print("\n" + "=" * 60)
        print("4. DATA INTEGRITY")
        print("=" * 60)

        from models.user import User
        from models.leave import LeaveRequest
        from sqlalchemy import func

        roles = db.session.query(User.role, func.count(User.id)).group_by(User.role).all()
        print(f"  Users by role: {dict(roles)}")

        # Orphan leave records
        orphan = db.session.execute(db.text(
            "SELECT COUNT(*) FROM leave_requests l WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = l.student_id)"
        )).scalar()
        print(f"  Orphan leave records: {orphan} {'OK' if orphan == 0 else 'WARNING'}")

        # Broken mentor refs
        broken = db.session.execute(db.text(
            "SELECT COUNT(*) FROM users u WHERE u.mentor_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM users m WHERE m.id = u.mentor_id)"
        )).scalar()
        print(f"  Broken mentor refs: {broken} {'OK' if broken == 0 else 'WARNING'}")

        # Stuck pending leaves
        stuck = LeaveRequest.query.filter_by(status="pending").count()
        mentor_pending = LeaveRequest.query.filter_by(mentor_status="pending").count()
        hod_pending = LeaveRequest.query.filter_by(hod_status="pending", mentor_status="approved").count()
        print(f"  Leaves - total pending: {stuck}, awaiting mentor: {mentor_pending}, awaiting HOD: {hod_pending}")

        # Students without mentor
        no_mentor = User.query.filter_by(role="student", mentor_id=None).count()
        total_students = User.query.filter_by(role="student").count()
        print(f"  Students without mentor: {no_mentor}/{total_students}")

        # Check uploads folder
        proof_dir = os.path.join(os.getcwd(), "uploads", "leave_proofs")
        print(f"  Proof upload dir exists: {os.path.exists(proof_dir)}")

        # ── 5. Route registration check ───────────────────────────────────────
        print("\n" + "=" * 60)
        print("5. ROUTE REGISTRATION")
        print("=" * 60)
        critical_routes = [
            "/admin/dashboard", "/admin/mentors", "/admin/api/mentors",
            "/admin/api/mentors/assign", "/admin/api/mentors/unassign",
            "/leave/apply", "/leave/my", "/leave/pending/mentor", "/leave/pending/hod",
            "/leave/mentor/report", "/leave/mentor/report/download",
            "/leave/proof/<path:filename>",
            "/dashboard/", "/dashboard/api/data",
            "/attendance/mark", "/attendance/defaulters",
            "/auth/login", "/auth/logout", "/auth/register",
        ]
        registered = [str(r) for r in app.url_map.iter_rules()]
        for route in critical_routes:
            found = route in registered
            print(f"  {'OK' if found else 'MISSING'} {route}")

        print("\n" + "=" * 60)
        print("DIAGNOSTIC COMPLETE")
        print("=" * 60)

except Exception as e:
    import traceback
    print(f"  ERROR: {e}")
    traceback.print_exc()
