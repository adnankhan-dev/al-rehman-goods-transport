from datetime import UTC, datetime

from ..extensions import db


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    user_id = db.Column(db.Integer, nullable=True)
    username = db.Column(db.String(100), nullable=False, default="system")
    action = db.Column(db.String(30), nullable=False)
    entity_type = db.Column(db.String(50), nullable=False)
    entity_id = db.Column(db.Integer, nullable=True)
    summary = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f"<AuditLog {self.action} {self.entity_type}:{self.entity_id} by {self.username}>"
