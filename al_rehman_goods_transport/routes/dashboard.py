from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..core.auth import require_permission
from ..core.templating import render_template
from ..services import get_dashboard_metrics


router = APIRouter()


@router.get("/", name="dashboard.dashboard")
async def dashboard(request: Request, _current_user=Depends(require_permission("dashboard.view"))):
    return render_template(request, "dashboard.html", **get_dashboard_metrics())


@router.get("/dashboard", name="dashboard.dashboard_alias")
async def dashboard_alias(request: Request, _current_user=Depends(require_permission("dashboard.view"))):
    return RedirectResponse(url=str(request.url_for("dashboard.dashboard")), status_code=303)
