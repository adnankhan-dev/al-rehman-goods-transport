"""Consolidated Period Report — the printable "file pack" for hard records.

One document for a chosen period, page-broken per section: financial position
of every account (previous -> current balance), a profit & loss summary, one
Account Summary statement per contractor / vehicle owner / petrol pump / plant
with the period activity behind it, then the trips register, the fuel log, and
the ledger book. Everything is computed live from the ledger through
``entity_period_financials`` so the pack always reconciles with the individual
bills and statements.
"""

from datetime import datetime

from sqlalchemy import or_

from ..extensions import db
from ..models import (
    Contractor,
    DieselEntry,
    Order,
    OrderLoading,
    PetrolPump,
    Plant,
    Transaction,
    Vehicle,
    VehicleOwner,
)
from .financials import entity_period_financials


def _safe(value):
    return float(value or 0.0)


def _as_date(value):
    return value.date() if hasattr(value, "date") else value


def _in_range(day, date_from, date_to):
    if day is None:
        return date_from is None and date_to is None
    day = _as_date(day)
    if date_from and day < date_from:
        return False
    if date_to and day > date_to:
        return False
    return True


def _significant(financial, rows):
    """Include an account in the pack when it has period activity or a balance."""
    return bool(rows) or abs(financial["previous_balance"]) > 0.005 or abs(financial["current_total"]) > 0.005 \
        or financial["receipts"] or financial["payments"] or financial["adjustments"]


def consolidated_report_context(date_from=None, date_to=None, session=None):
    session = session or db.session

    completed_orders = sorted(
        [
            o for o in session.query(Order).filter(Order.status == "Completed").all()
            if _in_range(o.completion_date or o.order_date, date_from, date_to)
        ],
        key=lambda o: (_as_date(o.completion_date or o.order_date), o.id),
    )
    diesel_rows = sorted(
        [
            e for e in session.query(DieselEntry).filter(DieselEntry.approval_status == "approved").all()
            if _in_range(e.date, date_from, date_to)
        ],
        key=lambda e: (_as_date(e.date), e.id),
    )
    ledger_rows = sorted(
        [
            t for t in session.query(Transaction).filter(Transaction.approval_status == "approved").all()
            if _in_range(t.date, date_from, date_to)
        ],
        key=lambda t: (_as_date(t.date), t.id),
    )

    # ---- per-account statements ----
    contractor_sections = []
    for contractor in session.query(Contractor).order_by(Contractor.name.asc()).all():
        rows = [o for o in completed_orders if o.contractor_id == contractor.id]
        financial = entity_period_financials(
            "contractor", contractor.id, start_date=date_from, end_date=date_to,
            period_activity_total=sum(_safe(o.billable_amount) for o in rows), session=session,
        )
        if _significant(financial, rows):
            contractor_sections.append({"entity": contractor, "financial": financial, "orders": rows})

    owner_sections = []
    vehicles_by_owner = {}
    for vehicle in session.query(Vehicle).all():
        if vehicle.owner_id:
            vehicles_by_owner.setdefault(vehicle.owner_id, set()).add(vehicle.id)
    for owner in session.query(VehicleOwner).order_by(VehicleOwner.name.asc()).all():
        vehicle_ids = vehicles_by_owner.get(owner.id, set())
        rows = [o for o in completed_orders if o.vehicle_id in vehicle_ids]
        fuel = [e for e in diesel_rows if e.vehicle_id in vehicle_ids]
        trips_amount = sum(_safe(o.remaining_vehicle_payment()) for o in rows)
        fuel_amount = sum(_safe(e.amount) for e in fuel)
        financial = entity_period_financials(
            "vehicle_owner", owner.id, start_date=date_from, end_date=date_to,
            period_activity_total=trips_amount - fuel_amount, session=session,
        )
        if _significant(financial, rows or fuel):
            owner_sections.append({
                "entity": owner, "financial": financial, "orders": rows,
                "diesel_rows": fuel, "trips_amount": trips_amount, "fuel_amount": fuel_amount,
            })

    pump_sections = []
    for pump in session.query(PetrolPump).order_by(PetrolPump.name.asc()).all():
        rows = [e for e in diesel_rows if e.petrol_pump_id == pump.id]
        financial = entity_period_financials(
            "petrol_pump", pump.id, start_date=date_from, end_date=date_to,
            period_activity_total=sum(_safe(e.amount) for e in rows), session=session,
        )
        if _significant(financial, rows):
            pump_sections.append({"entity": pump, "financial": financial, "diesel_rows": rows})

    plant_sections = []
    loadings_all = (
        session.query(OrderLoading)
        .join(Order, Order.id == OrderLoading.order_id)
        .filter(OrderLoading.plant_amount > 0, Order.status == "Completed")
        .all()
    )
    for plant in session.query(Plant).order_by(Plant.name.asc()).all():
        rows = [
            l for l in loadings_all
            if l.plant_id == plant.id and l.order is not None
            and _in_range(l.order.completion_date or l.order.order_date, date_from, date_to)
        ]
        financial = entity_period_financials(
            "plant", plant.id, start_date=date_from, end_date=date_to,
            period_activity_total=sum(_safe(l.plant_amount) for l in rows), session=session,
        )
        if _significant(financial, rows):
            plant_sections.append({"entity": plant, "financial": financial, "loadings": rows})

    # ---- position summary (previous -> current per account) ----
    def position_rows(sections, type_label, payable):
        rows = []
        for section in sections:
            financial = section["financial"]
            rows.append({
                "type": type_label,
                "name": financial["entity_name"],
                "payable": payable,
                "previous": financial["previous_balance"],
                "activity": financial["period_activity"],
                "received": financial["receipts_total"],
                "paid": financial["payments_total"],
                "current": financial["current_total"],
            })
        return rows

    position = (
        position_rows(contractor_sections, "Contractor", payable=False)
        + position_rows(owner_sections, "Vehicle Owner", payable=True)
        + position_rows(pump_sections, "Petrol Pump", payable=True)
        + position_rows(plant_sections, "Plant", payable=True)
    )
    total_receivable = sum(r["current"] for r in position if not r["payable"])
    total_payable = sum(r["current"] for r in position if r["payable"])

    # ---- profit & loss for the period ----
    revenue = sum(_safe(o.billable_amount) for o in completed_orders)
    vehicle_cost = sum(_safe(o.total_vehicle_amount()) for o in completed_orders)
    plant_cost = sum(
        _safe(l.plant_amount) for section in plant_sections for l in section["loadings"]
    )
    profit = revenue - vehicle_cost - plant_cost

    return {
        "date_from": date_from,
        "date_to": date_to,
        "generated_at": datetime.now(),
        "contractor_sections": contractor_sections,
        "owner_sections": owner_sections,
        "pump_sections": pump_sections,
        "plant_sections": plant_sections,
        "position": position,
        "total_receivable": total_receivable,
        "total_payable": total_payable,
        "net_position": total_receivable - total_payable,
        "pnl": {
            "revenue": revenue,
            "vehicle_cost": vehicle_cost,
            "plant_cost": plant_cost,
            "profit": profit,
            "trips": len(completed_orders),
        },
        "orders_register": completed_orders,
        "fuel_register": diesel_rows,
        "ledger_register": ledger_rows,
    }
