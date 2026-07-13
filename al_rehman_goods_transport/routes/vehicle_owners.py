from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..core.auth import require_permission
from ..core.flash import flash
from ..core.templating import render_template
from ..extensions import db
from ..forms import VehicleOwnerForm
from ..models import DieselEntry, Transaction, VehicleOwner
from ..services.billing import group_owner_activity_by_vehicle
from ..services.financials import entity_period_financials


router = APIRouter()


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _in_range(day, date_from, date_to):
    if day is None:
        return date_from is None and date_to is None
    day = day.date() if hasattr(day, "date") else day
    if date_from and day < date_from:
        return False
    if date_to and day > date_to:
        return False
    return True


def _owner_orders_and_summary(owner, date_from=None, date_to=None):
    related_orders = sorted(
        [
            order
            for vehicle in owner.vehicles
            for order in vehicle.orders
            if order.approval_status == "approved"
            and _in_range(order.completion_date or order.order_date, date_from, date_to)
        ],
        key=lambda order: (order.completion_date or order.order_date, order.id),
        reverse=True,
    )
    completed = [o for o in related_orders if (o.status or "").lower() == "completed"]

    gross = sum(o.total_vehicle_amount() for o in completed)
    order_diesel = sum(o.total_diesel_amount() for o in completed)
    advance = sum(o.total_advance_amount() for o in completed)
    net_payable = sum(o.remaining_vehicle_payment() for o in completed)

    # Standalone Fuel-Log diesel for this owner's vehicles (separate from the
    # order-attached diesel above) — also a deduction from what we owe the owner.
    vehicle_ids = [vehicle.id for vehicle in owner.vehicles]
    standalone_diesel_rows = []
    if vehicle_ids:
        diesel_query = (
            db.session.query(DieselEntry)
            .filter(DieselEntry.vehicle_id.in_(vehicle_ids), DieselEntry.approval_status == "approved")
            .order_by(DieselEntry.date.desc(), DieselEntry.id.desc())
        )
        if date_from:
            diesel_query = diesel_query.filter(DieselEntry.date >= date_from)
        if date_to:
            diesel_query = diesel_query.filter(DieselEntry.date <= date_to)
        standalone_diesel_rows = diesel_query.all()
    standalone_diesel = sum(float(entry.amount or 0) for entry in standalone_diesel_rows)

    # Payments already made to the owner (transaction mode).
    payments = sorted(
        [
            t for t in owner.transactions
            if t.type == "vehicle_owner_payment" and _in_range(t.date, date_from, date_to)
        ],
        key=lambda t: (t.date, t.id),
        reverse=True,
    )
    payments_total = sum(float(t.amount or 0) for t in payments)

    # Advances/payments to the owner's vehicles posted in the ledger
    # (Payment / Advance to Vehicle) — also money already paid toward what we owe.
    advances = []
    if vehicle_ids:
        advances = sorted(
            [
                t for t in db.session.query(Transaction)
                .filter(Transaction.type == "vehicle_payment", Transaction.vehicle_id.in_(vehicle_ids))
                .all()
                if _in_range(t.date, date_from, date_to)
            ],
            key=lambda t: (t.date, t.id),
            reverse=True,
        )
    advances_total = sum(float(t.amount or 0) for t in advances)

    net_after_diesel = net_payable - standalone_diesel
    outstanding = net_after_diesel - payments_total - advances_total

    summary = {
        "trips": len(completed),
        "total_delivered": sum((o.delivered_quantity or o.quantity or 0) for o in completed),
        "gross": gross,
        "diesel": order_diesel,
        "standalone_diesel": standalone_diesel,
        "advance": advance,
        "net_payable": net_payable,
        "net_after_diesel": net_after_diesel,
        "payments": payments_total,
        "advances": advances_total,
        "outstanding": outstanding,
    }
    # Statement/bill are presented grouped by vehicle, each with its own subtotals.
    vehicle_groups = group_owner_activity_by_vehicle(related_orders, standalone_diesel_rows, advances)
    # Account summary from the ledger: previous balance before the period,
    # receipts/payments inside it, and the resulting current balance.
    financial = entity_period_financials(
        "vehicle_owner",
        owner.id,
        start_date=date_from,
        end_date=date_to,
        period_activity_total=net_after_diesel,
    )
    return related_orders, summary, standalone_diesel_rows, payments, advances, vehicle_groups, financial


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
    date_from = _parse_date(request.query_params.get("date_from"))
    date_to = _parse_date(request.query_params.get("date_to"))
    related_orders, owner_summary, standalone_diesel_rows, payments, advances, vehicle_groups, financial = _owner_orders_and_summary(owner, date_from, date_to)
    related_transactions = sorted(owner.transactions, key=lambda transaction: (transaction.date, transaction.id), reverse=True)
    return render_template(
        request,
        "vehicle_owners/view.html",
        owner=owner,
        financial=financial,
        date_from=date_from,
        date_to=date_to,
        related_orders=related_orders,
        owner_summary=owner_summary,
        standalone_diesel_rows=standalone_diesel_rows,
        advances=advances,
        payments=payments,
        vehicle_groups=vehicle_groups,
        related_transactions=related_transactions,
    )


@router.get("/vehicle-owners/{id}/print", name="vehicle_owners.print_statement")
async def print_vehicle_owner(id: int, request: Request, _current_user=Depends(require_permission("vehicle_owners.view"))):
    owner = db.session.get(VehicleOwner, id)
    if owner is None:
        raise HTTPException(status_code=404, detail="Vehicle owner not found")
    date_from = _parse_date(request.query_params.get("date_from"))
    date_to = _parse_date(request.query_params.get("date_to"))
    related_orders, owner_summary, standalone_diesel_rows, payments, advances, vehicle_groups, financial = _owner_orders_and_summary(owner, date_from, date_to)
    return render_template(
        request,
        "vehicle_owners/print.html",
        show_nav=False,
        owner=owner,
        financial=financial,
        date_from=date_from,
        date_to=date_to,
        related_orders=related_orders,
        owner_summary=owner_summary,
        standalone_diesel_rows=standalone_diesel_rows,
        payments=payments,
        advances=advances,
        vehicle_groups=vehicle_groups,
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
