from datetime import datetime, UTC

from sqlalchemy import func

from ..extensions import db
from ..models import Bill, Contractor, DieselEntry, Order, Site, Vehicle, VehicleOwner


def get_dashboard_metrics():
    now = datetime.now(UTC).replace(tzinfo=None)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # ── Operational counts ────────────────────────────────────────────────────
    # Approved orders only — pending-approval orders are excluded everywhere.
    approved = Order.approval_status == "approved"
    total_orders = Order.query.filter(approved).count()
    orders_this_month = Order.query.filter(approved, Order.order_date >= month_start).count()
    unbilled_count = Order.query.filter(
        approved,
        Order.status == "Completed",
        Order.bill_id.is_(None),
    ).count()

    total_contractors = Contractor.query.count()
    total_vehicles = Vehicle.query.count()
    total_sites = Site.query.count()

    # ── Financial snapshot (open bills only) ─────────────────────────────────
    open_bills = Bill.query.filter(Bill.approval_status == "approved").all()
    contractor_receivables = sum(
        b.outstanding_amount for b in open_bills
        if b.entity_type == "contractor" and b.outstanding_amount > 0
    )
    owner_payables = sum(
        b.outstanding_amount for b in open_bills
        if b.entity_type == "vehicle_owner" and b.outstanding_amount > 0
    )
    plant_payables = sum(
        b.outstanding_amount for b in open_bills
        if b.entity_type == "plant" and b.outstanding_amount > 0
    )
    pump_payables = sum(
        b.outstanding_amount for b in open_bills
        if b.entity_type == "petrol_pump" and b.outstanding_amount > 0
    )
    total_payables = owner_payables + plant_payables + pump_payables

    # ── Recent orders (last 8 for the dashboard table) ───────────────────────
    recent_orders = (
        Order.query
        .filter(approved)
        .order_by(Order.order_date.desc())
        .limit(8)
        .all()
    )

    # ── Latest open bills (last 5 unsettled) ─────────────────────────────────
    recent_open_bills = (
        Bill.query
        .filter(Bill.approval_status == "approved", Bill.settled_amount < Bill.total_amount)
        .order_by(Bill.bill_date.desc())
        .limit(5)
        .all()
    )

    # ── Today's activity & P&L ────────────────────────────────────────────────
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    orders_today_list = Order.query.filter(
        approved,
        Order.order_date >= today_start,
        Order.status == "Completed",
    ).all()
    orders_today = Order.query.filter(approved, Order.order_date >= today_start).count()

    diesel_approved = DieselEntry.approval_status == "approved"
    diesel_entries_today = DieselEntry.query.filter(
        diesel_approved,
        DieselEntry.date >= today_start.date()
    ).count()
    diesel_amount_today = db.session.query(func.sum(DieselEntry.amount)).filter(
        diesel_approved,
        DieselEntry.date >= today_start.date()
    ).scalar() or 0.0

    # P&L for today's completed orders. Order-linked diesel/advances are
    # retired, so a trip's cost is the vehicle amount plus plant charges —
    # same statement structure as ReportService.financial_report.
    today_revenue = sum(o.total_contractor_amount() for o in orders_today_list)
    today_vehicle_cost = sum(o.total_vehicle_amount() for o in orders_today_list)
    today_plant_cost = sum(o.plant_amount or 0 for o in orders_today_list)
    today_diesel_cost = 0.0
    today_advances = 0.0
    today_expenses = today_vehicle_cost + today_plant_cost
    today_profit = today_revenue - today_expenses

    # ── Diesel entries this month ─────────────────────────────────────────────
    diesel_this_month = db.session.query(func.sum(DieselEntry.amount)).filter(
        diesel_approved,
        DieselEntry.date >= month_start.date()
    ).scalar() or 0.0

    return {
        "now": now,
        "total_orders": total_orders,
        "orders_this_month": orders_this_month,
        "unbilled_count": unbilled_count,
        "total_contractors": total_contractors,
        "total_vehicles": total_vehicles,
        "total_sites": total_sites,
        "total_vehicle_owners": VehicleOwner.query.count(),
        "contractor_receivables": contractor_receivables,
        "owner_payables": owner_payables,
        "plant_payables": plant_payables,
        "pump_payables": pump_payables,
        "total_payables": total_payables,
        "recent_orders": recent_orders,
        "recent_open_bills": recent_open_bills,
        "diesel_this_month": diesel_this_month,
        "orders_today": orders_today,
        "diesel_entries_today": diesel_entries_today,
        "diesel_amount_today": diesel_amount_today,
        "today_revenue": today_revenue,
        "today_vehicle_cost": today_vehicle_cost,
        "today_plant_cost": today_plant_cost,
        "today_diesel_cost": today_diesel_cost,
        "today_advances": today_advances,
        "today_expenses": today_expenses,
        "today_profit": today_profit,
        "today_completed_orders": len(orders_today_list),
        "today_str": today_start.strftime("%Y-%m-%d"),
    }
