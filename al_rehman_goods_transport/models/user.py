import json

from werkzeug.security import generate_password_hash, check_password_hash

from ..core.permissions import ALL_PERMISSION_CODES, grants_permission, normalize_permission_codes, normalize_role, role_label
from ..extensions import db

class User(db.Model):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    role = db.Column(db.String(50), default="admin", nullable=False)
    permissions = db.Column(db.Text, default="[]", nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    last_login_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def set_role(self, role):
        self.role = normalize_role(role)
        self.is_admin = self.role == "admin"

    def set_permissions(self, permission_codes):
        self.permissions = json.dumps(normalize_permission_codes(permission_codes))

    @property
    def permission_codes(self):
        if self.is_admin or self.role == "admin":
            return list(ALL_PERMISSION_CODES)

        try:
            raw_permissions = json.loads(self.permissions or "[]")
        except json.JSONDecodeError:
            raw_permissions = []
        return normalize_permission_codes(raw_permissions)

    def can(self, permission_code):
        if self.is_admin or self.role == "admin":
            return True
        return grants_permission(self.permission_codes, permission_code)

    @property
    def role_label(self):
        return role_label(self.role)

    @property
    def is_authenticated(self):
        return True

    def __repr__(self):
        return f"<User {self.username}>"
