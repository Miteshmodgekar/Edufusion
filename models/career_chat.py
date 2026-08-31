"""
CareerChatMessage — Persistent storage for AI Career Assistant conversations.
Each student can have multiple sessions (conversations). Messages are stored
per session so the student can revisit past career guidance.
"""
from extensions import db
from datetime import datetime, timezone, timedelta

_IST = timezone(timedelta(hours=5, minutes=30))
def _ist_now():
    return datetime.now(_IST).replace(tzinfo=None)


class CareerChatSession(db.Model):
    """One conversation thread (session) for a student."""
    __tablename__ = "career_chat_sessions"

    id         = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    session_id = db.Column(db.String(36), nullable=False, unique=True, index=True)
    title      = db.Column(db.String(120), nullable=True)   # auto-generated from first message
    created_at = db.Column(db.DateTime, default=_ist_now)
    updated_at = db.Column(db.DateTime, default=_ist_now, onupdate=_ist_now)

    messages = db.relationship("CareerChatMessage", backref="session",
                               lazy=True, cascade="all, delete-orphan",
                               order_by="CareerChatMessage.id")

    def to_dict(self, include_messages=False):
        d = {
            "session_id": self.session_id,
            "title":      self.title or "Career Chat",
            "created_at": self.created_at.strftime("%d %b %Y") if self.created_at else None,
            "updated_at": self.updated_at.strftime("%d %b, %I:%M %p") if self.updated_at else None,
            "msg_count":  len(self.messages),
        }
        if include_messages:
            d["messages"] = [m.to_dict() for m in self.messages]
        return d


class CareerChatMessage(db.Model):
    """Single message (user or AI) within a career chat session."""
    __tablename__ = "career_chat_messages"

    id         = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(36), db.ForeignKey("career_chat_sessions.session_id"),
                           nullable=False, index=True)
    role       = db.Column(db.String(16), nullable=False)   # 'user' | 'assistant'
    content    = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=_ist_now)

    def to_dict(self):
        return {
            "id":         self.id,
            "role":       self.role,
            "content":    self.content,
            "created_at": self.created_at.strftime("%I:%M %p") if self.created_at else None,
        }
