from ..extensions import db
from ..models import AuditLog


def record_audit(user, action, entity_type, entity_id=None, summary=None, session=None):
    """Append an audit trail entry. Never raises — a failed audit write must
    not roll back or mask the business action it documents."""
    session = session or db.session
    try:
        session.add(
            AuditLog(
                user_id=getattr(user, "id", None),
                username=getattr(user, "username", None) or "system",
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                summary=summary,
            )
        )
        session.commit()
    except Exception:
        session.rollback()


def list_audit_entries(session=None):
    session = session or db.session
    return session.query(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).all()


def list_entity_audit(entity_type, entity_id, session=None):
    """Full audit history (created/updated/approved/…) for one record, oldest
    first — used to show a per-record change log on view pages."""
    session = session or db.session
    if entity_id is None:
        return []
    return (
        session.query(AuditLog)
        .filter(AuditLog.entity_type == entity_type, AuditLog.entity_id == int(entity_id))
        .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
        .all()
    )
