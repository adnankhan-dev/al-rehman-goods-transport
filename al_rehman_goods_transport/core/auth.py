from dataclasses import dataclass
from urllib.parse import quote

from fastapi import HTTPException, Request, status

from .flash import flash
from ..models import User
from .database import SessionLocal


@dataclass
class AnonymousUser:
    id: int | None = None
    username: str = "Guest"
    is_authenticated: bool = False
    is_admin: bool = False
    role: str = "viewer"

    @property
    def permission_codes(self):
        return []

    def can(self, _permission_code: str) -> bool:
        return False


def get_optional_user(request: Request):
    current_user = getattr(request.state, "current_user", None)
    if getattr(current_user, "is_authenticated", False):
        return current_user

    try:
        user_id = request.session.get("user_id")
    except AssertionError:
        return current_user or AnonymousUser()

    if not user_id:
        return current_user or AnonymousUser()

    db = SessionLocal()
    try:
        user = db.get(User, int(user_id))
        if user is None:
            request.session.pop("user_id", None)
            return AnonymousUser()
        request.state.current_user = user
        return user
    finally:
        db.close()
        SessionLocal.remove()


def require_user(request: Request):
    user = get_optional_user(request)
    if getattr(user, "is_authenticated", False):
        request.state.current_user = user
        return user

    next_path = quote(request.url.path)
    raise HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Location": f"/login?next={next_path}"},
    )


def login_user(request: Request, user: User):
    request.session["user_id"] = user.id
    request.state.current_user = user


def logout_user(request: Request):
    request.session.clear()
    request.state.current_user = AnonymousUser()


def _permission_denied_redirect(request: Request, user, redirect_route: str):
    preferred_routes = [
        ("dashboard.view", "dashboard.dashboard"),
        ("orders.view", "orders.orders"),
        ("ledger.view", "ledger.index"),
        ("reports.view", "reports.index"),
        ("settings.view", "settings.index"),
        ("users.manage", "settings.index"),
    ]

    if redirect_route != "dashboard.dashboard":
        return str(request.url_for(redirect_route))

    for permission_code, route_name in preferred_routes:
        if getattr(user, "can", lambda _code: False)(permission_code):
            return str(request.url_for(route_name))

    return str(request.url_for("auth.logout"))


def require_permission(permission_code: str, message: str | None = None, redirect_route: str = "dashboard.dashboard"):
    def dependency(request: Request):
        user = require_user(request)
        if getattr(user, "can", None) and user.can(permission_code):
            return user

        flash(request, message or "You do not have permission to access that page.", "warning")
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": _permission_denied_redirect(request, user, redirect_route)},
        )

    return dependency


def require_any_permission(*permission_codes: str, message: str | None = None, redirect_route: str = "dashboard.dashboard"):
    def dependency(request: Request):
        user = require_user(request)
        if any(getattr(user, "can", lambda _code: False)(permission_code) for permission_code in permission_codes):
            return user

        flash(request, message or "You do not have permission to access that page.", "warning")
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": _permission_denied_redirect(request, user, redirect_route)},
        )

    return dependency
