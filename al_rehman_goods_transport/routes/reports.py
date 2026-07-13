from datetime import datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..core.auth import require_permission
from ..core.templating import render_template
from ..services import ReportService


router = APIRouter()


def _parse_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


def _report_filter_state(source):
    service = ReportService()
    date_from = _parse_date(source.get("date_from"))
    date_to = _parse_date(source.get("date_to"))

    # First visit (no submitted filters): default to the current month so the
    # report opens fast on large databases. The filter form always submits
    # report_type, so an explicit "Apply" with blank dates still means all-time.
    if "report_type" not in source and date_from is None and date_to is None:
        now = datetime.now()
        date_from = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        date_to = now

    return {
        "report_type": service.normalize_report_type((source.get("report_type") or "profit_loss").strip()),
        "contractor_id": _parse_int(source.get("contractor_id")),
        "site_id": _parse_int(source.get("site_id")),
        "from_site_id": _parse_int(source.get("from_site_id")),
        "material_id": _parse_int(source.get("material_id")),
        "vehicle_id": _parse_int(source.get("vehicle_id")),
        "plant_id": _parse_int(source.get("plant_id")),
        "petrol_pump_id": _parse_int(source.get("petrol_pump_id")),
        "billing_status": (source.get("billing_status") or "").strip(),
        "date_from": date_from,
        "date_to": date_to,
        "search": (source.get("search") or "").strip(),
    }


def _report_query_params(filter_state):
    return {
        "report_type": filter_state["report_type"],
        "contractor_id": filter_state["contractor_id"] or "",
        "site_id": filter_state["site_id"] or "",
        "from_site_id": filter_state["from_site_id"] or "",
        "material_id": filter_state["material_id"] or "",
        "vehicle_id": filter_state["vehicle_id"] or "",
        "plant_id": filter_state["plant_id"] or "",
        "petrol_pump_id": filter_state["petrol_pump_id"] or "",
        "billing_status": filter_state["billing_status"],
        "date_from": filter_state["date_from"].strftime("%Y-%m-%d") if filter_state["date_from"] else "",
        "date_to": filter_state["date_to"].strftime("%Y-%m-%d") if filter_state["date_to"] else "",
        "search": filter_state["search"],
    }


def _legacy_redirect(request: Request, report_type: str = "profit_loss"):
    query = _report_query_params(_report_filter_state(request.query_params))
    query["report_type"] = report_type
    target_url = f"{request.url_for('reports.index')}?{urlencode(query)}"
    return RedirectResponse(url=target_url, status_code=303)


@router.get("/reports", name="reports.index")
async def index(request: Request, _current_user=Depends(require_permission("reports.view"))):
    filter_state = _report_filter_state(request.query_params)
    report_service = ReportService()
    context = report_service.workspace_context(filter_state)
    return render_template(
        request,
        "reports/index.html",
        report_query=_report_query_params(filter_state),
        **context,
    )


@router.get("/reports/print", name="reports.print_report")
async def print_report(request: Request, _current_user=Depends(require_permission("reports.view"))):
    filter_state = _report_filter_state(request.query_params)
    report_service = ReportService()
    context = report_service.workspace_context(filter_state)
    return render_template(
        request,
        "reports/print.html",
        show_nav=False,
        body_class="report-print-page",
        report_query=_report_query_params(filter_state),
        **context,
    )


@router.get("/reports/consolidated", name="reports.consolidated_report")
async def consolidated_report(request: Request, current_user=Depends(require_permission("reports.view"))):
    """The printable period 'file pack': position summary, P&L, one Account
    Summary statement per account, then the trips / fuel / ledger registers."""
    from ..services.consolidated_report import consolidated_report_context

    date_from = _parse_date(request.query_params.get("date_from"))
    date_to = _parse_date(request.query_params.get("date_to"))
    # Default to the current month so the pack always has a defined period.
    if date_from is None and date_to is None:
        now = datetime.now()
        date_from = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        date_to = now
    context = consolidated_report_context(
        date_from=date_from.date() if date_from else None,
        date_to=date_to.date() if date_to else None,
    )
    return render_template(
        request,
        "reports/consolidated.html",
        show_nav=False,
        prepared_by=getattr(current_user, "username", None),
        **context,
    )


@router.get("/reports/financial", name="reports.financial_reports")
async def financial_reports(request: Request, _current_user=Depends(require_permission("reports.view"))):
    return _legacy_redirect(request, report_type="profit_loss")


@router.get("/reports/operational", name="reports.operational_reports")
async def operational_reports(request: Request, _current_user=Depends(require_permission("reports.view"))):
    return _legacy_redirect(request, report_type="profit_loss")


@router.get("/reports/entity", name="reports.entity_reports")
async def entity_reports(request: Request, _current_user=Depends(require_permission("reports.view"))):
    return _legacy_redirect(request, report_type="plants")


@router.get("/reports/materials", name="reports.material_reports")
async def material_reports(request: Request, _current_user=Depends(require_permission("reports.view"))):
    return _legacy_redirect(request, report_type="plants")


@router.get("/reports/time", name="reports.time_reports")
async def time_reports(request: Request, _current_user=Depends(require_permission("reports.view"))):
    return _legacy_redirect(request, report_type="diesel")
