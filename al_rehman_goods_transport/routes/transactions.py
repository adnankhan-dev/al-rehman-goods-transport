from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..core.auth import require_any_permission, require_permission
from ..core.flash import flash
from ..core.templating import render_template
from ..forms import TransactionForm
from ..models import Contractor, FinancialEntity, PetrolPump, Plant, Vehicle, VehicleOwner
from ..services import NotFoundError, TransactionInput, TransactionService, ValidationError
from ..services.audit import record_audit


router = APIRouter()


def _populate_transaction_choices(form: TransactionForm):
    form.vehicle_id.choices = [(0, "Select Vehicle")] + [(vehicle.id, f"{vehicle.vehicle_number} - {vehicle.owner_display_name}") for vehicle in Vehicle.query.order_by(Vehicle.vehicle_number.asc()).all()]
    form.vehicle_owner_id.choices = [(0, "Select Vehicle Owner")] + [(owner.id, owner.name) for owner in VehicleOwner.query.order_by(VehicleOwner.name.asc()).all()]
    form.contractor_id.choices = [(0, "Select Contractor")] + [(contractor.id, contractor.name) for contractor in Contractor.query.order_by(Contractor.name.asc()).all()]
    form.plant_id.choices = [(0, "Select Plant")] + [(plant.id, plant.name) for plant in Plant.query.order_by(Plant.name.asc()).all()]
    form.petrol_pump_id.choices = [(0, "Select Petrol Pump")] + [(pump.id, pump.name) for pump in PetrolPump.query.order_by(PetrolPump.name.asc()).all()]
    form.financial_entity_id.choices = [(0, "Select Financial Entity")] + [
        (entity.id, f"{entity.name} ({entity.kind_label})") for entity in FinancialEntity.query.order_by(FinancialEntity.name.asc()).all()
    ]


_ENTITY_ID_FIELD = {
    "contractor": "contractor_id",
    "vehicle_owner": "vehicle_owner_id",
    "plant": "plant_id",
    "petrol_pump": "petrol_pump_id",
    "financial_entity": "financial_entity_id",
}


def _transaction_input_from_form(form):
    direction = form.type.data  # 'payment' | 'receipt' | 'other_expense' | 'initial_balance'
    entity_type = None
    entity_id = None
    tx_type = direction
    vehicle_id = form.vehicle_id.data if form.vehicle_id.data not in (None, 0) else None

    if direction in ("payment", "receipt"):
        entity_type = form.entity_type.data or None
        field_name = _ENTITY_ID_FIELD.get(entity_type)
        if field_name:
            raw = getattr(form, field_name).data
            entity_id = raw if raw not in (None, 0) else None
        # A vehicle only ever narrows a vehicle-owner payment to one of that
        # owner's vehicles; it is never an account on its own.
        if entity_type != "vehicle_owner":
            vehicle_id = None
        if entity_type == "vehicle_owner" and vehicle_id:
            tx_type = f"vehicle_{direction}"
        else:
            # Internal type, e.g. contractor + receipt -> contractor_receipt.
            tx_type = f"{entity_type}_{direction}" if entity_type else direction
    else:
        vehicle_id = None

    return TransactionInput(
        type=tx_type,
        amount=form.amount.data,
        date=form.date.data,
        description=form.description.data,
        payment_method=form.payment_method.data,
        reference=form.reference.data,
        entity_type=entity_type,
        entity_id=entity_id,
        vehicle_id=vehicle_id,
        vehicle_owner_id=form.vehicle_owner_id.data if form.vehicle_owner_id.data != 0 else None,
        contractor_id=form.contractor_id.data if form.contractor_id.data != 0 else None,
        plant_id=form.plant_id.data if form.plant_id.data != 0 else None,
        petrol_pump_id=form.petrol_pump_id.data if form.petrol_pump_id.data != 0 else None,
        financial_entity_id=form.financial_entity_id.data if form.financial_entity_id.data != 0 else None,
    )


@router.get("/transactions", name="transactions.transactions")
async def transactions(request: Request, _current_user=Depends(require_permission("ledger.view"))):
    return RedirectResponse(url=str(request.url_for("ledger.index")), status_code=303)


@router.api_route("/transactions/create", methods=["GET", "POST"], name="transactions.create_transaction")
async def create_transaction(request: Request, current_user=Depends(require_permission("ledger.create"))):
    if request.method == "GET":
        return RedirectResponse(url=f"{request.url_for('ledger.create')}?mode=transaction", status_code=303)

    form = TransactionForm(await request.form() if request.method == "POST" else None)
    _populate_transaction_choices(form)

    if request.method == "POST" and form.validate():
        try:
            transaction = TransactionService().create_transaction(_transaction_input_from_form(form), approval_status="pending")
            record_audit(current_user, "create", "transaction", transaction.id, f"{transaction.type} Rs. {transaction.amount:,.2f} — {transaction.entity_name}")
            flash(request, "Transaction submitted for approval. It will post to the ledger once approved.", "success")
            return RedirectResponse(url=str(request.url_for("transactions.transactions")), status_code=303)
        except ValidationError as exc:
            flash(request, str(exc), "warning")

    return render_template(request, "transactions/create.html", form=form)


@router.get("/ledger/pending", name="ledger.pending_approvals")
async def ledger_pending(request: Request, current_user=Depends(require_any_permission("ledger.view", "ledger.approve"))):
    service = TransactionService()
    from ..services import BillingService

    pending_transactions = service.list_pending()
    pending_bills = BillingService().list_pending_bills()
    return render_template(
        request,
        "ledger/pending_approvals.html",
        pending_transactions=pending_transactions,
        pending_bills=pending_bills,
        pending_count=len(pending_transactions) + len(pending_bills),
        can_approve=bool(getattr(current_user, "can", lambda _c: False)("ledger.approve")),
    )


@router.post("/transactions/approve", name="transactions.approve_transactions")
async def approve_transactions(request: Request, current_user=Depends(require_permission("ledger.approve"))):
    service = TransactionService()
    form_data = await request.form()

    def _int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    ids = [i for i in (_int(v) for v in (form_data.get("transaction_ids") or "").split(",")) if i]
    if not ids:
        flash(request, "No transactions were selected for approval.", "warning")
        return RedirectResponse(url=str(request.url_for("ledger.pending_approvals")), status_code=303)
    approved = errors = 0
    for tx_id in ids:
        try:
            service.approve_transaction(tx_id, approver_id=getattr(current_user, "id", None))
            record_audit(current_user, "approve", "transaction", tx_id, f"Transaction #{tx_id} approved")
            approved += 1
        except Exception:
            errors += 1
    if approved:
        flash(request, f"{approved} transaction(s) approved and posted.", "success")
    if errors:
        flash(request, f"{errors} transaction(s) could not be approved.", "warning")
    return RedirectResponse(url=str(request.url_for("ledger.pending_approvals")), status_code=303)


@router.post("/transactions/{id}/reject", name="transactions.reject_transaction")
async def reject_transaction(id: int, request: Request, current_user=Depends(require_permission("ledger.approve"))):
    service = TransactionService()
    try:
        service.reject_transaction(id)
        record_audit(current_user, "reject", "transaction", id, f"Transaction #{id} rejected")
        flash(request, "Pending transaction rejected and removed.", "success")
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        flash(request, str(exc), "warning")
    return RedirectResponse(url=str(request.url_for("ledger.pending_approvals")), status_code=303)


@router.get("/transactions/{id}", name="transactions.view_transaction")
async def view_transaction(id: int, request: Request, _current_user=Depends(require_permission("ledger.view"))):
    try:
        transaction = TransactionService().get_transaction(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return render_template(request, "transactions/view.html", transaction=transaction)


@router.api_route("/transactions/{id}/edit", methods=["GET", "POST"], name="transactions.edit_transaction")
async def edit_transaction(id: int, request: Request, current_user=Depends(require_permission("ledger.edit"))):
    service = TransactionService()
    try:
        transaction = service.get_transaction(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    form = TransactionForm(await request.form() if request.method == "POST" else None, obj=transaction)
    if request.method == "GET":
        # Reverse-map the stored type (e.g. contractor_receipt) into the generic
        # direction + entity-type fields the form now uses.
        stored = transaction.type or ""
        if stored.endswith("_payment"):
            form.type.data = "payment"
            form.entity_type.data = stored[: -len("_payment")]
        elif stored.endswith("_receipt"):
            form.type.data = "receipt"
            form.entity_type.data = stored[: -len("_receipt")]
        else:
            form.type.data = stored
        form.vehicle_id.data = transaction.vehicle_id or 0
        form.vehicle_owner_id.data = transaction.vehicle_owner_id or 0
        form.contractor_id.data = transaction.contractor_id or 0
        form.plant_id.data = transaction.plant_id or 0
        form.petrol_pump_id.data = transaction.petrol_pump_id or 0
        form.financial_entity_id.data = transaction.financial_entity_id or 0
    _populate_transaction_choices(form)

    if request.method == "POST" and form.validate():
        try:
            service.update_transaction(id, _transaction_input_from_form(form))
            record_audit(current_user, "update", "transaction", id, f"Transaction #{id} updated")
            flash(request, "Transaction updated successfully!", "success")
            return RedirectResponse(url=str(request.url_for("transactions.view_transaction", id=id)), status_code=303)
        except ValidationError as exc:
            flash(request, str(exc), "warning")

    return render_template(request, "transactions/edit.html", form=form, transaction=transaction)


@router.post("/transactions/{id}/delete", name="transactions.delete_transaction")
async def delete_transaction(id: int, request: Request, current_user=Depends(require_permission("ledger.delete"))):
    service = TransactionService()
    try:
        service.delete_transaction(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        flash(request, str(exc), "warning")
        return RedirectResponse(url=str(request.url_for("transactions.view_transaction", id=id)), status_code=303)

    record_audit(current_user, "delete", "transaction", id, f"Transaction #{id} deleted")
    flash(request, "Transaction deleted successfully!", "success")
    return RedirectResponse(url=str(request.url_for("transactions.transactions")), status_code=303)
