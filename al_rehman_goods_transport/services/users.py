from __future__ import annotations

from ..core.permissions import PERMISSION_GROUPS, ROLE_DEFINITIONS, normalize_permission_codes, normalize_role, role_permissions
from ..extensions import db
from ..models import User
from .exceptions import NotFoundError, ValidationError


class UserManagementService:
    def __init__(self, session=None):
        self.session = session or db.session

    def list_users(self):
        return self.session.query(User).order_by(User.username.asc()).all()

    def role_choices(self):
        return [(role_key, role_data["label"]) for role_key, role_data in ROLE_DEFINITIONS.items()]

    def permission_groups(self):
        return PERMISSION_GROUPS

    def create_user(self, username, email, password, confirm_password, role, permission_codes, name=None):
        normalized_username = (username or "").strip()
        normalized_email = (email or "").strip().lower()
        normalized_role = normalize_role(role)
        normalized_permissions = self._resolved_permissions(normalized_role, permission_codes)

        self._validate_identity_fields(normalized_username, normalized_email, password, confirm_password)
        self._validate_permissions(normalized_role, normalized_permissions)

        if self.session.query(User).filter(User.username.ilike(normalized_username)).first():
            raise ValidationError("Username already exists.")

        if self.session.query(User).filter(User.email.ilike(normalized_email)).first():
            raise ValidationError("Email already exists.")

        user = User(username=normalized_username, email=normalized_email, name=(name or "").strip() or None)
        user.set_role(normalized_role)
        user.set_permissions(normalized_permissions)
        user.set_password(password)
        self.session.add(user)
        self.session.commit()
        return user

    def update_user_access(self, user_id, role, permission_codes, acting_user_id=None, name=None):
        user = self._get_user(user_id)

        # The primary admin is the lock-out safety net: it always holds every
        # privilege, so its role and privilege list are not editable at all.
        if user.is_protected_admin:
            if name is not None:
                user.name = (name or "").strip() or None
            self.session.commit()
            return user

        normalized_role = normalize_role(role)
        normalized_permissions = self._resolved_permissions(normalized_role, permission_codes)

        self._validate_permissions(normalized_role, normalized_permissions)
        self._ensure_admin_not_removed(user, normalized_role, acting_user_id)

        if name is not None:
            user.name = (name or "").strip() or None
        user.set_role(normalized_role)
        user.set_permissions(normalized_permissions)
        self.session.commit()
        return user

    def reset_password(self, user_id, password, confirm_password):
        user = self._get_user(user_id)
        self._validate_password(password, confirm_password)
        user.set_password(password)
        self.session.commit()
        return user

    def get_user(self, user_id):
        return self._get_user(user_id)

    def delete_user(self, user_id, acting_user_id=None):
        user = self._get_user(user_id)
        if acting_user_id and user.id == int(acting_user_id):
            raise ValidationError("You cannot delete your own account.")
        self._ensure_admin_not_removed(user, "viewer", acting_user_id)
        try:
            self.session.delete(user)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def toggle_active(self, user_id, acting_user_id=None):
        user = self._get_user(user_id)
        if acting_user_id and user.id == int(acting_user_id):
            raise ValidationError("You cannot deactivate your own account.")
        if user.is_active and user.role == "admin":
            admin_count = self.session.query(User).filter(User.role == "admin", User.is_active.is_(True)).count()
            if admin_count <= 1:
                raise ValidationError("At least one active administrator must remain.")
        user.is_active = not user.is_active
        self.session.commit()
        return user

    def create_form_state(self):
        return {
            "name": "",
            "username": "",
            "email": "",
            "role": "operations",
            "permission_codes": role_permissions("operations"),
        }

    def _get_user(self, user_id):
        user = self.session.get(User, int(user_id))
        if user is None:
            raise NotFoundError("User not found.")
        return user

    def _resolved_permissions(self, role, permission_codes):
        # Use exactly the submitted privileges. We intentionally do NOT fall back to
        # the role defaults when the list is empty — otherwise unchecking everything to
        # reduce access would silently re-grant the whole role. A non-admin left with no
        # privileges is rejected by _validate_permissions instead.
        return normalize_permission_codes(permission_codes)

    def _validate_identity_fields(self, username, email, password, confirm_password):
        if not username:
            raise ValidationError("Username is required.")
        if not email:
            raise ValidationError("Email is required.")
        self._validate_password(password, confirm_password)

    def _validate_password(self, password, confirm_password):
        password_value = password or ""
        if len(password_value) < 8:
            raise ValidationError("Password must be at least 8 characters long.")
        if password_value != (confirm_password or ""):
            raise ValidationError("Passwords do not match.")

    def _validate_permissions(self, role, permission_codes):
        # Every editable account — administrators included — now runs on exactly
        # the privileges saved against it, so an empty list would leave the user
        # with no access at all rather than silently falling back to the role.
        if not permission_codes:
            raise ValidationError("Select at least one privilege for this user.")

    def _ensure_admin_not_removed(self, user, next_role, acting_user_id):
        if user.role != "admin" or next_role == "admin":
            return

        admin_count = self.session.query(User).filter(User.role == "admin").count()
        if admin_count <= 1:
            raise ValidationError("At least one administrator must remain assigned to the system.")

        if acting_user_id and user.id == acting_user_id:
            raise ValidationError("You cannot remove administrator access from your own account while managing users.")
