from datetime import UTC, datetime

from ..extensions import db


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


class AppSetting(db.Model):
    __tablename__ = "app_setting"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.String(255), nullable=True)
    updated_at = db.Column(db.DateTime, default=utc_now, nullable=False, onupdate=utc_now)

    def __repr__(self):
        return f"<AppSetting {self.key}>"
