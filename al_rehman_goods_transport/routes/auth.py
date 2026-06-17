from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..core.auth import get_optional_user, login_user, logout_user
from ..core.database import get_db
from ..core.flash import flash
from ..core.templating import render_template
from ..models import User
from ..services import UserManagementService, ValidationError


router = APIRouter()


@router.api_route("/login", methods=["GET", "POST"], name="auth.login")
async def login(request: Request, db: Session = Depends(get_db)):
    if getattr(get_optional_user(request), "is_authenticated", False):
        return RedirectResponse(url=str(request.url_for("dashboard.dashboard")), status_code=303)

    if request.method == "POST":
        form_data = await request.form()
        username = (form_data.get("username") or "").strip()
        password = form_data.get("password") or ""
        user = db.query(User).filter_by(username=username).first()

        if user and user.check_password(password) and getattr(user, "is_active", True):
            user.last_login_at = datetime.now(UTC).replace(tzinfo=None)
            db.commit()
            login_user(request, user)
            flash(request, "Logged in successfully.", "success")
            next_page = request.query_params.get("next")
            return RedirectResponse(url=next_page or str(request.url_for("dashboard.dashboard")), status_code=303)

        flash(request, "Invalid username or password.", "danger")

    return render_template(
        request,
        "auth/login.html",
        show_nav=False,
        body_class="auth-page",
        main_class="auth-main",
    )


@router.get("/logout", name="auth.logout")
async def logout(request: Request):
    logout_user(request)
    flash(request, "You have been logged out.", "info")
    return RedirectResponse(url=str(request.url_for("auth.login")), status_code=303)


@router.api_route("/register", methods=["GET", "POST"], name="auth.register")
async def register(request: Request, db: Session = Depends(get_db)):
    existing_user_count = db.query(User).count()
    current_user = get_optional_user(request)

    if existing_user_count > 0:
        if getattr(current_user, "is_authenticated", False) and current_user.can("users.manage"):
            flash(request, "Create and manage users from Settings.", "info")
            return RedirectResponse(url=str(request.url_for("settings.index")), status_code=303)

        flash(request, "Only administrators can create additional users.", "warning")
        return RedirectResponse(url=str(request.url_for("auth.login")), status_code=303)

    if request.method == "POST":
        form_data = await request.form()
        try:
            UserManagementService(db).create_user(
                form_data.get("username"),
                form_data.get("email"),
                form_data.get("password"),
                form_data.get("confirm_password"),
                "admin",
                None,
            )
            flash(request, "Administrator account created successfully. You can now log in.", "success")
            return RedirectResponse(url=str(request.url_for("auth.login")), status_code=303)
        except ValidationError as exc:
            flash(request, str(exc), "danger")

    return render_template(
        request,
        "auth/register.html",
        show_nav=False,
        body_class="auth-page",
        main_class="auth-main",
    )
