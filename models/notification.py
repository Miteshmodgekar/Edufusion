"""
Notification Models
- PushSubscription:    stores browser push subscriptions per user
- NotificationLog:     records every notification sent (email/push)
- InAppNotification:   in-app bell notifications (placement, leave, general)
"""

from extensions import db
from datetime import datetime, timezone, timedelta

_IST = timezone(timedelta(hours=5, minutes=30))
def _ist_now():
    return datetime.now(_IST).replace(tzinfo=None)


class PushSubscription(db.Model):
    """One row per device subscription per user."""
    __tablename__ = "push_subscriptions"

    id                = db.Column(db.Integer, primary_key=True)
    user_id           = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    subscription_json = db.Column(db.Text, nullable=False)
    user_agent        = db.Column(db.String(200), nullable=True)
    created_at        = db.Column(db.DateTime, default=_ist_now)

    user = db.relationship("User", backref="push_subscriptions")

    def to_dict(self):
        return {
            "id":         self.id,
            "user_id":    self.user_id,
            "created_at": self.created_at.isoformat(),
        }


class NotificationLog(db.Model):
    """Record of every notification dispatched (email/push)."""
    __tablename__ = "notification_logs"

    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    channel    = db.Column(db.String(20), nullable=False)
    event_type = db.Column(db.String(50), nullable=False)
    title      = db.Column(db.String(200), nullable=True)
    body       = db.Column(db.Text, nullable=True)
    status     = db.Column(db.String(20), default="sent")
    error_msg  = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=_ist_now)

    user = db.relationship("User", backref="notification_logs")

    @classmethod
    def log(cls, user_id, channel, event_type, title, body, status="sent", error=None):
        entry = cls(
            user_id    = user_id,
            channel    = channel,
            event_type = event_type,
            title      = title,
            body       = body,
            status     = status,
            error_msg  = error,
        )
        db.session.add(entry)
        db.session.commit()
        return entry

    def to_dict(self):
        return {
            "id":         self.id,
            "user_id":    self.user_id,
            "channel":    self.channel,
            "event_type": self.event_type,
            "title":      self.title,
            "body":       self.body,
            "status":     self.status,
            "created_at": self.created_at.isoformat(),
        }


class InAppNotification(db.Model):
    """
    In-app bell notifications shown in the navbar.
    Created by the AI matching engine for placement drives,
    and by other events (leave approved, etc.).
    """
    __tablename__ = "in_app_notifications"

    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title      = db.Column(db.String(200), nullable=False)
    body       = db.Column(db.Text, nullable=True)
    icon       = db.Column(db.String(50), default="bi-bell")       # Bootstrap icon name
    color      = db.Column(db.String(20), default="#58a6ff")       # badge color
    notif_type = db.Column(db.String(30), default="general")       # placement|leave|general
    ref_id     = db.Column(db.Integer, nullable=True)              # e.g. drive_id
    ref_url    = db.Column(db.String(200), nullable=True)          # click target
    is_read    = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=_ist_now)

    user = db.relationship("User", backref="in_app_notifications")

    def to_dict(self):
        return {
            "id":         self.id,
            "user_id":    self.user_id,
            "title":      self.title,
            "body":       self.body,
            "icon":       self.icon,
            "color":      self.color,
            "notif_type": self.notif_type,
            "ref_id":     self.ref_id,
            "ref_url":    self.ref_url,
            "is_read":    self.is_read,
            "created_at": self.created_at.isoformat(),
        }
