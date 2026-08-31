"""Project Monitoring Blueprint"""
from flask import Blueprint, request, jsonify, render_template
from flask_login import login_required, current_user
from extensions import db
from models.placement import Project, ProjectUpdate

project_bp = Blueprint("project", __name__)


@project_bp.route("/", methods=["GET"])
@login_required
def list_projects():
    # HTML request → serve board template
    if "application/json" not in request.headers.get("Accept", ""):
        return render_template("project/board.html")

    if current_user.is_student:
        projects = Project.query.filter_by(student_id=current_user.id).all()
    else:
        projects = Project.query.all()
    return jsonify({"success": True, "projects": [p.to_dict() for p in projects]})


@project_bp.route("/", methods=["POST"])
@login_required
def create_project():
    if not (current_user.is_faculty or current_user.is_hod or current_user.is_admin):
        return jsonify({"success": False, "message": "Faculty only."}), 403

    data    = request.get_json()
    project = Project(
        student_id  = data["student_id"],
        guide_id    = current_user.id,
        title       = data["title"],
        description = data.get("description", ""),
        domain      = data.get("domain", ""),
        semester    = data.get("semester"),
    )
    db.session.add(project)
    db.session.commit()
    return jsonify({"success": True, "project": project.to_dict()}), 201


@project_bp.route("/<int:project_id>/update", methods=["POST"])
@login_required
def add_update(project_id):
    project = Project.query.get_or_404(project_id)
    if project.student_id != current_user.id:
        return jsonify({"success": False, "message": "Not your project."}), 403

    data   = request.get_json()
    update = ProjectUpdate(
        project_id  = project_id,
        week_number = data.get("week_number", 1),
        update_text = data.get("update_text", ""),
    )
    db.session.add(update)
    db.session.commit()
    return jsonify({"success": True, "message": "Update submitted."}), 201


@project_bp.route("/update/<int:update_id>/approve", methods=["PUT"])
@login_required
def approve_update(update_id):
    if not (current_user.is_faculty or current_user.is_hod):
        return jsonify({"success": False, "message": "Faculty only."}), 403

    update  = ProjectUpdate.query.get_or_404(update_id)
    data    = request.get_json()
    update.guide_approved = data.get("approved", True)
    update.guide_comment  = data.get("comment", "")

    project = Project.query.get(update.project_id)
    if project:
        project.progress_pct = min(project.progress_pct + 10, 100)

    db.session.commit()
    return jsonify({"success": True, "message": "Update reviewed."})
