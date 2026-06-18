from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..core.auth import require_permission
from ..core.flash import flash
from ..core.templating import render_template
from ..extensions import db
from ..forms import VehicleOwnerForm
from ..models import VehicleOwner


router = APIRouter()


def _owner_orders_and_summary(owner):
    related_orders = sorted(
        [order for vehicle in owner.vehicles for order in vehicle.orders],
        key=lambda order: (order.completion_date or order.order_date, order.id),
        reverse=True,
    )
    completed = [o for o in related_orders if (o.status or "").lower() == "completed"]
    summary = {
        "trips": len(completed),
        "total_delivered": sum((o.delivered_quantity or o.quantity or 0) for o in completed),
        "gross": sum(o.total_vehicle_amount() for o in completed),
        "diesel": sum(o.total_diesel_amount() for o in completed),
        "advance": sum(o.total_advance_amount() for o in completed),
        "net_payable": sum(o.remaining_vehicle_payment() for o in completed),
    }
    return related_orders, summary


@router.get("/vehicle-owners", name="vehicle_owners.index")
async def vehicle_owners(request: Request, _current_user=Depends(require_permission("vehicle_owners.view"))):
    owners = VehicleOwner.query.order_by(VehicleOwner.name.asc()).all()
    return render_template(request, "vehicle_owners/list.html", owners=owners)


@router.api_route("/vehicle-owners/create", methods=["GET", "POST"], name="vehicle_owners.create")
async def create_vehicle_owner(request: Request, _current_user=Depends(require_permission("vehicle_owners.create"))):
    form = VehicleOwnerForm(await request.form() if request.method == "POST" else None)
    if request.method == "POST" and form.validate():
        owner = VehicleOwner(
            name=form.name.data,
            phone=form.phone.data,
            address=form.address.data,
        )
        db.session.add(owner)
        db.session.commit()
        flash(request, "Vehicle owner created successfully!", "success")
        return RedirectResponse(url=str(request.url_for("vehicle_owners.index")), status_code=303)

    return render_template(request, "vehicle_owners/create.html", form=form)


@router.get("/vehicle-owners/{id}", name="vehicle_owners.view")
async def view_vehicle_owner(id: int, request: Request, _current_user=Depends(require_permission("vehicle_owners.view"))):
    owner = db.session.get(VehicleOwner, id)
    if owner is None:
        raise HTTPException(status_code=404, detail="Vehicle owner not found")
    related_orders, owner_summary = _owner_orders_and_summary(owner)
    related_transactions = sorted(owner.transactions, key=lambda transaction: (transaction.date, transaction.id), reverse=True)
    return render_template(
        request,
        "vehicle_owners/view.html",
        owner=owner,
        related_orders=related_orders,
        owner_summary=owner_summary,
        related_transactions=related_transactions,
    )


@router.get("/vehicle-owners/{id}/print", name="vehicle_owners.print_statement")
async def print_vehicle_owner(id: int, request: Request, _current_user=Depends(require_permission("vehicle_owners.view"))):
    owner = db.session.get(VehicleOwner, id)
    if owner is None:
        raise HTTPException(status_code=404, detail="Vehicle owner not found")
    related_orders, owner_summary = _owner_orders_and_summary(owner)
    return render_template(
        request,
        "vehicle_owners/print.html",
        show_nav=False,
        owner=owner,
        related_orders=related_orders,
        owner_summary=owner_summary,
        now=datetime.now(),
    )


@router.api_route("/vehicle-owners/{id}/edit", methods=["GET", "POST"], name="vehicle_owners.edit")
async def edit_vehicle_owner(id: int, request: Request, _current_user=Depends(require_permission("vehicle_owners.edit"))):
    owner = db.session.get(VehicleOwner, id)
    if owner is None:
        raise HTTPException(status_code=404, detail="Vehicle owner not found")

    form = VehicleOwnerForm(await request.form() if request.method == "POST" else None, obj=owner)
    if request.method == "POST" and form.validate():
        owner.name = form.name.data
        owner.phone = form.phone.data
        owner.address = form.address.data

        for vehicle in owner.vehicles:
            vehicle.sync_owner_name()

        db.session.commit()
        flash(request, "Vehicle owner updated successfully!", "success")
        return RedirectResponse(url=str(request.url_for("vehicle_owners.view", id=id)), status_code=303)

    return render_template(request, "vehicle_owners/edit.html", form=form, owner=owner)


@router.post("/vehicle-owners/{id}/delete", name="vehicle_owners.delete")
async def delete_vehicle_owner(id: int, request: Request, _current_user=Depends(require_permission("vehicle_owners.delete"))):
    owner = db.session.get(VehicleOwner, id)
    if owner is None:
        raise HTTPException(status_code=404, detail="Vehicle owner not found")

    if owner.vehicles:
        flash(request, "Reassign or remove the owner's vehicles before deleting this owner.", "danger")
        return RedirectResponse(url=str(request.url_for("vehicle_owners.view", id=id)), status_code=303)

    db.session.delete(owner)
    db.session.commit()
    flash(request, "Vehicle owner deleted successfully!", "success")
    return RedirectResponse(url=str(request.url_for("vehicle_owners.index")), status_code=303)
