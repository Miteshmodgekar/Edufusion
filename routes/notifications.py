"""In-App Notification Routes — bell icon API."""

from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from extensions import db
from models.notification import InAppNotification

notify_bp = Blueprint("notify", __name__)


@notify_bp.route("/api/mine", methods=["GET"])
@login_required
def my_notifications():
    """Return all notifications for the current user, newest first."""
    notifs = (InAppNotification.query
              .filter_by(user_id=current_user.id)
              .order_by(InAppNotification.created_at.desc())
              .limit(30)
              .all())
    unread = sum(1 for n in notifs if not n.is_read)
    return jsonify({
        "success":       True,
        "notifications": [n.to_dict() for n in notifs],
        "unread":        unread,
    })


@notify_bp.route("/api/read/<int:notif_id>", methods=["POST"])
@login_required
def mark_read(notif_id):
    notif = InAppNotification.query.get_or_404(notif_id)
    if notif.user_id != current_user.id:
        return jsonify({"success": False, "message": "Access denied."}), 403
    notif.is_read = True
    db.session.commit()
    return jsonify({"success": True})


@notify_bp.route("/api/read-all", methods=["POST"])
@login_required
def mark_all_read():
    (InAppNotification.query
     .filter_by(user_id=current_user.id, is_read=False)
     .update({"is_read": True}))
    db.session.commit()
    return jsonify({"success": True})


@notify_bp.route("/api/clear", methods=["DELETE"])
@login_required
def clear_all_notifications():
    """Permanently delete all in-app notifications for the current user."""
    try:
        InAppNotification.query.filter_by(user_id=current_user.id).delete()
        db.session.commit()
        return jsonify({"success": True, "message": "All notifications cleared."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
