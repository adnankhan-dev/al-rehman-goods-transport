from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..core.auth import require_permission
from ..core.flash import flash
from ..core.templating import render_template
from ..forms import TransactionForm
from ..services import LedgerService, NotFoundError, TransactionInput, TransactionService, ValidationError
from ..services.audit import record_audit
from ..utils.pagination import parse_page


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


def _populate_transaction_choices(form, context):
    form.vehicle_id.choices = [(0, "Select Vehicle")] + [(vehicle.id, f"{vehicle.vehicle_number} - {vehicle.owner_display_name}") for vehicle in context["vehicles"]]
    form.vehicle_owner_id.choices = [(0, "Select Vehicle Owner")] + [(owner.id, owner.name) for owner in context["vehicle_owners"]]
    form.contractor_id.choices = [(0, "Select Contractor")] + [(contractor.id, contractor.name) for contractor in context["contractors"]]
    form.plant_id.choices = [(0, "Select Plant")] + [(plant.id, plant.name) for plant in context["plants"]]
    form.petrol_pump_id.choices = [(0, "Select Petrol Pump")] + [(pump.id, pump.name) for pump in context["petrol_pumps"]]


def _transaction_input_from_form(form):
    entity_type = None
    entity_id = None
    if form.type.data == "vehicle_payment":
        entity_type = "vehicle"
        entity_id = form.vehicle_id.data if form.vehicle_id.data != 0 else None
    elif form.type.data in ("vehicle_owner_payment", "vehicle_owner_receipt"):
        entity_type = "vehicle_owner"
        entity_id = form.vehicle_owner_id.data if form.vehicle_owner_id.data != 0 else None
    elif form.type.data == "contractor_receipt":
        entity_type = "contractor"
        entity_id = form.contractor_id.data if form.contractor_id.data != 0 else None
    elif form.type.data == "plant_payment":
        entity_type = "plant"
        entity_id = form.plant_id.data if form.plant_id.data != 0 else None
    elif form.type.data == "petrol_pump_payment":
        entity_type = "petrol_pump"
        entity_id = form.petrol_pump_id.data if form.petrol_pump_id.data != 0 else None

    return TransactionInput(
        type=form.type.data,
        amount=form.amount.data,
        description=form.description.data,
        payment_method=form.payment_method.data,
        reference=form.reference.data,
        entity_type=entity_type,
        entity_id=entity_id,
        vehicle_id=form.vehicle_id.data if form.vehicle_id.data != 0 else None,
        vehicle_owner_id=form.vehicle_owner_id.data if form.vehicle_owner_id.data != 0 else None,
        contractor_id=form.contractor_id.data if form.contractor_id.data != 0 else None,
        plant_id=form.plant_id.data if form.plant_id.data != 0 else None,
        petrol_pump_id=form.petrol_pump_id.data if form.petrol_pump_id.data != 0 else None,
    )


@router.get("/ledger", name="ledger.index")
async def index(request: Request, _current_user=Depends(require_permission("ledger.view"))):
    filters = {
        "date_from": request.query_params.get("date_from", ""),
        "date_to": request.query_params.get("date_to", ""),
        "kind": request.query_params.get("kind", ""),
        "entity_type": request.query_params.get("entity_type", ""),
    }
    page = parse_page(request.query_params.get("page"))
    return render_template(request, "ledger/index.html", filters=filters, **LedgerService().index_context(filters=filters, page=page))


@router.get("/ledger/print", name="ledger.print")
async def print_ledger(request: Request, _current_user=Depends(require_permission("ledger.view"))):
    filters = {
        "date_from": request.query_params.get("date_from", ""),
        "date_to": request.query_params.get("date_to", ""),
        "kind": request.query_params.get("kind", ""),
        "entity_type": request.query_params.get("entity_type", ""),
    }
    return render_template(
        request,
        "ledger/print.html",
        show_nav=False,
        now=datetime.now(),
        filters=filters,
        **LedgerService().index_context(filters=filters, per_page=100000),
    )


@router.api_route("/ledger/create", methods=["GET", "POST"], name="ledger.create")
async def create_entry(request: Request, current_user=Depends(require_permission("ledger.create"))):
    form_data = await request.form() if request.method == "POST" else None
    mode = (form_data.get("mode") if form_data else request.query_params.get("mode", "bill")) or "bill"
    filter_state = _build_filter_state(form_data if request.method == "POST" else request.query_params)
    selected_order_ids = _parse_int_list(form_data.getlist("order_ids")) if form_data and hasattr(form_data, "getlist") else []
    selected_entry_ids = _parse_int_list(form_data.getlist("entry_ids")) if form_data and hasattr(form_data, "getlist") else []
    selected_loading_ids = _parse_int_list(form_data.getlist("loading_ids")) if form_data and hasattr(form_data, "getlist") else []
    service = LedgerService()
    context = service.create_context(mode, filter_state, selected_order_ids, selected_entry_ids, selected_loading_ids)

    transaction_form = TransactionForm(form_data if mode == "transaction" else None)
    _populate_transaction_choices(transaction_form, context)

    if request.method == "POST":
        if mode == "transaction" and transaction_form.validate():
            try:
                transaction = TransactionService().create_transaction(_transaction_input_from_form(transaction_form))
                record_audit(current_user, "create", "transaction", transaction.id, f"{transaction.type} Rs. {transaction.amount:,.2f} — {transaction.entity_name}")
                flash(request, "Ledger transaction posted successfully.", "success")
                return RedirectResponse(url=str(request.url_for("transactions.view_transaction", id=transaction.id)), status_code=303)
            except ValidationError as exc:
                flash(request, str(exc), "warning")
        elif mode == "bill":
            try:
                bill, _selected_orders = service.billing.create_bill(
                    entity_type=filter_state["entity_type"],
                    entity_id=filter_state["entity_id"],
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
                flash(request, f"Bill {bill.bill_number} created successfully.", "success")
                return RedirectResponse(url=str(request.url_for("bills.view_bill", id=bill.id)), status_code=303)
            except (NotFoundError, ValidationError) as exc:
                flash(request, str(exc), "warning")

    return render_template(request, "ledger/create.html", transaction_form=transaction_form, **context)
