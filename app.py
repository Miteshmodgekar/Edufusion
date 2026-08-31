"""
AI-Driven Smart Academic, Attendance & Placement Management System
"""
from flask import Flask
from config import Config
from extensions import db, login_manager, bcrypt, mail, MAIL_AVAILABLE


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialise extensions with app
    db.init_app(app)
    login_manager.init_app(app)
    bcrypt.init_app(app)
    if MAIL_AVAILABLE and mail:
        mail.init_app(app)

    # Initialise rate limiter
    from routes.auth import limiter
    limiter.init_app(app)

    # Return rate-limit errors as JSON (default is an HTML page, which breaks
    # any frontend fetch() call expecting res.json() to succeed).
    from flask import jsonify, request as flask_request
    @app.errorhandler(429)
    def _rate_limit_handler(e):
        msg = "Too many attempts. Please wait a moment and try again."
        if flask_request.is_json or flask_request.path.startswith(("/auth/", "/attendance/api", "/placement/api",
                                                                     "/drives/api", "/ml/", "/dashboard/api")):
            return jsonify({"success": False, "message": msg}), 429
        return e

    login_manager.login_view             = "auth.login"
    login_manager.login_message_category = "info"

    # Register blueprints
    from routes.auth          import auth_bp
    from routes.attendance   import attendance_bp
    from routes.leave        import leave_bp
    from routes.ml_routes    import ml_bp
    from routes.placement    import placement_bp
    from routes.dashboard    import dashboard_bp
    from routes.project      import project_bp
    from routes.alert        import alert_bp
    from routes.admin        import admin_bp
    from routes.drives       import drives_bp
    from routes.notifications import notify_bp
    from routes.hod          import hod_bp

    app.register_blueprint(auth_bp,        url_prefix="/auth")
    app.register_blueprint(attendance_bp,  url_prefix="/attendance")
    app.register_blueprint(leave_bp,       url_prefix="/leave")
    app.register_blueprint(ml_bp,          url_prefix="/ml")
    app.register_blueprint(placement_bp,   url_prefix="/placement")
    app.register_blueprint(dashboard_bp,   url_prefix="/dashboard")
    app.register_blueprint(project_bp,     url_prefix="/project")
    app.register_blueprint(alert_bp,       url_prefix="/alert")
    app.register_blueprint(admin_bp,       url_prefix="/admin")
    app.register_blueprint(drives_bp,      url_prefix="/drives")
    app.register_blueprint(notify_bp,      url_prefix="/notify")
    app.register_blueprint(hod_bp,         url_prefix="/hod")

    @app.route("/")
    def index():
        from flask import render_template
        return render_template("landing.html")

    # ── PWA: Service Worker must be served from root scope ────────────────────
    @app.route("/sw.js")
    def service_worker():
        from flask import send_from_directory, make_response
        resp = make_response(send_from_directory("static", "sw.js"))
        resp.headers["Content-Type"]  = "application/javascript"
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp

    # ── PWA: Offline fallback page ────────────────────────────────────────────
    @app.route("/offline")
    def offline():
        from flask import render_template
        return render_template("offline.html")

    # ── PWA: Manifest served from root scope ──────────────────────────────────
    @app.route("/manifest.json")
    def pwa_manifest():
        from flask import send_from_directory
        return send_from_directory("static", "manifest.json",
                                   mimetype="application/manifest+json")

    with app.app_context():
        # Import models here so metadata is populated before create_all
        import models.user
        import models.attendance
        import models.leave
        import models.placement
        import models.notification
        import models.drive
        import models.faculty_subject
        import models.career_chat
        db.create_all()
        _auto_migrate(db)
        _seed_demo_data()

    # ── Background Scheduler: process deferred leave adjustments ─────────────────
    # Faculty deadline = 12:00 PM IST every day.
    # At 12:05 PM IST the scheduler wakes up, checks which subjects actually
    # held class on each student's leave date (via AttendanceRecord), and only
    # credits those subjects in attendance_summary.
    _start_leave_scheduler(app)

    return app


def _start_leave_scheduler(app):
    """Register and start the APScheduler job for deferred leave adjustments."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        from zoneinfo import ZoneInfo

        def _run_adjustment():
            """Inner function: runs inside app context so DB queries work."""
            with app.app_context():
                from routes.leave import process_pending_leave_adjustments
                process_pending_leave_adjustments()

        scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
        # Run every day at 12:05 PM IST (5 min after faculty deadline)
        scheduler.add_job(
            func    = _run_adjustment,
            trigger = CronTrigger(hour=12, minute=5, timezone="Asia/Kolkata"),
            id      = "leave_adjustment",
            name    = "Deferred Leave Attendance Adjustment",
            replace_existing = True,
        )
        scheduler.start()
        print("[SCHEDULER] Leave adjustment job scheduled at 12:05 PM IST daily.")
    except Exception as e:
        print(f"[SCHEDULER] Warning: Could not start scheduler — {e}")
        print("  (Install APScheduler: pip install APScheduler==3.10.4)")



def _auto_migrate(db):
    """
    Lightweight column-adder for existing databases (no Alembic in this
    project). db.create_all() only creates brand-new tables — it never
    alters an existing one — so when a model gains a new column, this
    adds it in place instead of the app crashing with 'Unknown column'.
    Safe to run every startup: it only ever adds missing columns, never
    drops or modifies existing data.
    """
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)

    wanted_columns = {
        "placement_profiles": [
            ("interests", "TEXT"),
        ],
        # New columns added for deferred leave attendance adjustment
        "leave_requests": [
            ("adjust_after",    "DATETIME"),
            ("adjustment_note", "TEXT"),
        ],
    }

    for table, columns in wanted_columns.items():
        if table not in inspector.get_table_names():
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        for col_name, col_type in columns:
            if col_name in existing:
                continue
            try:
                db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))
                db.session.commit()
                print(f"[MIGRATE] Added column '{col_name}' to '{table}'.")
            except Exception as e:
                db.session.rollback()
                print(f"[MIGRATE] Could not add column '{col_name}' to '{table}': {e}")


def _seed_demo_data():
    from models.user import User
    if User.query.count() > 0:
        return

    users = [
        User(username="CS21001", email="student@cse.edu", role="student",
             name="Arjun Sharma", roll_number="CS21001",
             semester=4, section="A", department="CSE",
             password_hash=bcrypt.generate_password_hash("student123").decode()),
        User(username="CS21002", email="student2@cse.edu", role="student",
             name="Priya Singh", roll_number="CS21002",
             semester=4, section="A", department="CSE",
             password_hash=bcrypt.generate_password_hash("student123").decode()),
        User(username="FAC001", email="faculty@cse.edu", role="faculty",
             name="Dr. Priya Nair", department="CSE",
             designation="Assistant Professor",
             password_hash=bcrypt.generate_password_hash("faculty123").decode()),
        User(username="HOD001", email="hod@cse.edu", role="hod",
             name="Prof. K. Ramesh", department="CSE",
             designation="Head of Department",
             password_hash=bcrypt.generate_password_hash("hod123").decode()),
        User(username="admin", email="admin@cse.edu", role="admin",
             name="System Administrator",
             password_hash=bcrypt.generate_password_hash("admin123").decode()),
    ]
    db.session.add_all(users)
    db.session.commit()
    print("\n" + "="*45)
    print("  [SEED] Demo users created!")
    print("  Student : CS21001 / student123")
    print("  Faculty : FAC001  / faculty123")
    print("  HOD     : HOD001  / hod123")
    print("  Admin   : admin   / admin123")
    print("="*45 + "\n")


if __name__ == "__main__":
    app = create_app()
    app.run(
        host="0.0.0.0",       # Accept connections from all devices on network
        debug=True,
        port=5000,
        use_reloader=True,
        reloader_type="stat",
        exclude_patterns=["*/site-packages/*", "*/anaconda/*"],
    )
