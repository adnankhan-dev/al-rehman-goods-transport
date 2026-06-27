from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..core.auth import require_permission
from ..core.flash import flash
from ..core.templating import render_template
from ..forms import TransactionForm
from ..models import Contractor, PetrolPump, Plant, Vehicle, VehicleOwner
from ..services import NotFoundError, TransactionInput, TransactionService, ValidationError
from ..services.audit import record_audit


router = APIRouter()


def _populate_transaction_choices(form: TransactionForm):
    form.vehicle_id.choices = [(0, "Select Vehicle")] + [(vehicle.id, f"{vehicle.vehicle_number} - {vehicle.owner_display_name}") for vehicle in Vehicle.query.order_by(Vehicle.vehicle_number.asc()).all()]
    form.vehicle_owner_id.choices = [(0, "Select Vehicle Owner")] + [(owner.id, owner.name) for owner in VehicleOwner.query.order_by(VehicleOwner.name.asc()).all()]
    form.contractor_id.choices = [(0, "Select Contractor")] + [(contractor.id, contractor.name) for contractor in Contractor.query.order_by(Contractor.name.asc()).all()]
    form.plant_id.choices = [(0, "Select Plant")] + [(plant.id, plant.name) for plant in Plant.query.order_by(Plant.name.asc()).all()]
    form.petrol_pump_id.choices = [(0, "Select Petrol Pump")] + [(pump.id, pump.name) for pump in PetrolPump.query.order_by(PetrolPump.name.asc()).all()]


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
            transaction = TransactionService().create_transaction(_transaction_input_from_form(form))
            record_audit(current_user, "create", "transaction", transaction.id, f"{transaction.type} Rs. {transaction.amount:,.2f} — {transaction.entity_name}")
            flash(request, "Transaction added successfully!", "success")
            return RedirectResponse(url=str(request.url_for("transactions.transactions")), status_code=303)
        except ValidationError as exc:
            flash(request, str(exc), "warning")

    return render_template(request, "transactions/create.html", form=form)


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
        form.vehicle_owner_id.data = transaction.vehicle_owner_id or 0
        form.petrol_pump_id.data = transaction.petrol_pump_id or 0
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
