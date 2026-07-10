from datetime import UTC, datetime, time

from sqlalchemy import func

from ..extensions import db
from ..models import Bill, Contractor, DieselEntry, Order, OrderDieselEntry, OrderLoading, PetrolPump, Plant, Vehicle, VehicleOwner


def _day_start(value):
    if value is None:
        return None
    return datetime.combine(value, time.min).replace(tzinfo=None)


def _day_end(value):
    if value is None:
        return None
    return datetime.combine(value, time.max).replace(tzinfo=None)


class BillingRepository:
    def __init__(self, session=None):
        self.session = session or db.session

    def list_bills(self):
        return (
            self.session.query(Bill)
            .filter(Bill.approval_status == "approved")
            .order_by(Bill.bill_date.desc(), Bill.id.desc())
            .all()
        )

    def list_pending_bills(self):
        return (
            self.session.query(Bill)
            .filter(Bill.approval_status == "pending")
            .order_by(Bill.bill_date.desc(), Bill.id.desc())
            .all()
        )

    def pending_bill_count(self):
        return self.session.query(func.count(Bill.id)).filter(Bill.approval_status == "pending").scalar() or 0

    def get_bill(self, bill_id):
        return self.session.get(Bill, bill_id)

    def add_bill(self, bill):
        self.session.add(bill)
        self.session.flush()
        return bill

    def next_bill_number(self, entity_name=None):
        """Per-entity sequential bill number: BILL-<FIRSTWORD>-001, where
        FIRSTWORD is the uppercased first word of the account name (e.g. 'MKA' →
        BILL-MKA-001, 'Ahsan Malla Khel' → BILL-AHSAN-001). Robust against
        deleted bills — the sequence continues from the current max for the
        prefix so numbers never collide with the unique constraint."""
        import re

        name = (entity_name or "").strip()
        first_word = name.split()[0] if name else "GEN"
        slug = re.sub(r"[^A-Za-z0-9]", "", first_word).upper() or "GEN"
        prefix = f"BILL-{slug}-"
        existing = self.session.query(Bill.bill_number).filter(Bill.bill_number.like(f"{prefix}%")).all()
        max_seq = 0
        for (number,) in existing:
            tail = (number or "")[len(prefix):]
            if tail.isdigit():
                max_seq = max(max_seq, int(tail))
        return f"{prefix}{max_seq + 1:03d}"

    def get_contractor(self, contractor_id):
        return self.session.get(Contractor, contractor_id)

    def get_plant(self, plant_id):
        return self.session.get(Plant, plant_id)

    def get_petrol_pump(self, petrol_pump_id):
        return self.session.get(PetrolPump, petrol_pump_id)

    def get_vehicle_owner(self, vehicle_owner_id):
        return self.session.get(VehicleOwner, vehicle_owner_id)

    def contractor_billable_orders(self, contractor_id, site_ids=None, material_type=None, start_date=None, end_date=None):
        query = (
            self.session.query(Order)
            .filter(
                Order.contractor_id == contractor_id,
                Order.status == "Completed",
                Order.bill_id.is_(None),
            )
            .order_by(Order.completion_date.desc(), Order.order_date.desc(), Order.id.desc())
        )
        if site_ids:
            query = query.filter(Order.site_id.in_(site_ids))
        if material_type:
            query = query.filter(Order.material_type == material_type)
        start_value = _day_start(start_date)
        if start_value is not None:
            query = query.filter(func.coalesce(Order.completion_date, Order.order_date) >= start_value)
        end_value = _day_end(end_date)
        if end_value is not None:
            query = query.filter(func.coalesce(Order.completion_date, Order.order_date) <= end_value)
        return query.all()

    def contractor_filter_options(self, contractor_id):
        contractor = self.get_contractor(contractor_id)
        if contractor is None:
            return {"sites": [], "materials": []}

        materials = [
            row[0]
            for row in (
                self.session.query(Order.material_type)
                .filter(
                    Order.contractor_id == contractor_id,
                    Order.status == "Completed",
                    Order.bill_id.is_(None),
                )
                .distinct()
                .order_by(Order.material_type.asc())
                .all()
            )
            if row[0]
        ]
        return {
            "sites": contractor.sites,
            "materials": materials,
        }

    def unbilled_diesel_entries(self, petrol_pump_id, start_date=None, end_date=None):
        query = (
            self.session.query(DieselEntry)
            .filter(
                DieselEntry.petrol_pump_id == petrol_pump_id,
                DieselEntry.bill_id.is_(None),
                DieselEntry.approval_status == "approved",
            )
            .order_by(DieselEntry.date.desc(), DieselEntry.id.desc())
        )
        if start_date is not None:
            query = query.filter(DieselEntry.date >= start_date)
        if end_date is not None:
            query = query.filter(DieselEntry.date <= end_date)
        return query.all()

    def unbilled_plant_loadings(self, plant_id, start_date=None, end_date=None):
        query = (
            self.session.query(OrderLoading)
            .join(Order, Order.id == OrderLoading.order_id)
            .filter(
                OrderLoading.plant_id == plant_id,
                OrderLoading.bill_id.is_(None),
                OrderLoading.plant_amount > 0,
                Order.status == "Completed",
            )
            .order_by(Order.completion_date.desc(), Order.order_date.desc(), OrderLoading.id.desc())
        )
        if start_date is not None:
            query = query.filter(func.coalesce(Order.completion_date, Order.order_date) >= start_date)
        if end_date is not None:
            query = query.filter(func.coalesce(Order.completion_date, Order.order_date) <= end_date)
        return query.all()

    def vehicle_owner_billable_orders(self, vehicle_owner_id, start_date=None, end_date=None):
        query = (
            self.session.query(Order)
            .join(Vehicle, Vehicle.id == Order.vehicle_id)
            .filter(
                Vehicle.owner_id == vehicle_owner_id,
                Order.status == "Completed",
                Order.vehicle_owner_bill_id.is_(None),
            )
            .order_by(Order.completion_date.desc(), Order.order_date.desc(), Order.id.desc())
        )
        if start_date is not None:
            query = query.filter(func.coalesce(Order.completion_date, Order.order_date) >= start_date)
        if end_date is not None:
            query = query.filter(func.coalesce(Order.completion_date, Order.order_date) <= end_date)
        return query.all()

    def standalone_diesel_rows(self, petrol_pump_id, start_date=None, end_date=None):
        query = (
            self.session.query(DieselEntry)
            .filter(
                DieselEntry.petrol_pump_id == petrol_pump_id,
                DieselEntry.approval_status == "approved",
            )
            .order_by(DieselEntry.date.desc(), DieselEntry.id.desc())
        )
        if start_date is not None:
            query = query.filter(DieselEntry.date >= start_date)
        if end_date is not None:
            query = query.filter(DieselEntry.date <= end_date)
        return query.all()

    def petrol_pump_activity_rows(self, petrol_pump_id, start_date=None, end_date=None):
        query = (
            self.session.query(OrderDieselEntry)
            .join(Order, Order.id == OrderDieselEntry.order_id)
            .filter(
                OrderDieselEntry.petrol_pump_id == petrol_pump_id,
                Order.status == "Completed",
                OrderDieselEntry.amount > 0,
            )
            .order_by(Order.completion_date.desc(), Order.order_date.desc(), OrderDieselEntry.id.desc())
        )
        if start_date is not None:
            query = query.filter(func.coalesce(Order.completion_date, Order.order_date) >= start_date)
        if end_date is not None:
            query = query.filter(func.coalesce(Order.completion_date, Order.order_date) <= end_date)
        return query.all()

    def plant_activity_rows(self, plant_id, start_date=None, end_date=None, material_type=None):
        query = (
            self.session.query(OrderLoading)
            .join(Order, Order.id == OrderLoading.order_id)
            .filter(
                OrderLoading.plant_id == plant_id,
                Order.status == "Completed",
                OrderLoading.plant_amount > 0,
            )
            .order_by(Order.completion_date.desc(), Order.order_date.desc(), OrderLoading.id.desc())
        )
        if start_date is not None:
            query = query.filter(func.coalesce(Order.completion_date, Order.order_date) >= start_date)
        if end_date is not None:
            query = query.filter(func.coalesce(Order.completion_date, Order.order_date) <= end_date)
        if material_type:
            query = query.filter(Order.material_type == material_type)
        return query.all()

    def plant_loadings_all(self, plant_id, start_date=None, end_date=None, material_type=None):
        """Every loading for a plant (regardless of plant amount), so orders that
        selected the plant but had no plant charge still appear on the plant page."""
        query = (
            self.session.query(OrderLoading)
            .join(Order, Order.id == OrderLoading.order_id)
            .filter(OrderLoading.plant_id == plant_id, Order.approval_status == "approved")
            .order_by(Order.completion_date.desc(), Order.order_date.desc(), OrderLoading.id.desc())
        )
        if start_date is not None:
            query = query.filter(func.coalesce(Order.completion_date, Order.order_date) >= start_date)
        if end_date is not None:
            query = query.filter(func.coalesce(Order.completion_date, Order.order_date) <= end_date)
        if material_type:
            query = query.filter(Order.material_type == material_type)
        return query.all()

    def vehicle_owner_activity_rows(self, vehicle_owner_id, start_date=None, end_date=None):
        query = (
            self.session.query(Order)
            .join(Vehicle, Vehicle.id == Order.vehicle_id)
            .filter(
                Vehicle.owner_id == vehicle_owner_id,
                Order.status == "Completed",
            )
            .order_by(Order.completion_date.desc(), Order.order_date.desc(), Order.id.desc())
        )
        if start_date is not None:
            query = query.filter(func.coalesce(Order.completion_date, Order.order_date) >= start_date)
        if end_date is not None:
            query = query.filter(func.coalesce(Order.completion_date, Order.order_date) <= end_date)
        return query.all()

    def diesel_entries_for_vehicle_owner(self, vehicle_owner_id, start_date=None, end_date=None):
        """Unbilled standalone diesel entries for all vehicles of a given owner."""
        query = (
            self.session.query(DieselEntry)
            .join(Vehicle, Vehicle.id == DieselEntry.vehicle_id)
            .filter(
                Vehicle.owner_id == vehicle_owner_id,
                DieselEntry.vehicle_owner_bill_id.is_(None),
                DieselEntry.approval_status == "approved",
            )
            .order_by(DieselEntry.date.desc(), DieselEntry.id.desc())
        )
        if start_date is not None:
            start_val = start_date.date() if hasattr(start_date, "date") else start_date
            query = query.filter(DieselEntry.date >= start_val)
        if end_date is not None:
            end_val = end_date.date() if hasattr(end_date, "date") else end_date
            query = query.filter(DieselEntry.date <= end_val)
        return query.all()
