"""Placement Blueprint"""
import uuid
from flask import Blueprint, request, jsonify, render_template, current_app
from flask_login import login_required, current_user
from extensions import db
from models.placement import PlacementProfile, Internship
from models.attendance import AttendanceSummary
from models.user import User
from models.drive import PlacementDrive
from models.career_chat import CareerChatSession, CareerChatMessage
from config import Config
from utils.live_attendance import get_avg_attendance_for_student, get_live_attendance_path
from utils.career_ai import build_system_prompt, send_career_chat, MAX_HISTORY_MESSAGES
from routes.auth import limiter

placement_bp = Blueprint("placement", __name__)


@placement_bp.route("/profile", methods=["GET"])
@login_required
def profile_page():
    if "application/json" not in request.headers.get("Accept", ""):
        return render_template("placement/profile.html")
    return _profile_json()


@placement_bp.route("/api/profile", methods=["GET"])
@login_required
def api_profile():
    """Dedicated JSON endpoint the profile page's JS calls on load."""
    return _profile_json()


def _profile_json():
    profile = PlacementProfile.query.filter_by(student_id=current_user.id).first()
    avg_att, att_src = get_avg_attendance_for_student(
        get_live_attendance_path(current_app.config), current_user,
        threshold=Config.ATTENDANCE_THRESHOLD
    )
    if not profile:
        return jsonify({
            "success": True, "profile": None,
            "attendance_pct": avg_att, "attendance_source": att_src,
        })
    data = profile.to_dict()
    return jsonify({
        "success": True, "profile": data,
        "attendance_pct": avg_att, "attendance_source": att_src,
    })


@placement_bp.route("/profile", methods=["POST", "PUT"])
@login_required
def upsert_profile():
    if not current_user.is_student:
        return jsonify({"success": False, "message": "Students only."}), 403

    data    = request.get_json()
    profile = PlacementProfile.query.filter_by(student_id=current_user.id).first()
    if not profile:
        profile = PlacementProfile(student_id=current_user.id)
        db.session.add(profile)

    profile.cgpa           = float(data.get("cgpa", 0))
    profile.backlogs       = int(data.get("backlogs", 0))
    profile.skills         = data.get("skills", "")
    profile.interests      = data.get("interests", "")
    profile.certifications = data.get("certifications", "")

    avg_att, _src = get_avg_attendance_for_student(
        get_live_attendance_path(current_app.config), current_user,
        threshold=Config.ATTENDANCE_THRESHOLD
    )
    profile.calculate_readiness(avg_att)

    db.session.commit()
    return jsonify({"success": True, "profile": profile.to_dict()}), 201


@placement_bp.route("/internship", methods=["POST"])
@login_required
def add_internship():
    if not current_user.is_student:
        return jsonify({"success": False, "message": "Students only."}), 403

    profile = PlacementProfile.query.filter_by(student_id=current_user.id).first()
    if not profile:
        profile = PlacementProfile(student_id=current_user.id)
        db.session.add(profile)
        db.session.flush()   # get profile.id before creating the internship

    data = request.get_json()
    intern = Internship(
        profile_id     = profile.id,
        company        = data.get("company", ""),
        role           = data.get("role", ""),
        duration_weeks = data.get("duration_weeks"),
        stipend        = data.get("stipend"),
        status         = data.get("status", "ongoing"),
    )
    db.session.add(intern)
    db.session.commit()
    return jsonify({"success": True, "message": "Internship added.", "internship": intern.to_dict()}), 201


@placement_bp.route("/internship/<int:internship_id>", methods=["DELETE"])
@login_required
def delete_internship(internship_id):
    if not current_user.is_student:
        return jsonify({"success": False, "message": "Students only."}), 403

    profile = PlacementProfile.query.filter_by(student_id=current_user.id).first()
    intern  = Internship.query.get(internship_id)
    if not profile or not intern or intern.profile_id != profile.id:
        return jsonify({"success": False, "message": "Internship not found."}), 404

    db.session.delete(intern)
    db.session.commit()
    return jsonify({"success": True, "message": "Internship removed."})


@placement_bp.route("/eligible", methods=["GET"])
@login_required
def eligible_students():
    if current_user.is_student:
        return jsonify({"success": False, "message": "Access denied."}), 403
    if "application/json" not in request.headers.get("Accept", ""):
        return render_template("placement/eligible.html")
    return _eligible_json()


@placement_bp.route("/api/eligible", methods=["GET"])
@login_required
def api_eligible_students():
    if current_user.is_student:
        return jsonify({"success": False, "message": "Access denied."}), 403
    return _eligible_json()


def _eligible_json():
    """
    Live-recomputed eligibility list — doesn't trust the stored is_eligible
    flag (which only updates when a student manually re-saves their
    profile), so it stays accurate even if attendance changed since then.
    """
    department = request.args.get("department") or (
        None if current_user.is_admin else current_user.department
    )

    query = PlacementProfile.query.join(
        User, PlacementProfile.student_id == User.id
    )
    if department:
        query = query.filter(User.department == department)

    results = []
    for profile in query.all():
        student = db.session.get(User, profile.student_id)
        if not student:
            continue
        avg_att, _src = get_avg_attendance_for_student(
            get_live_attendance_path(current_app.config), student,
            threshold=Config.ATTENDANCE_THRESHOLD
        )
        # Recompute live instead of trusting the possibly-stale stored flag.
        is_eligible = (
            (profile.cgpa or 0) >= 6.0 and
            (profile.backlogs or 0) == 0
        )
        readiness = profile.calculate_readiness(avg_att)  # not committed — read-only preview

        results.append({
            "student_id":     student.id,
            "name":           student.name,
            "roll_number":    student.roll_number,
            "department":     student.department,
            "semester":       student.semester,
            "section":        student.section,
            "cgpa":           profile.cgpa,
            "backlogs":       profile.backlogs,
            "skills":         profile.skills,
            "attendance_pct": avg_att,
            "readiness_score": readiness,
            "is_eligible":    is_eligible,
        })

    db.session.rollback()  # discard the read-only calculate_readiness() writes above

    eligible_only = [r for r in results if r["is_eligible"]]
    return jsonify({
        "success":  True,
        "count":    len(eligible_only),
        "total":    len(results),
        "students": eligible_only,
        "all_students": results,
    })


# ── AI Career Assistant ──────────────────────────────────────────────────────

@placement_bp.route("/career-assistant", methods=["GET"])
@login_required
def career_assistant_page():
    if not current_user.is_student:
        return jsonify({"success": False, "message": "Students only."}), 403
    return render_template("placement/career_assistant.html",
                            ai_configured=bool(Config.GEMINI_API_KEY or Config.GROQ_API_KEY))


@placement_bp.route("/api/career-chat", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def career_chat():
    if not current_user.is_student:
        return jsonify({"success": False, "message": "Students only."}), 403

    data         = request.get_json() or {}
    user_message = (data.get("message") or "").strip()
    history      = data.get("history") or []
    session_id   = (data.get("session_id") or "").strip()

    if not user_message:
        return jsonify({"success": False, "message": "Please type a message."}), 400
    if len(user_message) > 2000:
        return jsonify({"success": False, "message": "Message is too long."}), 400

    # ── Get or create chat session ────────────────────────────────────────
    sess = None
    if session_id:
        sess = CareerChatSession.query.filter_by(
            session_id=session_id, student_id=current_user.id).first()
    if not sess:
        # Create new session with auto-generated title from first message
        title = user_message[:60] + ("…" if len(user_message) > 60 else "")
        sess  = CareerChatSession(
            student_id = current_user.id,
            session_id = str(uuid.uuid4()),
            title      = title,
        )
        db.session.add(sess)
        db.session.flush()   # get session_id before adding messages

    # ── Build history from DB for this session (authoritative source) ─────
    db_messages = CareerChatMessage.query.filter_by(
        session_id=sess.session_id
    ).order_by(CareerChatMessage.id.desc()).limit(MAX_HISTORY_MESSAGES).all()
    db_messages.reverse()
    clean_history = [
        {"role": m.role, "content": m.content} for m in db_messages
    ]

    profile = PlacementProfile.query.filter_by(student_id=current_user.id).first()
    avg_att, _src = get_avg_attendance_for_student(
        get_live_attendance_path(current_app.config), current_user,
        threshold=Config.ATTENDANCE_THRESHOLD
    )
    open_drives = (PlacementDrive.query.filter_by(status="open")
                   .order_by(PlacementDrive.created_at.desc()).limit(20).all())

    system_prompt = build_system_prompt(current_user, profile, avg_att, open_drives)

    success, reply = send_career_chat(
        Config.GEMINI_API_KEY or Config.GROQ_API_KEY, Config.CAREER_AI_MODEL,
        system_prompt, clean_history, user_message
    )

    if not success:
        db.session.rollback()
        return jsonify({"success": False, "message": reply}), 200

    # ── Persist both messages ─────────────────────────────────────────────
    db.session.add(CareerChatMessage(session_id=sess.session_id,
                                     role="user",      content=user_message))
    db.session.add(CareerChatMessage(session_id=sess.session_id,
                                     role="assistant", content=reply))
    db.session.commit()

    return jsonify({"success": True, "reply": reply, "session_id": sess.session_id})


# ── Chat History: load a session's messages ───────────────────────────────────

@placement_bp.route("/api/chat-history")
@login_required
def chat_history():
    if not current_user.is_student:
        return jsonify({"success": False, "message": "Students only."}), 403

    session_id = request.args.get("session_id", "").strip()

    if session_id:
        sess = CareerChatSession.query.filter_by(
            session_id=session_id, student_id=current_user.id).first()
    else:
        # Return the most recent session
        sess = (CareerChatSession.query
                .filter_by(student_id=current_user.id)
                .order_by(CareerChatSession.updated_at.desc())
                .first())

    if not sess:
        return jsonify({"success": True, "messages": [], "session_id": None})

    messages = CareerChatMessage.query.filter_by(
        session_id=sess.session_id
    ).order_by(CareerChatMessage.id).all()

    return jsonify({
        "success":    True,
        "session_id": sess.session_id,
        "title":      sess.title,
        "messages":   [m.to_dict() for m in messages],
    })


# ── List past sessions ────────────────────────────────────────────────────────

@placement_bp.route("/api/chat-sessions")
@login_required
def chat_sessions():
    if not current_user.is_student:
        return jsonify({"success": False, "message": "Students only."}), 403

    sessions = (CareerChatSession.query
                .filter_by(student_id=current_user.id)
                .order_by(CareerChatSession.updated_at.desc())
                .limit(10).all())
    return jsonify({"success": True, "sessions": [s.to_dict() for s in sessions]})


# ── Start a new session (clear chat) ─────────────────────────────────────────

@placement_bp.route("/api/chat-new", methods=["POST"])
@login_required
def chat_new_session():
    if not current_user.is_student:
        return jsonify({"success": False, "message": "Students only."}), 403
    new_id = str(uuid.uuid4())
    return jsonify({"success": True, "session_id": new_id})


# ── Delete a session ──────────────────────────────────────────────────────────

@placement_bp.route("/api/chat-session/<session_id>", methods=["DELETE"])
@login_required
def chat_delete_session(session_id):
    if not current_user.is_student:
        return jsonify({"success": False, "message": "Students only."}), 403
    sess = CareerChatSession.query.filter_by(
        session_id=session_id, student_id=current_user.id).first()
    if sess:
        db.session.delete(sess)
        db.session.commit()
    return jsonify({"success": True})
