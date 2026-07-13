from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..core.auth import require_permission
from ..core.flash import flash
from ..core.templating import render_template
from ..extensions import db
from ..forms import VehicleForm
from ..models import Vehicle, VehicleOwner


router = APIRouter()


def _vehicle_owner_choices():
    return [(owner.id, owner.name) for owner in VehicleOwner.query.order_by(VehicleOwner.name.asc()).all()]


@router.get("/vehicles", name="vehicles.vehicles")
async def vehicles(request: Request, _current_user=Depends(require_permission("vehicles.view"))):
    all_vehicles = Vehicle.query.order_by(Vehicle.vehicle_number.asc()).all()
    return render_template(request, "vehicles/list.html", vehicles=all_vehicles)


@router.api_route("/vehicles/create", methods=["GET", "POST"], name="vehicles.create_vehicle")
async def create_vehicle(request: Request, _current_user=Depends(require_permission("vehicles.create"))):
    form = VehicleForm(await request.form() if request.method == "POST" else None)
    owner_choices = _vehicle_owner_choices()
    if not owner_choices:
        flash(request, "Create a vehicle owner before adding vehicles.", "warning")
        return RedirectResponse(url=str(request.url_for("vehicle_owners.create")), status_code=303)

    form.owner_id.choices = owner_choices
    if request.method == "POST" and form.validate():
        vehicle = Vehicle(
            vehicle_number=form.vehicle_number.data,
            owner_id=form.owner_id.data,
            vehicle_type=form.vehicle_type.data,
            capacity=form.capacity.data,
            insurance_details=form.insurance_details.data,
            fitness_certificate=form.fitness_certificate.data,
            balance=form.balance.data or 0.0,
        )
        vehicle.sync_owner_name()
        db.session.add(vehicle)
        # Owner balance mirrors the sum of vehicle balances, so an opening
        # vehicle balance must be reflected on the owner as well.
        if vehicle.balance and vehicle.owner_id:
            owner = db.session.get(VehicleOwner, vehicle.owner_id)
            if owner:
                owner.balance = (owner.balance or 0.0) + vehicle.balance
        db.session.commit()
        flash(request, "Vehicle created successfully!", "success")
        return RedirectResponse(url=str(request.url_for("vehicles.vehicles")), status_code=303)

    return render_template(request, "vehicles/create.html", form=form)


@router.get("/vehicles/{id}", name="vehicles.view_vehicle")
async def view_vehicle(id: int, request: Request, _current_user=Depends(require_permission("vehicles.view"))):
    vehicle = db.session.get(Vehicle, id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    related_orders = sorted(
        [order for order in vehicle.orders],
        key=lambda order: (order.completion_date or order.order_date, order.id),
        reverse=True,
    )
    completed_orders = [order for order in related_orders if order.status == "Completed"]
    total_owed = sum(order.remaining_vehicle_payment() for order in completed_orders)
    related_transactions = sorted(vehicle.transactions, key=lambda transaction: (transaction.date, transaction.id), reverse=True)
    return render_template(
        request,
        "vehicles/view.html",
        vehicle=vehicle,
        total_owed=total_owed,
        related_orders=related_orders,
        related_transactions=related_transactions,
    )


@router.api_route("/vehicles/{id}/edit", methods=["GET", "POST"], name="vehicles.edit_vehicle")
async def edit_vehicle(id: int, request: Request, _current_user=Depends(require_permission("vehicles.edit"))):
    vehicle = db.session.get(Vehicle, id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    form = VehicleForm(await request.form() if request.method == "POST" else None, obj=vehicle)
    # The running balance is maintained by orders/diesel/payments; editing
    # master data must never overwrite it. Adjustments go through the ledger.
    del form.balance
    owner_choices = _vehicle_owner_choices()
    if not owner_choices:
        flash(request, "Create a vehicle owner before editing vehicles.", "warning")
        return RedirectResponse(url=str(request.url_for("vehicle_owners.create")), status_code=303)

    form.owner_id.choices = owner_choices
    if request.method == "POST" and form.validate():
        vehicle.vehicle_number = form.vehicle_number.data
        vehicle.owner_id = form.owner_id.data
        vehicle.vehicle_type = form.vehicle_type.data
        vehicle.capacity = form.capacity.data
        vehicle.insurance_details = form.insurance_details.data
        vehicle.fitness_certificate = form.fitness_certificate.data
        vehicle.sync_owner_name()
        db.session.commit()
        flash(request, "Vehicle updated successfully!", "success")
        return RedirectResponse(url=str(request.url_for("vehicles.vehicles")), status_code=303)

    return render_template(request, "vehicles/edit.html", form=form, vehicle=vehicle)


@router.post("/vehicles/{id}/delete", name="vehicles.delete_vehicle")
async def delete_vehicle(id: int, request: Request, _current_user=Depends(require_permission("vehicles.delete"))):
    vehicle = db.session.get(Vehicle, id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    if vehicle.orders or vehicle.transactions or vehicle.diesel_entries_log.first() is not None:
        flash(request, "This vehicle has orders, diesel entries, or transactions and cannot be deleted.", "danger")
        return RedirectResponse(url=str(request.url_for("vehicles.view_vehicle", id=id)), status_code=303)

    db.session.delete(vehicle)
    db.session.commit()
    flash(request, "Vehicle deleted successfully!", "success")
    return RedirectResponse(url=str(request.url_for("vehicles.vehicles")), status_code=303)
