"""Project Monitoring Blueprint — supports group projects (2–4 members)."""
from flask import Blueprint, request, jsonify, render_template
from flask_login import login_required, current_user
from extensions import db
from models.placement import Project, ProjectUpdate, ProjectMember
from models.user import User
from sqlalchemy import or_

project_bp = Blueprint("project", __name__)

MIN_MEMBERS = 1
MAX_MEMBERS = 10


# ── Pages ─────────────────────────────────────────────────────────────────────

@project_bp.route("/", methods=["GET"])
@login_required
def list_projects():
    # HTML request → serve board template
    if "application/json" not in request.headers.get("Accept", ""):
        return render_template("project/board.html")

    if current_user.is_student:
        # Student sees projects they are a member of
        member_rows = ProjectMember.query.filter_by(student_id=current_user.id).all()
        project_ids = [m.project_id for m in member_rows]

        # Also include legacy projects where student_id matches but no member rows
        legacy = Project.query.filter_by(student_id=current_user.id).all()
        legacy_ids = [p.id for p in legacy if p.id not in project_ids]
        project_ids += legacy_ids

        projects = Project.query.filter(Project.id.in_(project_ids)).all() if project_ids else []
    else:
        projects = Project.query.all()

    return jsonify({"success": True, "projects": [p.to_dict() for p in projects]})


# ── Student search for member picker ─────────────────────────────────────────

@project_bp.route("/api/search-students", methods=["GET"])
@login_required
def search_students():
    """Search students by name or USN, with optional semester & section filter."""
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Faculty only."}), 403

    q       = request.args.get("q", "").strip()
    sem     = request.args.get("sem", "").strip()
    section = request.args.get("section", "").strip()

    query = User.query.filter_by(role="student", is_active=True)

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                User.name.ilike(like),
                User.roll_number.ilike(like),
                User.username.ilike(like),
            )
        )

    if sem:
        try:
            query = query.filter_by(semester=int(sem))
        except ValueError:
            pass

    if section:
        query = query.filter(User.section.ilike(section))

    students = query.order_by(User.name).limit(20).all()

    return jsonify({
        "success":  True,
        "students": [
            {
                "id":          s.id,
                "name":        s.name,
                "usn":         s.roll_number or "",
                "semester":    s.semester,
                "section":     s.section or "",
            }
            for s in students
        ],
    })


# ── Create project (multi-member) ─────────────────────────────────────────────

@project_bp.route("/", methods=["POST"])
@login_required
def create_project():
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Faculty only."}), 403

    data = request.get_json(silent=True) or {}

    # ── Collect member IDs ───────────────────────────────────────────────────
    # Accepts either:
    #   member_ids: [id1, id2, ...]           (new group flow)
    #   student_id: <single_id>               (legacy / fallback)
    member_ids = data.get("member_ids", [])
    if not member_ids and data.get("student_id"):
        member_ids = [data["student_id"]]

    # Deduplicate while preserving order
    seen = set()
    unique_member_ids = []
    for mid in member_ids:
        try:
            mid = int(mid)
        except (TypeError, ValueError):
            continue
        if mid not in seen:
            seen.add(mid)
            unique_member_ids.append(mid)

    if len(unique_member_ids) < MIN_MEMBERS:
        return jsonify({
            "success": False,
            "message": "Please add at least one student."
        }), 400

    if len(unique_member_ids) > MAX_MEMBERS:
        return jsonify({
            "success": False,
            "message": f"A project can have at most {MAX_MEMBERS} members."
        }), 400

    # Validate all students exist
    students = User.query.filter(
        User.id.in_(unique_member_ids), User.role == "student"
    ).all()
    if len(students) != len(unique_member_ids):
        return jsonify({"success": False, "message": "One or more students not found."}), 404

    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"success": False, "message": "Project title is required."}), 400

    # First member is the project lead (stored in student_id for backward compat)
    lead_id = unique_member_ids[0]

    project = Project(
        student_id  = lead_id,
        guide_id    = current_user.id,
        title       = title,
        description = data.get("description", ""),
        domain      = data.get("domain", ""),
        semester    = data.get("semester") or None,
    )
    db.session.add(project)
    db.session.flush()   # get project.id before commit

    # Create member rows
    for idx, sid in enumerate(unique_member_ids):
        member = ProjectMember(
            project_id = project.id,
            student_id = sid,
            is_lead    = (idx == 0),
        )
        db.session.add(member)

    db.session.commit()
    return jsonify({"success": True, "project": project.to_dict()}), 201


# ── Weekly update (student submits) ───────────────────────────────────────────

@project_bp.route("/<int:project_id>/update", methods=["POST"])
@login_required
def add_update(project_id):
    project = Project.query.get_or_404(project_id)

    # Allow any project member (not just the lead) to submit updates
    member_ids = [m.student_id for m in project.members]
    if project.student_id not in member_ids:
        member_ids.append(project.student_id)   # legacy fallback

    if current_user.id not in member_ids:
        return jsonify({"success": False, "message": "Not your project."}), 403

    data   = request.get_json(silent=True) or {}
    update = ProjectUpdate(
        project_id  = project_id,
        week_number = data.get("week_number", 1),
        update_text = data.get("update_text", ""),
    )
    db.session.add(update)
    db.session.commit()
    return jsonify({"success": True, "message": "Update submitted."}), 201


# ── Guide approves a weekly update ────────────────────────────────────────────

@project_bp.route("/update/<int:update_id>/approve", methods=["PUT"])
@login_required
def approve_update(update_id):
    if not (current_user.is_faculty or current_user.is_hod):
        return jsonify({"success": False, "message": "Faculty only."}), 403

    update  = ProjectUpdate.query.get_or_404(update_id)
    data    = request.get_json(silent=True) or {}
    update.guide_approved = data.get("approved", True)
    update.guide_comment  = data.get("comment", "")

    project = Project.query.get(update.project_id)
    if project:
        project.progress_pct = min(project.progress_pct + 10, 100)

    db.session.commit()
    return jsonify({"success": True, "message": "Update reviewed."})


# ── Edit project ──────────────────────────────────────────────────────────────

@project_bp.route("/<int:project_id>/edit", methods=["PUT"])
@login_required
def edit_project(project_id):
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Faculty only."}), 403

    project = Project.query.get_or_404(project_id)
    data    = request.get_json(silent=True) or {}

    if data.get("title"):
        project.title = data["title"].strip()
    if "domain" in data:
        project.domain = data["domain"]
    if "semester" in data:
        project.semester = data["semester"] or None
    if "status" in data:
        allowed = {"allocated", "in_progress", "review", "completed"}
        if data["status"] in allowed:
            project.status = data["status"]

    # Replace members if provided
    new_ids = data.get("member_ids")
    if new_ids is not None:
        unique_new = []
        seen = set()
        for mid in new_ids:
            try:
                mid = int(mid)
            except (TypeError, ValueError):
                continue
            if mid not in seen:
                seen.add(mid)
                unique_new.append(mid)

        if not unique_new:
            return jsonify({"success": False, "message": "At least one member required."}), 400

        # Validate students exist
        students = User.query.filter(
            User.id.in_(unique_new), User.role == "student"
        ).all()
        if len(students) != len(unique_new):
            return jsonify({"success": False, "message": "One or more students not found."}), 404

        # Remove old member rows and re-create
        ProjectMember.query.filter_by(project_id=project.id).delete()
        for idx, sid in enumerate(unique_new):
            db.session.add(ProjectMember(
                project_id=project.id,
                student_id=sid,
                is_lead=(idx == 0),
            ))
        project.student_id = unique_new[0]  # update lead

    db.session.commit()
    return jsonify({"success": True, "project": project.to_dict()})


# ── Delete project ────────────────────────────────────────────────────────────

@project_bp.route("/<int:project_id>/delete", methods=["DELETE"])
@login_required
def delete_project(project_id):
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Faculty only."}), 403

    project = Project.query.get_or_404(project_id)
    # Cascade deletes members + updates (SQLAlchemy relationship)
    db.session.delete(project)
    db.session.commit()
    return jsonify({"success": True, "message": "Project deleted."})
