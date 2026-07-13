from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..core.auth import require_permission
from ..core.flash import flash
from ..core.templating import render_template
from ..forms import DieselEntryForm
from ..models import PetrolPump, Vehicle, VehicleOwner
from ..services import DieselService, NotFoundError
from ..services.audit import record_audit
from ..services.petrol_pumps import PetrolPumpService
from ..services.settings import SettingsService
from ..utils.pagination import paginate_list, parse_page

router = APIRouter()


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _populate_choices(form, service: DieselService, vehicle_id=None):
    choices = service.build_form_choices(vehicle_id=vehicle_id)
    form.vehicle_id.choices = choices["vehicle_choices"]
    form.petrol_pump_id.choices = choices["pump_choices"]
    form.order_id.choices = choices["order_choices"]


def _entry_data_from_form(form):
    return {
        "vehicle_id": form.vehicle_id.data,
        "petrol_pump_id": form.petrol_pump_id.data if form.petrol_pump_id.data not in (None, 0) else None,
        "order_id": form.order_id.data if form.order_id.data not in (None, 0) else None,
        "date": form.date.data,
        "litres": form.litres.data or None,
        "amount": form.amount.data,
        "receipt_number": (form.receipt_number.data or "").strip() or None,
        "notes": (form.notes.data or "").strip() or None,
    }


@router.get("/diesel", name="diesel.index")
async def diesel_index(request: Request, _=Depends(require_permission("diesel.view"))):
    service = DieselService()
    vehicle_id = _parse_int(request.query_params.get("vehicle_id"))
    pump_id = _parse_int(request.query_params.get("pump_id"))
    owner_id = _parse_int(request.query_params.get("owner_id"))
    date_from = _parse_date(request.query_params.get("date_from"))
    date_to = _parse_date(request.query_params.get("date_to"))
    search = (request.query_params.get("search") or "").strip()
    entries = service.list_entries(vehicle_id=vehicle_id, pump_id=pump_id, owner_id=owner_id, date_from=date_from, date_to=date_to, search=search or None)
    stats = service.summary_stats(entries)
    pagination = paginate_list(entries, parse_page(request.query_params.get("page")))
    vehicles = Vehicle.query.order_by(Vehicle.vehicle_number).all()
    pumps = PetrolPump.query.order_by(PetrolPump.name).all()
    vehicle_owners = VehicleOwner.query.order_by(VehicleOwner.name).all()

    # Previous balance, payments, and net for the pump(s) in scope — computed
    # from the ledger so the fuel log always matches the pump statement.
    from ..services.financials import entity_period_financials

    selected_pump = next((p for p in pumps if p.id == pump_id), None) if pump_id else None
    scope_pumps = [selected_pump] if selected_pump else pumps
    pump_financials = [
        entity_period_financials("petrol_pump", p.id, start_date=date_from, end_date=date_to)
        for p in scope_pumps
    ]
    opening_balance = sum(f["previous_balance"] for f in pump_financials)
    amount_paid = sum(f["payments_total"] for f in pump_financials)
    net_payable = sum(f["current_total"] for f in pump_financials)
    return render_template(
        request,
        "diesel/list.html",
        entries=pagination["items"],
        pagination=pagination,
        vehicles=vehicles,
        pumps=pumps,
        vehicle_owners=vehicle_owners,
        amount_paid=amount_paid,
        opening_balance=opening_balance,
        net_payable=net_payable,
        selected_pump=selected_pump,
        filter_state={
            "vehicle_id": vehicle_id,
            "pump_id": pump_id,
            "owner_id": owner_id,
            "date_from": date_from,
            "date_to": date_to,
            "search": search,
        },
        **stats,
    )


@router.get("/diesel/print", name="diesel.print_statement")
async def diesel_print(request: Request, _=Depends(require_permission("diesel.view"))):
    from datetime import datetime as _dt

    service = DieselService()
    vehicle_id = _parse_int(request.query_params.get("vehicle_id"))
    pump_id = _parse_int(request.query_params.get("pump_id"))
    owner_id = _parse_int(request.query_params.get("owner_id"))
    date_from = _parse_date(request.query_params.get("date_from"))
    date_to = _parse_date(request.query_params.get("date_to"))
    search = (request.query_params.get("search") or "").strip()
    entries = service.list_entries(vehicle_id=vehicle_id, pump_id=pump_id, owner_id=owner_id, date_from=date_from, date_to=date_to, search=search or None)
    stats = service.summary_stats(entries)

    def _name(model, _id):
        obj = model.query.get(_id) if _id else None
        return obj.name if obj else None

    filters = {
        "owner": _name(VehicleOwner, owner_id),
        "vehicle": (Vehicle.query.get(vehicle_id).vehicle_number if vehicle_id else None),
        "pump": _name(PetrolPump, pump_id),
        "date_from": date_from.strftime("%Y-%m-%d") if date_from else None,
        "date_to": date_to.strftime("%Y-%m-%d") if date_to else None,
        "search": search or None,
    }
    from ..services.financials import entity_period_financials

    scope_pumps = [PetrolPump.query.get(pump_id)] if pump_id else PetrolPump.query.all()
    pump_financials = [
        entity_period_financials("petrol_pump", p.id, start_date=date_from, end_date=date_to)
        for p in scope_pumps if p is not None
    ]
    opening_balance = sum(f["previous_balance"] for f in pump_financials)
    amount_paid = sum(f["payments_total"] for f in pump_financials)
    net_payable = sum(f["current_total"] for f in pump_financials)
    return render_template(request, "diesel/print.html", show_nav=False, entries=entries, filters=filters,
                           amount_paid=amount_paid, opening_balance=opening_balance, net_payable=net_payable,
                           now=_dt.now(), **stats)


@router.api_route("/diesel/create", methods=["GET", "POST"], name="diesel.create")
async def diesel_create(request: Request, current_user=Depends(require_permission("diesel.create"))):
    service = DieselService()
    diesel_rate = SettingsService().get_diesel_rate()
    get_vehicle_id = _parse_int(request.query_params.get("vehicle_id"))
    form = DieselEntryForm()
    _populate_choices(form, service, vehicle_id=get_vehicle_id)
    if get_vehicle_id:
        form.vehicle_id.data = get_vehicle_id
    if request.method == "GET":
        # Default to the last entered date — many entries share the same date.
        form.date.data = service.last_entry_date()

    if request.method == "POST":
        form_data = await request.form()
        form = DieselEntryForm(form_data)
        vehicle_id = _parse_int(form_data.get("vehicle_id"))
        _populate_choices(form, service, vehicle_id=vehicle_id)
        if form.validate():
            try:
                entry = service.create_entry(_entry_data_from_form(form))
                record_audit(current_user, "create", "diesel_entry", entry.id, f"Diesel Rs. {entry.amount:,.2f} — {entry.vehicle.vehicle_number}")
                flash(request, f"Diesel entry #{entry.id} saved for vehicle {entry.vehicle.vehicle_number}.", "success")
                return RedirectResponse(url=str(request.url_for("diesel.index")), status_code=303)
            except Exception as exc:
                flash(request, str(exc), "danger")

    return render_template(request, "diesel/create.html", form=form, diesel_rate=diesel_rate, pump_prices=PetrolPumpService().prices_map())


@router.get("/diesel/pending", name="diesel.pending_approvals")
async def diesel_pending(request: Request, _=Depends(require_permission("diesel.approve"))):
    service = DieselService()
    entries = service.list_pending()
    return render_template(request, "diesel/pending_approvals.html", entries=entries, pending_count=len(entries))


@router.post("/diesel/approve", name="diesel.approve_entries")
async def diesel_approve(request: Request, current_user=Depends(require_permission("diesel.approve"))):
    service = DieselService()
    form_data = await request.form()
    entry_ids = [i for i in (_parse_int(v) for v in (form_data.get("entry_ids") or "").split(",")) if i]
    if not entry_ids:
        flash(request, "No diesel entries were selected for approval.", "warning")
        return RedirectResponse(url=str(request.url_for("diesel.pending_approvals")), status_code=303)
    approved = errors = 0
    for entry_id in entry_ids:
        try:
            service.approve_entry(entry_id, approver_id=getattr(current_user, "id", None))
            record_audit(current_user, "approve", "diesel_entry", entry_id, f"Diesel entry #{entry_id} approved")
            approved += 1
        except Exception:
            errors += 1
    if approved:
        flash(request, f"{approved} diesel entry(ies) approved and posted.", "success")
    if errors:
        flash(request, f"{errors} entry(ies) could not be approved.", "warning")
    return RedirectResponse(url=str(request.url_for("diesel.pending_approvals")), status_code=303)


@router.post("/diesel/{id}/reject", name="diesel.reject_entry")
async def diesel_reject(id: int, request: Request, current_user=Depends(require_permission("diesel.approve"))):
    service = DieselService()
    try:
        service.reject_entry(id)
        record_audit(current_user, "reject", "diesel_entry", id, f"Diesel entry #{id} rejected")
        flash(request, "Pending diesel entry rejected and removed.", "success")
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        flash(request, str(exc), "warning")
    return RedirectResponse(url=str(request.url_for("diesel.pending_approvals")), status_code=303)


@router.get("/diesel/{id}", name="diesel.view")
async def diesel_view(id: int, request: Request, _=Depends(require_permission("diesel.view"))):
    service = DieselService()
    try:
        entry = service.get_entry(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return render_template(request, "diesel/view.html", entry=entry)


@router.api_route("/diesel/{id}/edit", methods=["GET", "POST"], name="diesel.edit")
async def diesel_edit(id: int, request: Request, current_user=Depends(require_permission("diesel.edit"))):
    service = DieselService()
    try:
        entry = service.get_entry(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    diesel_rate = SettingsService().get_diesel_rate()
    form = DieselEntryForm(obj=entry)
    _populate_choices(form, service, vehicle_id=entry.vehicle_id)

    if request.method == "POST":
        form_data = await request.form()
        form = DieselEntryForm(form_data)
        vehicle_id = _parse_int(form_data.get("vehicle_id"))
        _populate_choices(form, service, vehicle_id=vehicle_id)
        if form.validate():
            try:
                service.update_entry(id, _entry_data_from_form(form))
                record_audit(current_user, "update", "diesel_entry", id, f"Diesel entry #{id} updated")
                flash(request, f"Diesel entry #{id} updated.", "success")
                return RedirectResponse(url=str(request.url_for("diesel.view", id=id)), status_code=303)
            except Exception as exc:
                flash(request, str(exc), "danger")

    return render_template(request, "diesel/edit.html", form=form, entry=entry, diesel_rate=diesel_rate, pump_prices=PetrolPumpService().prices_map())


@router.post("/diesel/{id}/delete", name="diesel.delete")
async def diesel_delete(id: int, request: Request, current_user=Depends(require_permission("diesel.delete"))):
    service = DieselService()
    try:
        service.delete_entry(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    record_audit(current_user, "delete", "diesel_entry", id, f"Diesel entry #{id} deleted")
    flash(request, f"Diesel entry #{id} deleted.", "success")
    return RedirectResponse(url=str(request.url_for("diesel.index")), status_code=303)
