from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from starlette.datastructures import QueryParams

from ..core.auth import require_permission
from ..core.flash import flash
from ..core.templating import render_template, render_template_string
from ..models import Contractor, PetrolPump, Plant, VehicleOwner
from ..services import BillingService, NotFoundError, ValidationError
from ..services.audit import record_audit


router = APIRouter()


def _parse_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_int_list(values):
    return [value for value in (_parse_int(item) for item in values) if value is not None]


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


def _build_filter_state(source):
    return {
        "entity_type": (source.get("entity_type") or "contractor").strip() or "contractor",
        "entity_id": _parse_int(source.get("entity_id") or source.get("contractor_id")),
        "site_ids": _parse_int_list(source.getlist("site_ids")) if hasattr(source, "getlist") else [],
        "material_type": (source.get("material_type") or "").strip(),
        "start_date": _parse_date(source.get("start_date")),
        "end_date": _parse_date(source.get("end_date")),
        "notes": (source.get("notes") or "").strip(),
    }


def _bill_create_context(filter_state, selected_order_ids=None, selected_entry_ids=None, selected_loading_ids=None):
    service = BillingService()
    context = service.bill_context(
        filter_state["entity_type"],
        filter_state["entity_id"],
        filter_state["site_ids"],
        filter_state["material_type"] or None,
        filter_state["start_date"],
        filter_state["end_date"],
    )
    options = service.filter_options(filter_state["entity_type"], filter_state["entity_id"])
    return {
        "contractors": Contractor.query.order_by(Contractor.name.asc()).all(),
        "plants": Plant.query.order_by(Plant.name.asc()).all(),
        "petrol_pumps": PetrolPump.query.order_by(PetrolPump.name.asc()).all(),
        "vehicle_owners": VehicleOwner.query.order_by(VehicleOwner.name.asc()).all(),
        "filter_state": filter_state,
        "available_sites": options["sites"],
        "available_materials": options["materials"],
        "selected_order_ids": selected_order_ids or [],
        "selected_entry_ids": selected_entry_ids or [],
        "selected_loading_ids": selected_loading_ids or [],
        **context,
    }


@router.get("/bills", name="bills.index")
async def index(request: Request, _current_user=Depends(require_permission("ledger.view"))):
    return RedirectResponse(url=str(request.url_for("ledger.index")), status_code=303)


@router.api_route("/bills/create", methods=["GET", "POST"], name="bills.create_bill")
async def create_bill(request: Request, current_user=Depends(require_permission("ledger.create"))):
    service = BillingService()
    if request.method == "GET":
        query_string = str(QueryParams(request.query_params.multi_items()))
        target_url = str(request.url_for("ledger.create"))
        if query_string:
            target_url = f"{target_url}?mode=bill&{query_string}"
        else:
            target_url = f"{target_url}?mode=bill"
        return RedirectResponse(url=target_url, status_code=303)

    if request.method == "POST":
        form_data = await request.form()
        filter_state = _build_filter_state(form_data)
        selected_order_ids = _parse_int_list(form_data.getlist("order_ids"))
        selected_entry_ids = _parse_int_list(form_data.getlist("entry_ids"))
        selected_loading_ids = _parse_int_list(form_data.getlist("loading_ids"))
        try:
            bill, selected_orders = service.create_bill(
                filter_state["entity_type"],
                filter_state["entity_id"],
                order_ids=selected_order_ids,
                entry_ids=selected_entry_ids,
                loading_ids=selected_loading_ids,
                site_ids=filter_state["site_ids"],
                material_type=filter_state["material_type"] or None,
                start_date=filter_state["start_date"],
                end_date=filter_state["end_date"],
                notes=filter_state["notes"] or None,
            )
            record_audit(current_user, "create", "bill", bill.id, f"Bill {bill.bill_number} for {bill.entity_name} — Rs. {bill.total_amount:,.2f}")
            flash(request, f"Bill {bill.bill_number} created for {bill.entity_name} and submitted for approval.", "success")
            return RedirectResponse(url=str(request.url_for("bills.view_bill", id=bill.id)), status_code=303)
        except (NotFoundError, ValidationError) as exc:
            flash(request, str(exc), "warning")
            return render_template(request, "bills/create.html", **_bill_create_context(filter_state, selected_order_ids, selected_entry_ids, selected_loading_ids), status_code=400)

    filter_state = _build_filter_state(request.query_params)
    return render_template(request, "bills/create.html", **_bill_create_context(filter_state))


@router.get("/bills/{id}", name="bills.view_bill")
async def view_bill(id: int, request: Request, _current_user=Depends(require_permission("ledger.view"))):
    service = BillingService()
    try:
        snapshot = service.bill_snapshot(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return render_template(request, "bills/view.html", **snapshot)


@router.get("/bills/{id}/print", name="bills.print_bill")
async def print_bill(id: int, request: Request, _current_user=Depends(require_permission("ledger.view"))):
    service = BillingService()
    try:
        snapshot = service.bill_snapshot(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return render_template(request, "bills/print.html", show_nav=False, **snapshot)


@router.get("/bills/{id}/export/excel", name="bills.export_bill_excel")
async def export_bill_excel(id: int, _current_user=Depends(require_permission("ledger.view"))):
    service = BillingService()
    try:
        filename, content = service.export_bill_excel(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return Response(
        content=content,
        media_type="application/vnd.ms-excel",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/bills/{id}/export/pdf", name="bills.export_bill_pdf")
async def export_bill_pdf(id: int, request: Request, _current_user=Depends(require_permission("ledger.view"))):
    service = BillingService()
    try:
        snapshot = service.bill_snapshot(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    html = render_template_string(request, "bills/print.html", show_nav=False, **snapshot)
    try:
        filename, content = service.render_bill_pdf(snapshot["bill"], html)
    except ValidationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/bills/{id}/delete", name="bills.delete_bill")
async def delete_bill(id: int, request: Request, current_user=Depends(require_permission("ledger.delete"))):
    service = BillingService()
    try:
        bill_number = service.delete_bill(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        flash(request, str(exc), "warning")
        return RedirectResponse(url=str(request.url_for("bills.view_bill", id=id)), status_code=303)

    record_audit(current_user, "delete", "bill", id, f"Bill {bill_number} deleted; linked records released for re-billing.")
    flash(request, f"Bill {bill_number} deleted. Its trips and entries are available to bill again.", "success")
    return RedirectResponse(url=str(request.url_for("ledger.index")), status_code=303)


@router.post("/bills/{id}/approve", name="bills.approve_bill")
async def approve_bill(id: int, request: Request, current_user=Depends(require_permission("ledger.approve"))):
    service = BillingService()
    try:
        bill = service.approve_bill(id, approver_id=getattr(current_user, "id", None))
        record_audit(current_user, "approve", "bill", bill.id, f"Bill {bill.bill_number} approved")
        flash(request, f"Bill {bill.bill_number} approved.", "success")
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        flash(request, str(exc), "warning")
    return RedirectResponse(url=str(request.url_for("ledger.pending_approvals")), status_code=303)


@router.post("/bills/{id}/reject", name="bills.reject_bill")
async def reject_bill(id: int, request: Request, current_user=Depends(require_permission("ledger.approve"))):
    service = BillingService()
    try:
        service.reject_bill(id)
        record_audit(current_user, "reject", "bill", id, f"Bill #{id} rejected; linked records released.")
        flash(request, "Pending bill rejected and its records released.", "success")
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        flash(request, str(exc), "warning")
    return RedirectResponse(url=str(request.url_for("ledger.pending_approvals")), status_code=303)


@router.post("/bills/{id}/settle", name="bills.settle_bill")
async def settle_bill(id: int, request: Request, current_user=Depends(require_permission("ledger.edit"))):
    form_data = await request.form()
    amount = form_data.get("amount")
    payment_method = form_data.get("payment_method")
    reference = form_data.get("reference")
    service = BillingService()
    try:
        bill = service.settle_bill(id, amount, payment_method=payment_method, reference=reference)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        flash(request, str(exc), "warning")
        return RedirectResponse(url=str(request.url_for("bills.view_bill", id=id)), status_code=303)

    record_audit(current_user, "settle", "bill", bill.id, f"Bill {bill.bill_number} settled Rs. {float(amount or 0):,.2f}")
    flash(request, f"Recorded settlement for bill {bill.bill_number}.", "success")
    return RedirectResponse(url=str(request.url_for("bills.view_bill", id=id)), status_code=303)
