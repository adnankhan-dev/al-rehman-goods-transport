from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ..core.auth import require_any_permission, require_permission
from ..core.permissions import PERMISSION_GROUPS, ROLE_DEFINITIONS, role_permissions
from ..core.flash import flash
from ..core.templating import render_template
from ..forms import SettingsForm
from ..repositories import LookupRepository
from ..services import NotFoundError, ReconciliationService, SettingsService, UserManagementService, ValidationError
from ..services.audit import list_audit_entries, record_audit
from ..services.backups import automatic_backup_status, run_automatic_backup
from ..utils.pagination import paginate_list, parse_page


router = APIRouter()


@router.get("/settings/backup/download", name="settings.download_backup")
async def download_backup(_request: Request, _current_user=Depends(require_permission("settings.manage"))):
    backup = SettingsService().create_backup()
    return Response(
        content=backup["content"],
        media_type=backup["media_type"],
        headers={"Content-Disposition": f'attachment; filename="{backup["filename"]}"'},
    )


@router.get("/settings/backup/download-data", name="settings.download_data_backup")
async def download_data_backup(_request: Request, _current_user=Depends(require_permission("settings.manage"))):
    backup = SettingsService().create_data_snapshot()
    return Response(
        content=backup["content"],
        media_type=backup["media_type"],
        headers={"Content-Disposition": f'attachment; filename="{backup["filename"]}"'},
    )


@router.post("/settings/backup/restore", name="settings.restore_backup")
async def restore_backup(request: Request, current_user=Depends(require_permission("settings.manage"))):
    form_data = await request.form()
    uploaded_file = form_data.get("backup_file")

    if not hasattr(uploaded_file, "read"):
        flash(request, "Select a backup file to restore.", "warning")
        return RedirectResponse(url=str(request.url_for("settings.index")), status_code=303)

    try:
        content = await uploaded_file.read()
        SettingsService().restore_backup(getattr(uploaded_file, "filename", ""), content)
        record_audit(current_user, "restore", "database", None, f"Database restored from {getattr(uploaded_file, 'filename', 'uploaded file')}")
        flash(request, "Backup restored successfully.", "success")
    except ValidationError as exc:
        flash(request, str(exc), "warning")
    finally:
        close_method = getattr(uploaded_file, "close", None)
        if callable(close_method):
            maybe_awaitable = close_method()
            if hasattr(maybe_awaitable, "__await__"):
                await maybe_awaitable

    return RedirectResponse(url=str(request.url_for("settings.index")), status_code=303)


@router.api_route("/settings", methods=["GET", "POST"], name="settings.index")
async def settings_dashboard(request: Request, current_user=Depends(require_any_permission("settings.view", "users.manage"))):
    service = SettingsService()
    user_service = UserManagementService()
    form_data = await request.form() if request.method == "POST" else None
    form = SettingsForm(form_data if request.method == "POST" else None)

    if request.method == "GET":
        diesel_rate = service.get_diesel_rate()
        form.diesel_rate.data = diesel_rate

    if request.method == "POST":
        action = (form_data.get("action") or "").strip()

        if action == "update_diesel":
            if not current_user.can("settings.manage"):
                flash(request, "You do not have permission to update ERP settings.", "warning")
                return RedirectResponse(url=str(request.url_for("settings.index")), status_code=303)

            if form.validate():
                try:
                    service.update_diesel_rate(form.diesel_rate.data)
                    flash(request, "Diesel rate updated successfully.", "success")
                    return RedirectResponse(url=str(request.url_for("settings.index")), status_code=303)
                except ValidationError as exc:
                    flash(request, str(exc), "warning")

    return render_template(
        request,
        "settings/index.html",
        form=form,
        users=user_service.list_users(),
        can_manage_settings=current_user.can("settings.manage"),
        can_manage_users=current_user.can("users.manage"),
        backup_supported=service.backup_supported(),
        database_filename=service.database_filename(),
        auto_backup=automatic_backup_status(),
        contractors=LookupRepository().list_contractors(),
        current_month=datetime.now().strftime("%Y-%m"),
        **service.diesel_rate_context(),
    )


@router.get("/settings/manual-entry-form", name="settings.manual_entry_form")
async def manual_entry_form(request: Request, _current_user=Depends(require_any_permission("orders.view", "settings.view"))):
    contractor_id = request.query_params.get("contractor_id")
    month_value = (request.query_params.get("month") or "").strip()  # expected "YYYY-MM"
    try:
        year, month = (int(part) for part in month_value.split("-", 1))
        datetime(year, month, 1)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Select a valid month (YYYY-MM).")

    lookups = LookupRepository()
    contractor = next((c for c in lookups.list_contractors() if str(c.id) == str(contractor_id)), None)
    contractor_name = contractor.name if contractor else "All Contractors"

    html = SettingsService().build_manual_entry_form_html(contractor_name, year, month)
    return HTMLResponse(content=html)


@router.post("/settings/backup/run-now", name="settings.run_backup_now")
async def run_backup_now(request: Request, _current_user=Depends(require_permission("settings.manage"))):
    target = run_automatic_backup()
    if target is None:
        flash(request, "Automatic backups are only available for SQLite databases.", "warning")
    else:
        flash(request, f"Backup written: {target.name}", "success")
    return RedirectResponse(url=str(request.url_for("settings.index")), status_code=303)


# ── Balance reconciliation ─────────────────────────────────────────────────────

@router.get("/settings/reconciliation", name="settings.reconciliation")
async def reconciliation(request: Request, _current_user=Depends(require_permission("settings.manage"))):
    report = ReconciliationService().build_report()
    return render_template(request, "settings/reconciliation.html", **report)


@router.post("/settings/reconciliation/repair", name="settings.reconciliation_repair")
async def reconciliation_repair(request: Request, current_user=Depends(require_permission("settings.manage"))):
    form_data = await request.form()
    entity_type = (form_data.get("entity_type") or "").strip()
    try:
        entity_id = int(form_data.get("entity_id") or 0)
    except (TypeError, ValueError):
        entity_id = 0

    try:
        row = ReconciliationService().repair(entity_type, entity_id)
        record_audit(current_user, "repair", entity_type, entity_id, f"{row['name']} balance set to Rs. {row['expected_balance']:,.2f} (was Rs. {row['stored_balance']:,.2f})")
        flash(request, f"Balance for {row['name']} set to Rs. {row['expected_balance']:,.2f} (was Rs. {row['stored_balance']:,.2f}).", "success")
    except (NotFoundError, ValidationError) as exc:
        flash(request, str(exc), "warning")

    return RedirectResponse(url=str(request.url_for("settings.reconciliation")), status_code=303)


# ── Audit trail ────────────────────────────────────────────────────────────────

@router.get("/settings/audit", name="settings.audit")
async def audit_trail(request: Request, _current_user=Depends(require_permission("settings.manage"))):
    page = parse_page(request.query_params.get("page"))
    pagination = paginate_list(list_audit_entries(), page, per_page=100)
    return render_template(request, "settings/audit.html", entries=pagination["items"], pagination=pagination)


# ── User management ────────────────────────────────────────────────────────────

@router.api_route("/settings/users/create", methods=["GET", "POST"], name="settings.users_create")
async def users_create(request: Request, current_user=Depends(require_permission("users.manage"))):
    user_service = UserManagementService()
    form_state = user_service.create_form_state()

    if request.method == "POST":
        form_data = await request.form()
        role = (form_data.get("role") or "operations").strip()
        form_state = {
            "username": (form_data.get("username") or "").strip(),
            "email": (form_data.get("email") or "").strip(),
            "role": role,
            "permission_codes": form_data.getlist("permission_codes") or role_permissions(role),
        }
        try:
            user = user_service.create_user(
                form_data.get("username"),
                form_data.get("email"),
                form_data.get("password"),
                form_data.get("confirm_password"),
                role,
                form_data.getlist("permission_codes") or role_permissions(role),
            )
            flash(request, f"User {user.username} created successfully.", "success")
            return RedirectResponse(url=str(request.url_for("settings.index")), status_code=303)
        except ValidationError as exc:
            flash(request, str(exc), "warning")

    return render_template(
        request,
        "settings/users/create.html",
        form_state=form_state,
        role_definitions=ROLE_DEFINITIONS,
        permission_groups=PERMISSION_GROUPS,
    )


@router.api_route("/settings/users/{user_id}/edit", methods=["GET", "POST"], name="settings.users_edit")
async def users_edit(user_id: int, request: Request, current_user=Depends(require_permission("users.manage"))):
    user_service = UserManagementService()
    try:
        user = user_service.get_user(user_id)
    except NotFoundError as exc:
        flash(request, str(exc), "warning")
        return RedirectResponse(url=str(request.url_for("settings.index")), status_code=303)

    if request.method == "POST":
        form_data = await request.form()
        action = (form_data.get("action") or "").strip()

        if action == "update_access":
            role = (form_data.get("role") or "viewer").strip()
            permission_codes = form_data.getlist("permission_codes") or role_permissions(role)
            try:
                user_service.update_user_access(user_id, role, permission_codes, acting_user_id=current_user.id)
                flash(request, f"Access updated for {user.username}.", "success")
                return RedirectResponse(url=str(request.url_for("settings.users_edit", user_id=user_id)), status_code=303)
            except ValidationError as exc:
                flash(request, str(exc), "warning")

        elif action == "reset_password":
            try:
                user_service.reset_password(user_id, form_data.get("new_password"), form_data.get("confirm_new_password"))
                flash(request, f"Password reset for {user.username}.", "success")
                return RedirectResponse(url=str(request.url_for("settings.users_edit", user_id=user_id)), status_code=303)
            except (NotFoundError, ValidationError) as exc:
                flash(request, str(exc), "warning")

    return render_template(
        request,
        "settings/users/edit.html",
        edited_user=user,
        role_definitions=ROLE_DEFINITIONS,
        permission_groups=PERMISSION_GROUPS,
        is_self=current_user.id == user.id,
    )


@router.post("/settings/users/{user_id}/delete", name="settings.users_delete")
async def users_delete(user_id: int, request: Request, current_user=Depends(require_permission("users.manage"))):
    user_service = UserManagementService()
    try:
        user_service.delete_user(user_id, acting_user_id=current_user.id)
        flash(request, "User deleted.", "success")
    except (NotFoundError, ValidationError) as exc:
        flash(request, str(exc), "warning")
    return RedirectResponse(url=str(request.url_for("settings.index")), status_code=303)


@router.post("/settings/users/{user_id}/toggle-active", name="settings.users_toggle_active")
async def users_toggle_active(user_id: int, request: Request, current_user=Depends(require_permission("users.manage"))):
    user_service = UserManagementService()
    try:
        user = user_service.toggle_active(user_id, acting_user_id=current_user.id)
        state = "activated" if user.is_active else "deactivated"
        flash(request, f"{user.username} {state}.", "success")
    except (NotFoundError, ValidationError) as exc:
        flash(request, str(exc), "warning")
    return RedirectResponse(url=str(request.url_for("settings.index")), status_code=303)
