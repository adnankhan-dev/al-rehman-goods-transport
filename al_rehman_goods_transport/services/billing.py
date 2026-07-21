import re
from html import escape
from io import BytesIO, StringIO

from datetime import UTC, datetime

from sqlalchemy.orm import joinedload

from ..extensions import db
from ..models import Bill, DieselEntry, Order, OrderLoading
from ..repositories import BillingRepository
from .exceptions import NotFoundError, ValidationError
from .finance_summary import balance_summary
from .financials import entity_period_financials
from .transactions import TransactionInput, TransactionService


class BillingService:
    def __init__(self, session=None):
        self.session = session or db.session
        self.billing = BillingRepository(self.session)
        self.transactions = TransactionService(self.session)

    def list_bills(self):
        return self.billing.list_bills()

    def list_pending_bills(self):
        return self.billing.list_pending_bills()

    def pending_bill_count(self):
        return self.billing.pending_bill_count()

    def get_bill(self, bill_id):
        bill = self.billing.get_bill(bill_id)
        if bill is None:
            raise NotFoundError("Bill not found.")
        return bill

    def approve_bill(self, bill_id, approver_id=None):
        """Approve a pending bill so it becomes visible on the ledger and can be
        settled. (Creating a bill only reserves its trips/entries; approval makes
        it live. No entity balance moves until it is settled.)"""
        from datetime import UTC, datetime

        bill = self.get_bill(bill_id)
        if bill.approval_status == "approved":
            return bill
        try:
            bill.approval_status = "approved"
            bill.approved_by_id = approver_id
            bill.approved_at = datetime.now(UTC).replace(tzinfo=None)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return bill

    def reject_bill(self, bill_id):
        """Reject a pending bill: release its reserved trips/entries and delete
        it. Safe because a pending bill has posted no settlement/balance effect."""
        bill = self.get_bill(bill_id)
        if bill.approval_status == "approved":
            raise ValidationError("This bill is already approved and cannot be rejected. Delete it instead.")
        self._release_and_delete_bill(bill)

    def bill_snapshot(self, bill_id):
        bill = self.get_bill(bill_id)
        orders = (
            self.session.query(Order)
            .options(
                joinedload(Order.vehicle),
                joinedload(Order.site),
                joinedload(Order.from_site),
                joinedload(Order.material),
            )
            .filter(Order.bill_id == bill_id)
            .order_by(Order.completion_date.asc(), Order.order_date.asc(), Order.id.asc())
            .all()
        )
        diesel_activity_rows = []
        loading_activity_rows = []
        vehicle_owner_activity_rows = []
        vehicle_owner_diesel_rows = []
        standalone_diesel_rows = []
        activity_end_date = bill.end_date or bill.bill_date

        if bill.entity_type == "petrol_pump" and bill.petrol_pump_id:
            # Order-linked diesel is retired — pump bills are built from the
            # Fuel Log entries only.
            by_bill = list(self.session.query(DieselEntry).filter(DieselEntry.bill_id == bill.id).order_by(DieselEntry.date.desc()).all())
            standalone_diesel_rows = by_bill if by_bill else self.billing.standalone_diesel_rows(bill.petrol_pump_id, bill.start_date, activity_end_date)

        if bill.entity_type == "plant" and bill.plant_id:
            by_bill = (
                self.session.query(OrderLoading)
                .join(Order, Order.id == OrderLoading.order_id)
                .filter(OrderLoading.bill_id == bill.id)
                .order_by(Order.completion_date.desc(), OrderLoading.id.desc())
                .all()
            )
            loading_activity_rows = by_bill if by_bill else self.billing.plant_activity_rows(bill.plant_id, bill.start_date, activity_end_date, bill.material_type or None)

        if bill.entity_type == "vehicle_owner" and bill.vehicle_owner_id:
            by_bill = (
                self.session.query(Order)
                .filter(Order.vehicle_owner_bill_id == bill.id)
                .order_by(Order.completion_date.desc(), Order.id.desc())
                .all()
            )
            vehicle_owner_activity_rows = by_bill if by_bill else self.billing.vehicle_owner_activity_rows(bill.vehicle_owner_id, bill.start_date, activity_end_date)
            vehicle_owner_diesel_rows = (
                self.session.query(DieselEntry)
                .filter(DieselEntry.vehicle_owner_bill_id == bill.id)
                .order_by(DieselEntry.date.desc(), DieselEntry.id.desc())
                .all()
            )
        linked_orders = self._linked_orders_for_bill(bill, orders, diesel_activity_rows, loading_activity_rows, vehicle_owner_activity_rows)
        orders_grouped = self._group_orders_by_material(linked_orders)
        # Nested To site -> From site -> material grouping for the printed/viewed bill.
        orders_by_site = group_orders_by_site_and_material(linked_orders)
        material_summary = [
            {
                "material": g["material"],
                "unit": g["unit"],
                "trip_count": g["trip_count"],
                "total_quantity": g["total_quantity"],
                "total_amount": g["total_amount"],
                "contractor_rate": g.get("contractor_rate"),
            }
            for g in orders_grouped
        ]

        trips_gross = sum(o.remaining_vehicle_payment() for o in vehicle_owner_activity_rows)
        diesel_deduction_total = sum(float(d.amount or 0) for d in vehicle_owner_diesel_rows)
        # Vehicle-owner bills are presented grouped by vehicle.
        owner_vehicle_groups = []
        if bill.entity_type == "vehicle_owner":
            owner_vehicle_groups = group_owner_activity_by_vehicle(vehicle_owner_activity_rows, vehicle_owner_diesel_rows)
        related_transactions = self.related_transactions(bill)

        # Financial picture for EVERY bill, computed live at render time from
        # the ledger: previous balance = all activity before the bill period
        # (excluding this bill's own rows); receipts/payments inside the period
        # fall into this bill automatically, even when posted in back dates.
        financial_summary = entity_period_financials(
            bill.entity_type,
            self._bill_entity_id(bill),
            start_date=bill.start_date,
            end_date=bill.end_date,
            exclude_bill_id=bill.id,
            period_activity_total=float(bill.total_amount or 0),
            session=self.session,
        )

        return {
            "bill": bill,
            "financial_summary": financial_summary,
            "addable_candidates": self.billable_candidates_for_bill(bill),
            "orders": linked_orders,
            "orders_grouped": orders_grouped,
            "orders_by_site": orders_by_site,
            "material_summary": material_summary,
            "linked_trip_count": len(linked_orders),
            "total_quantity": sum((order.delivered_quantity or order.quantity or 0) for order in linked_orders),
            "related_transactions": related_transactions,
            "diesel_activity_rows": diesel_activity_rows,
            "standalone_diesel_rows": standalone_diesel_rows,
            "loading_activity_rows": loading_activity_rows,
            "vehicle_owner_activity_rows": vehicle_owner_activity_rows,
            "vehicle_owner_diesel_rows": vehicle_owner_diesel_rows,
            "owner_vehicle_groups": owner_vehicle_groups,
            "trips_gross": trips_gross,
            "diesel_deduction_total": diesel_deduction_total,
            "balance_summary": balance_summary(bill.entity_type, self._entity_balance(bill)),
        }

    def filter_options(self, entity_type, entity_id):
        if entity_type == "contractor" and entity_id:
            return self.billing.contractor_filter_options(entity_id)
        return {"sites": [], "materials": []}

    def bill_context(self, entity_type, entity_id, site_ids=None, material_type=None, start_date=None, end_date=None):
        selected_entity = self._get_entity(entity_type, entity_id) if entity_id else None
        candidate_orders = []
        candidate_diesel_entries = []
        candidate_plant_loadings = []

        candidate_vehicle_owner_diesel_entries = []

        # The bill timeframe drives the FINANCIAL section only — it does not
        # limit which unbilled records can be picked (backdated trips/entries
        # must remain billable), so the pick lists are date-unbounded.
        if selected_entity:
            if entity_type == "contractor":
                candidate_orders = self.billing.contractor_billable_orders(entity_id, site_ids, material_type)
            elif entity_type == "petrol_pump":
                candidate_diesel_entries = self.billing.unbilled_diesel_entries(entity_id)
            elif entity_type == "plant":
                candidate_plant_loadings = self.billing.unbilled_plant_loadings(entity_id)
            elif entity_type == "vehicle_owner":
                candidate_orders = self.billing.vehicle_owner_billable_orders(entity_id)
                candidate_vehicle_owner_diesel_entries = self.billing.diesel_entries_for_vehicle_owner(entity_id)

        if entity_type == "contractor":
            candidate_total_amount = sum(o.billable_amount for o in candidate_orders)
            candidate_total_quantity = sum((o.delivered_quantity or o.quantity or 0) for o in candidate_orders)
        elif entity_type == "petrol_pump":
            candidate_total_amount = sum(e.amount or 0 for e in candidate_diesel_entries)
            candidate_total_quantity = sum(e.litres or 0 for e in candidate_diesel_entries)
        elif entity_type == "plant":
            candidate_total_amount = sum(l.plant_amount or 0 for l in candidate_plant_loadings)
            candidate_total_quantity = sum(l.load_quantity or 0 for l in candidate_plant_loadings)
        elif entity_type == "vehicle_owner":
            trips_gross = sum(o.remaining_vehicle_payment() for o in candidate_orders)
            diesel_deductions = sum(d.amount or 0 for d in candidate_vehicle_owner_diesel_entries)
            candidate_total_amount = trips_gross - diesel_deductions
            candidate_total_quantity = sum((o.delivered_quantity or o.quantity or 0) for o in candidate_orders)
        else:
            candidate_total_amount = 0.0
            candidate_total_quantity = 0.0

        entity_transactions = (
            self.transactions.transactions_for_entity(entity_type, entity_id)
            if selected_entity and entity_id
            else []
        )
        return {
            "selected_entity": selected_entity,
            "candidate_orders": candidate_orders,
            "candidate_diesel_entries": candidate_diesel_entries,
            "candidate_plant_loadings": candidate_plant_loadings,
            "candidate_vehicle_owner_diesel_entries": candidate_vehicle_owner_diesel_entries,
            "entity_transactions": entity_transactions,
            "entity_balance": getattr(selected_entity, "balance", 0.0) if selected_entity else 0.0,
            "entity_balance_summary": balance_summary(entity_type, getattr(selected_entity, "balance", 0.0) if selected_entity else 0.0),
            "candidate_total_amount": candidate_total_amount,
            "candidate_total_quantity": candidate_total_quantity,
            "suggested_start_date": self.suggested_start_date(entity_type, entity_id) if selected_entity else None,
        }

    def suggested_start_date(self, entity_type, entity_id):
        """Day after the entity's last bill period ended — the default start
        for the next bill so periods chain without gaps. None for a first bill."""
        from datetime import timedelta

        if not entity_id:
            return None
        field = {
            "contractor": Bill.contractor_id,
            "plant": Bill.plant_id,
            "petrol_pump": Bill.petrol_pump_id,
            "vehicle_owner": Bill.vehicle_owner_id,
        }.get(entity_type)
        if field is None:
            return None
        last_bill = (
            self.session.query(Bill)
            .filter(field == entity_id, Bill.end_date.isnot(None))
            .order_by(Bill.end_date.desc(), Bill.id.desc())
            .first()
        )
        if last_bill is None:
            return None
        last_end = last_bill.end_date.date() if hasattr(last_bill.end_date, "date") else last_bill.end_date
        return last_end + timedelta(days=1)

    def create_bill(self, entity_type, entity_id, order_ids=None, entry_ids=None, loading_ids=None, site_ids=None, material_type=None, start_date=None, end_date=None, notes=None):
        entity = self._get_entity(entity_type, entity_id)
        bill_date = datetime.now(UTC).replace(tzinfo=None)

        # The bill timeframe is mandatory: it drives the financial section
        # (previous balance as on the day before the start; receipts/payments
        # dated inside the period — even posted later in back dates — reflect
        # in this bill automatically).
        if not start_date or not end_date:
            raise ValidationError("Select the bill timeframe (start and end dates) before creating the bill.")
        if (end_date.date() if hasattr(end_date, "date") else end_date) < (start_date.date() if hasattr(start_date, "date") else start_date):
            raise ValidationError("The bill end date cannot be before its start date.")

        selected_orders = []
        selected_entries = []
        selected_loadings = []

        if entity_type == "contractor":
            if not order_ids:
                raise ValidationError("Select at least one delivered trip to create a bill.")
            candidates = self.billing.contractor_billable_orders(entity_id, site_ids, material_type)
            eligible = {o.id for o in candidates}
            if any(oid not in eligible for oid in order_ids):
                raise ValidationError("Some selected trips are no longer available for billing.")
            selected_orders = [o for o in candidates if o.id in set(order_ids)]
            total_amount = sum(o.billable_amount for o in selected_orders)

        elif entity_type == "petrol_pump":
            if not entry_ids:
                raise ValidationError("Select at least one diesel entry to create a bill.")
            candidates = self.billing.unbilled_diesel_entries(entity_id)
            eligible = {e.id for e in candidates}
            if any(eid not in eligible for eid in entry_ids):
                raise ValidationError("Some selected diesel entries are no longer available for billing.")
            selected_entries = [e for e in candidates if e.id in set(entry_ids)]
            total_amount = sum(e.amount or 0 for e in selected_entries)
            if total_amount <= 0:
                raise ValidationError("Total amount of selected entries must be greater than zero.")

        elif entity_type == "plant":
            if not loading_ids:
                raise ValidationError("Select at least one loading record to create a bill.")
            candidates = self.billing.unbilled_plant_loadings(entity_id)
            eligible = {l.id for l in candidates}
            if any(lid not in eligible for lid in loading_ids):
                raise ValidationError("Some selected loadings are no longer available for billing.")
            selected_loadings = [l for l in candidates if l.id in set(loading_ids)]
            total_amount = sum(l.plant_amount or 0 for l in selected_loadings)
            if total_amount <= 0:
                raise ValidationError("Total amount of selected loadings must be greater than zero.")

        elif entity_type == "vehicle_owner":
            if not order_ids:
                raise ValidationError("Select at least one order to create a bill.")
            candidates = self.billing.vehicle_owner_billable_orders(entity_id)
            eligible = {o.id for o in candidates}
            if any(oid not in eligible for oid in order_ids):
                raise ValidationError("Some selected orders are no longer available for billing.")
            selected_orders = [o for o in candidates if o.id in set(order_ids)]
            trips_gross = sum(o.remaining_vehicle_payment() for o in selected_orders)
            # Auto-attach all unlinked diesel entries for the owner's vehicles in the period
            diesel_deductions = self.billing.diesel_entries_for_vehicle_owner(entity_id)
            diesel_total = sum(d.amount or 0 for d in diesel_deductions)
            total_amount = trips_gross - diesel_total

        else:
            total_amount = float(getattr(entity, "balance", 0.0) or 0.0)
            if total_amount <= 0:
                raise ValidationError("Selected account does not have an outstanding balance to bill.")

        bill = Bill(
            bill_number=self.billing.next_bill_number(entity_name=getattr(entity, "name", None)),
            entity_type=entity_type,
            contractor_id=entity.id if entity_type == "contractor" else None,
            plant_id=entity.id if entity_type == "plant" else None,
            petrol_pump_id=entity.id if entity_type == "petrol_pump" else None,
            vehicle_owner_id=entity.id if entity_type == "vehicle_owner" else None,
            bill_date=bill_date,
            start_date=start_date,
            end_date=end_date,
            material_type=material_type or None,
            notes=notes,
            total_amount=total_amount,
            approval_status="pending",
        )

        try:
            self.billing.add_bill(bill)
            for order in selected_orders:
                if entity_type == "contractor":
                    order.bill_id = bill.id
                    order.billed_at = bill_date
                elif entity_type == "vehicle_owner":
                    order.vehicle_owner_bill_id = bill.id
            for entry in selected_entries:
                entry.bill_id = bill.id
            for loading in selected_loadings:
                loading.bill_id = bill.id
            if entity_type == "vehicle_owner":
                for d_entry in diesel_deductions:
                    d_entry.vehicle_owner_bill_id = bill.id
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return bill, selected_orders

    # Bill settlement was retired: bills are pure period documents. Money
    # movement is recorded only as ledger receipts/payments, which every
    # bill/statement reflects by date in its Account Summary section.

    def delete_bill(self, bill_id):
        bill = self.get_bill(bill_id)
        if (bill.settled_amount or 0.0) > 0:
            raise ValidationError(
                "This bill has recorded settlements and cannot be deleted. Reverse the settlement transactions first, then delete and recreate the bill."
            )
        return self._release_and_delete_bill(bill)

    def _release_and_delete_bill(self, bill):
        # Release every record this bill claimed so the trips/entries become
        # available for billing again, then remove the bill itself.
        self.session.query(Order).filter(Order.bill_id == bill.id).update(
            {Order.bill_id: None, Order.billed_at: None}, synchronize_session=False
        )
        self.session.query(Order).filter(Order.vehicle_owner_bill_id == bill.id).update(
            {Order.vehicle_owner_bill_id: None}, synchronize_session=False
        )
        self.session.query(DieselEntry).filter(DieselEntry.bill_id == bill.id).update(
            {DieselEntry.bill_id: None}, synchronize_session=False
        )
        self.session.query(DieselEntry).filter(DieselEntry.vehicle_owner_bill_id == bill.id).update(
            {DieselEntry.vehicle_owner_bill_id: None}, synchronize_session=False
        )
        self.session.query(OrderLoading).filter(OrderLoading.bill_id == bill.id).update(
            {OrderLoading.bill_id: None}, synchronize_session=False
        )

        bill_number = bill.bill_number
        try:
            self.session.delete(bill)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return bill_number

    def related_transactions(self, bill):
        return self.transactions.transactions_for_entity(bill.entity_type, self._bill_entity_id(bill))

    # ── Admin bill editing (permission: ledger.admin) ────────────────────────

    def _recompute_bill_total(self, bill):
        """Recalculate a bill's total from the records currently attached."""
        # The session runs with autoflush off — push pending attach/detach
        # changes so the queries below see the current linkage.
        self.session.flush()
        if bill.entity_type == "contractor":
            orders = self.session.query(Order).filter(Order.bill_id == bill.id).all()
            bill.total_amount = sum(float(o.billable_amount or 0) for o in orders)
        elif bill.entity_type == "vehicle_owner":
            orders = self.session.query(Order).filter(Order.vehicle_owner_bill_id == bill.id).all()
            diesel = self.session.query(DieselEntry).filter(DieselEntry.vehicle_owner_bill_id == bill.id).all()
            bill.total_amount = sum(float(o.remaining_vehicle_payment() or 0) for o in orders) - sum(float(d.amount or 0) for d in diesel)
        elif bill.entity_type == "petrol_pump":
            entries = self.session.query(DieselEntry).filter(DieselEntry.bill_id == bill.id).all()
            bill.total_amount = sum(float(e.amount or 0) for e in entries)
        elif bill.entity_type == "plant":
            loadings = self.session.query(OrderLoading).filter(OrderLoading.bill_id == bill.id).all()
            bill.total_amount = sum(float(l.plant_amount or 0) for l in loadings)
        return bill.total_amount

    def remove_order_from_bill(self, bill_id, record_id, kind=None):
        """Detach one record (trip / diesel entry / loading, per bill type)
        from a bill and recalculate the total. The record becomes billable
        again. For vehicle-owner bills `kind='diesel'` targets a fuel-log
        entry (order ids and diesel ids can collide numerically)."""
        bill = self.get_bill(bill_id)
        detached = False
        if bill.entity_type == "contractor":
            order = self.session.get(Order, record_id)
            if order and order.bill_id == bill.id:
                order.bill_id = None
                order.billed_at = None
                detached = True
        elif bill.entity_type == "vehicle_owner":
            if kind == "diesel":
                entry = self.session.get(DieselEntry, record_id)
                if entry and entry.vehicle_owner_bill_id == bill.id:
                    entry.vehicle_owner_bill_id = None
                    detached = True
            else:
                order = self.session.get(Order, record_id)
                if order and order.vehicle_owner_bill_id == bill.id:
                    order.vehicle_owner_bill_id = None
                    detached = True
        elif bill.entity_type == "petrol_pump":
            entry = self.session.get(DieselEntry, record_id)
            if entry and entry.bill_id == bill.id:
                entry.bill_id = None
                detached = True
        elif bill.entity_type == "plant":
            loading = self.session.get(OrderLoading, record_id)
            if loading and loading.bill_id == bill.id:
                loading.bill_id = None
                detached = True
        if not detached:
            raise ValidationError("The selected record is not attached to this bill.")
        try:
            self._recompute_bill_total(bill)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return bill

    def remove_vehicle_from_bill(self, bill_id, vehicle_id):
        """Detach every record of one vehicle from a bill (trips and, for
        vehicle-owner bills, that vehicle's fuel-log diesel) and recalculate."""
        bill = self.get_bill(bill_id)
        removed = 0
        if bill.entity_type == "contractor":
            orders = self.session.query(Order).filter(Order.bill_id == bill.id, Order.vehicle_id == vehicle_id).all()
            for order in orders:
                order.bill_id = None
                order.billed_at = None
                removed += 1
        elif bill.entity_type == "vehicle_owner":
            orders = self.session.query(Order).filter(Order.vehicle_owner_bill_id == bill.id, Order.vehicle_id == vehicle_id).all()
            for order in orders:
                order.vehicle_owner_bill_id = None
                removed += 1
            diesel = self.session.query(DieselEntry).filter(DieselEntry.vehicle_owner_bill_id == bill.id, DieselEntry.vehicle_id == vehicle_id).all()
            for entry in diesel:
                entry.vehicle_owner_bill_id = None
                removed += 1
        else:
            raise ValidationError("Removing a vehicle applies to contractor and vehicle-owner bills only.")
        if not removed:
            raise ValidationError("This vehicle has no records on the bill.")
        try:
            self._recompute_bill_total(bill)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return bill, removed

    def add_orders_to_bill(self, bill_id, record_ids):
        """Attach unbilled records (trips / diesel entries / loadings, per bill
        type) to an existing bill and recalculate the total."""
        bill = self.get_bill(bill_id)
        if not record_ids:
            raise ValidationError("Select at least one record to add to the bill.")
        added = 0
        if bill.entity_type == "contractor":
            eligible = {o.id: o for o in self.billing.contractor_billable_orders(bill.contractor_id)}
            for record_id in record_ids:
                order = eligible.get(record_id)
                if order is None:
                    raise ValidationError("Some selected trips are no longer available for billing.")
                order.bill_id = bill.id
                order.billed_at = datetime.now(UTC).replace(tzinfo=None)
                added += 1
        elif bill.entity_type == "vehicle_owner":
            eligible = {o.id: o for o in self.billing.vehicle_owner_billable_orders(bill.vehicle_owner_id)}
            for record_id in record_ids:
                order = eligible.get(record_id)
                if order is None:
                    raise ValidationError("Some selected trips are no longer available for billing.")
                order.vehicle_owner_bill_id = bill.id
                added += 1
        elif bill.entity_type == "petrol_pump":
            eligible = {e.id: e for e in self.billing.unbilled_diesel_entries(bill.petrol_pump_id)}
            for record_id in record_ids:
                entry = eligible.get(record_id)
                if entry is None:
                    raise ValidationError("Some selected diesel entries are no longer available for billing.")
                entry.bill_id = bill.id
                added += 1
        elif bill.entity_type == "plant":
            eligible = {l.id: l for l in self.billing.unbilled_plant_loadings(bill.plant_id)}
            for record_id in record_ids:
                loading = eligible.get(record_id)
                if loading is None:
                    raise ValidationError("Some selected loadings are no longer available for billing.")
                loading.bill_id = bill.id
                added += 1
        try:
            self._recompute_bill_total(bill)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return bill, added

    def billable_candidates_for_bill(self, bill):
        """Unbilled records that an admin could still add to this bill."""
        if bill.entity_type == "contractor":
            return self.billing.contractor_billable_orders(bill.contractor_id)
        if bill.entity_type == "vehicle_owner":
            return self.billing.vehicle_owner_billable_orders(bill.vehicle_owner_id)
        if bill.entity_type == "petrol_pump":
            return self.billing.unbilled_diesel_entries(bill.petrol_pump_id)
        if bill.entity_type == "plant":
            return self.billing.unbilled_plant_loadings(bill.plant_id)
        return []

    def export_bill_excel(self, bill_id):
        snapshot = self.bill_snapshot(bill_id)
        bill = snapshot["bill"]
        orders = snapshot["orders"]
        orders_grouped = snapshot["orders_grouped"]
        material_summary = snapshot["material_summary"]
        related_transactions = snapshot["related_transactions"]
        diesel_activity_rows = snapshot["diesel_activity_rows"]
        standalone_diesel_rows = snapshot["standalone_diesel_rows"]
        loading_activity_rows = snapshot["loading_activity_rows"]
        vehicle_owner_activity_rows = snapshot["vehicle_owner_activity_rows"]
        balance_meta = snapshot["balance_summary"]

        def _cell(value):
            return escape("" if value is None else str(value))

        order_rows = ""
        for group in orders_grouped:
            order_rows += (
                f"<tr style='background:#dbeafe'>"
                f"<td colspan='8'><strong>{_cell(group['material'])}</strong> "
                f"&mdash; {group['trip_count']} trip{'s' if group['trip_count'] != 1 else ''}</td>"
                f"</tr>"
            )
            for order in group["orders"]:
                order_rows += (
                    "<tr>"
                    f"<td>{_cell((order.completion_date or order.order_date).strftime('%Y-%m-%d'))}</td>"
                    f"<td>{_cell(order.vehicle.vehicle_number if order.vehicle else '-')}</td>"
                    f"<td>{_cell(order.from_site.name if order.from_site else '-')}</td>"
                    f"<td>{_cell(order.site.name if order.site else '-')}</td>"
                    f"<td>{_cell(order.material_name)}</td>"
                    f"<td>{_cell(order.receipt_number or '-')}</td>"
                    f"<td>{_cell(f'{(order.delivered_quantity or order.quantity or 0):.2f} {order.unit.upper()}')}</td>"
                    f"<td>{_cell(f'{order.billable_amount:.2f}')}</td>"
                    "</tr>"
                )
            g_qty = f"{group['total_quantity']:.2f} {group['unit']}"
            g_amt = f"{group['total_amount']:.2f}"
            order_rows += (
                "<tr style='background:#f1f5f9;font-weight:bold'>"
                f"<td colspan='6'>{_cell(group['material'])} Subtotal</td>"
                f"<td>{_cell(g_qty)}</td>"
                f"<td>{_cell(g_amt)}</td>"
                "</tr>"
            )

        if not order_rows:
            order_rows = "<tr><td colspan='8'>No linked trips</td></tr>"

        material_summary_rows = "".join(
            (
                "<tr>"
                + f"<td>{_cell(item['material'])}</td>"
                + f"<td>{_cell(str(item['trip_count']))}</td>"
                + "<td>" + _cell(f"{item['total_quantity']:.2f} {item['unit']}") + "</td>"
                + "<td>" + _cell(f"{item['total_amount']:.2f}") + "</td>"
                + "</tr>"
            )
            for item in material_summary
        )
        material_summary_rows += (
            "<tr style='background:#0b2742;color:#ffffff;font-weight:bold'>"
            f"<td>Grand Total</td>"
            f"<td>{len(orders)}</td>"
            "<td>&mdash;</td>"
            f"<td>{_cell(f'{bill.total_amount:.2f}')}</td>"
            "</tr>"
        )

        transaction_rows = "".join(
            (
                "<tr>"
                f"<td>{_cell(transaction.date.strftime('%Y-%m-%d'))}</td>"
                f"<td>{_cell(transaction.type.replace('_', ' ').title())}</td>"
                f"<td>{_cell(f'{(transaction.amount or 0):.2f}')}</td>"
                f"<td>{_cell(transaction.reference or '-')}</td>"
                "</tr>"
            )
            for transaction in related_transactions
        ) or "<tr><td colspan='4'>No related transactions</td></tr>"

        operational_section = ""
        if bill.entity_type == "petrol_pump":
            standalone_rows_html = "".join(
                (
                    "<tr>"
                    f"<td>{_cell(entry.date.strftime('%Y-%m-%d'))}</td>"
                    f"<td>{_cell(entry.vehicle.vehicle_number if entry.vehicle else '-')}</td>"
                    f"<td>{_cell(entry.vehicle.owner_display_name if entry.vehicle else '-')}</td>"
                    f"<td>{_cell(entry.receipt_number or '-')}</td>"
                    f"<td>{_cell(f'{(entry.litres or 0):.2f}')}</td>"
                    f"<td>{_cell(f'{(entry.amount or 0):.2f}')}</td>"
                    "</tr>"
                )
                for entry in standalone_diesel_rows
            ) or "<tr><td colspan='6'>No diesel entries found</td></tr>"
            operational_section = (
                "<h3>Diesel Entries</h3>"
                "<table><thead><tr><th>Date</th><th>Vehicle</th><th>Owner</th><th>Receipt</th><th>Litres</th><th>Amount</th></tr></thead>"
                f"<tbody>{standalone_rows_html}</tbody></table>"
            )
            if diesel_activity_rows:
                order_linked_rows = "".join(
                    (
                        "<tr>"
                        f"<td>{_cell((entry.order.completion_date or entry.order.order_date).strftime('%Y-%m-%d'))}</td>"
                        f"<td>{_cell(entry.order.vehicle.vehicle_number if entry.order and entry.order.vehicle else '-')}</td>"
                        f"<td>{_cell(entry.receipt_number or '-')}</td>"
                        f"<td>{_cell(f'{(entry.litres or 0):.4f}')}</td>"
                        f"<td>{_cell(f'{(entry.amount or 0):.2f}')}</td>"
                        "</tr>"
                    )
                    for entry in diesel_activity_rows
                )
                operational_section += (
                    "<h3>Order-Linked Diesel Activity</h3>"
                    "<table><thead><tr><th>Date</th><th>Vehicle</th><th>Receipt</th><th>Litres</th><th>Amount</th></tr></thead>"
                    f"<tbody>{order_linked_rows}</tbody></table>"
                )
        elif bill.entity_type == "plant":
            activity_rows = "".join(
                (
                    "<tr>"
                    f"<td>{_cell((loading.order.completion_date or loading.order.order_date).strftime('%Y-%m-%d'))}</td>"
                    f"<td>{_cell(loading.order.vehicle.vehicle_number if loading.order and loading.order.vehicle else '-')}</td>"
                    f"<td>{_cell(loading.order.material_name if loading.order else '-')}</td>"
                    f"<td>{_cell(f'{(loading.load_quantity or 0):.2f} {(loading.order.unit.upper() if loading.order and loading.order.unit else '')}'.strip())}</td>"
                    f"<td>{_cell(f'{(loading.plant_amount or 0):.2f}')}</td>"
                    "</tr>"
                )
                for loading in loading_activity_rows
            ) or "<tr><td colspan='5'>No loading activity found</td></tr>"
            operational_section = (
                "<h3>Plant Loading Activity</h3>"
                "<table><thead><tr><th>Date</th><th>Vehicle</th><th>Material</th><th>Loading Qty</th><th>Amount</th></tr></thead>"
                f"<tbody>{activity_rows}</tbody></table>"
            )
        elif bill.entity_type == "vehicle_owner":
            activity_rows = "".join(
                (
                    "<tr>"
                    f"<td>{_cell((order.completion_date or order.order_date).strftime('%Y-%m-%d'))}</td>"
                    f"<td>{_cell(order.vehicle.vehicle_number if order.vehicle else '-')}</td>"
                    f"<td>{_cell(f'{order.effective_vehicle_quantity:.2f} {order.unit.upper()}')}</td>"
                    f"<td>{_cell(f'{(order.vehicle_rate or 0):.2f}')}</td>"
                    f"<td>{_cell(order.receipt_number or '-')}</td>"
                    f"<td>{_cell(f'{order.remaining_vehicle_payment():.2f}')}</td>"
                    "</tr>"
                )
                for order in vehicle_owner_activity_rows
            ) or "<tr><td colspan='6'>No vehicle owner activity found</td></tr>"
            operational_section = (
                "<h3>Vehicle Owner Activity</h3>"
                "<table><thead><tr><th>Date</th><th>Vehicle</th><th>Delivered Qty</th><th>Vehicle Rate</th><th>Delivery Receipt</th><th>Remaining Payable</th></tr></thead>"
                f"<tbody>{activity_rows}</tbody></table>"
            )

        workbook = StringIO()
        workbook.write(
            f"""
            <html>
            <head>
                <meta charset="utf-8">
                <style>
                    body {{ font-family: Arial, sans-serif; }}
                    table {{ border-collapse: collapse; width: 100%; margin-bottom: 16px; }}
                    th, td {{ border: 1px solid #cbd5e1; padding: 8px; text-align: left; }}
                    th {{ background: #e2e8f0; }}
                    .summary td:first-child {{ font-weight: bold; width: 220px; }}
                </style>
            </head>
            <body>
                <h2>Bill {escape(bill.bill_number)}</h2>
                <table class="summary">
                    <tr><td>Entity</td><td>{escape(bill.entity_name)}</td></tr>
                    <tr><td>Entity Type</td><td>{escape(bill.entity_type.replace('_', ' ').title())}</td></tr>
                    <tr><td>Bill Date</td><td>{escape(bill.bill_date.strftime('%Y-%m-%d %H:%M'))}</td></tr>
                    <tr><td>Total Amount</td><td>{escape(f'{bill.total_amount:.2f}')}</td></tr>
                    <tr><td>Settled Amount</td><td>{escape(f'{(bill.settled_amount or 0):.2f}')}</td></tr>
                    <tr><td>Outstanding Amount</td><td>{escape(f'{bill.outstanding_amount:.2f}')}</td></tr>
                    <tr><td>Balance Position</td><td>{escape(balance_meta['label'])} Rs. {escape(f"{balance_meta['amount']:.2f}")}</td></tr>
                    <tr><td>Notes</td><td>{escape(bill.notes or '-')}</td></tr>
                </table>

                <h3>Linked Trips (Grouped by Material)</h3>
                <table>
                    <thead>
                        <tr>
                            <th>Date</th>
                            <th>Vehicle</th>
                            <th>From Site</th>
                            <th>To Site</th>
                            <th>Material</th>
                            <th>Receipt</th>
                            <th>Quantity</th>
                            <th>Amount</th>
                        </tr>
                    </thead>
                    <tbody>{order_rows}</tbody>
                </table>

                <h3>Material Summary</h3>
                <table>
                    <thead>
                        <tr>
                            <th>Material</th>
                            <th>Trips</th>
                            <th>Total Quantity</th>
                            <th>Total Amount</th>
                        </tr>
                    </thead>
                    <tbody>{material_summary_rows}</tbody>
                </table>

                {operational_section}

                <h3>Related Transactions</h3>
                <table>
                    <thead>
                        <tr>
                            <th>Date</th>
                            <th>Type</th>
                            <th>Amount</th>
                            <th>Reference</th>
                        </tr>
                    </thead>
                    <tbody>{transaction_rows}</tbody>
                </table>
            </body>
            </html>
            """
        )
        filename = f"{bill.bill_number.replace('/', '-')}.xls"
        return filename, workbook.getvalue().encode("utf-8")

    _PDF_CSS_VARS = {
        "--ink": "#0f172a",
        "--muted": "#475569",
        "--soft": "#e2e8f0",
        "--panel": "#f8fafc",
        "--brand": "#0b2742",
        "--accent": "#d97706",
    }

    def render_bill_pdf(self, bill, html):
        # xhtml2pdf has no flexbox/grid support, so the screen layout's flex/grid
        # containers are swapped for table-based equivalents here. The markup and
        # data come straight from bills/print.html, so the PDF always matches it.
        # xhtml2pdf also can't resolve CSS custom properties, so var(--x) is
        # replaced with its literal value before conversion.
        for name, value in self._PDF_CSS_VARS.items():
            html = re.sub(rf"var\(\s*{re.escape(name)}\s*\)", value, html)

        pdf_overrides = """
        <style>
            .statement-actions { display: none; }
            body { padding: 0; background: #ffffff; }
            .statement-shell { box-shadow: none; border-radius: 0; max-width: none; }
            .statement-body { padding: 0; }
            .statement-header { display: table; width: 100%; }
            .statement-company, .statement-meta { display: table-cell; vertical-align: top; }
            .statement-meta { width: 38%; }
            .meta-row { display: table-row; }
            .meta-label, .meta-value { display: table-cell; padding: 3px 0; }
            .meta-value { text-align: right; }
            .summary-grid { display: table; width: 100%; table-layout: fixed; }
            .summary-card { display: table-cell; }
            .statement-table th, .statement-table td { font-size: 0.72rem; padding: 4px 6px; }
            .statement-table td:first-child, .statement-table th:first-child { white-space: nowrap; }
        </style>
        """
        html = html.replace("</head>", pdf_overrides + "</head>")

        from xhtml2pdf import pisa

        buffer = BytesIO()
        result = pisa.CreatePDF(html, dest=buffer, encoding="utf-8")
        if result.err:
            raise ValidationError("PDF could not be generated for this bill.")

        filename = f"{bill.bill_number.replace('/', '-')}.pdf"
        return filename, buffer.getvalue()

    def _group_orders_by_material(self, orders):
        groups = {}
        for order in orders:
            mat = order.material_name or "Unknown"
            unit = (order.unit or "cft").upper()
            rate = round(order.contractor_rate, 4) if order.contractor_rate else None
            key = (mat, unit, rate)
            if key not in groups:
                groups[key] = {
                    "material": mat,
                    "unit": unit,
                    "contractor_rate": rate,
                    "orders": [],
                    "total_quantity": 0.0,
                    "total_amount": 0.0,
                    "total_vehicle_amount": 0.0,
                    "total_diesel_amount": 0.0,
                    "total_advance_amount": 0.0,
                    "total_net_payable": 0.0,
                    "trip_count": 0,
                }
            group = groups[key]
            group["orders"].append(order)
            group["total_quantity"] += float(order.delivered_quantity or order.quantity or 0)
            group["total_amount"] += float(order.billable_amount)
            group["total_vehicle_amount"] += float(order.total_vehicle_amount())
            group["total_diesel_amount"] += float(order.total_diesel_amount())
            group["total_advance_amount"] += float(order.total_advance_amount())
            group["total_net_payable"] += float(order.remaining_vehicle_payment())
            group["trip_count"] += 1

        return sorted(
            groups.values(),
            key=lambda g: (g["material"], g["contractor_rate"] if g["contractor_rate"] is not None else float("inf")),
        )

    def _bill_entity_id(self, bill):
        return bill.contractor_id or bill.plant_id or bill.petrol_pump_id or bill.vehicle_owner_id

    def _entity_balance(self, bill):
        if bill.contractor:
            return bill.contractor.balance
        if bill.plant:
            return bill.plant.balance
        if bill.petrol_pump:
            return bill.petrol_pump.balance
        if bill.vehicle_owner:
            return bill.vehicle_owner.balance
        return 0.0

    def _linked_orders_for_bill(self, bill, direct_orders, diesel_activity_rows, loading_activity_rows, vehicle_owner_activity_rows):
        if direct_orders:
            return direct_orders

        derived_orders = []
        if bill.entity_type == "petrol_pump":
            derived_orders = [entry.order for entry in diesel_activity_rows if getattr(entry, "order", None)]
        elif bill.entity_type == "plant":
            derived_orders = [loading.order for loading in loading_activity_rows if getattr(loading, "order", None)]
        elif bill.entity_type == "vehicle_owner":
            derived_orders = [order for order in vehicle_owner_activity_rows if order]

        unique_orders = {}
        for order in derived_orders:
            unique_orders[order.id] = order

        return sorted(
            unique_orders.values(),
            key=lambda order: (order.completion_date or order.order_date, order.id),
            reverse=True,
        )

    def _get_entity(self, entity_type, entity_id):
        entity = None
        if entity_type == "contractor":
            entity = self.billing.get_contractor(entity_id)
        elif entity_type == "plant":
            entity = self.billing.get_plant(entity_id)
        elif entity_type == "petrol_pump":
            entity = self.billing.get_petrol_pump(entity_id)
        elif entity_type == "vehicle_owner":
            entity = self.billing.get_vehicle_owner(entity_id)

        if entity is None:
            raise NotFoundError("Selected billing entity was not found.")
        return entity


def generate_bill_number(entity_name=None):
    return BillingService().billing.next_bill_number(entity_name=entity_name)


def contractor_billable_orders(contractor_id, site_ids=None, material_type=None, start_date=None, end_date=None):
    return BillingService().billing.contractor_billable_orders(contractor_id, site_ids, material_type, start_date, end_date)


def contractor_filter_options(contractor_id):
    return BillingService().billing.contractor_filter_options(contractor_id)


def create_contractor_bill(contractor_id, order_ids, site_ids=None, material_type=None, start_date=None, end_date=None, notes=None):
    return BillingService().create_bill(
        "contractor",
        contractor_id,
        order_ids=order_ids,
        site_ids=site_ids,
        material_type=material_type,
        start_date=start_date,
        end_date=end_date,
        notes=notes,
    )


def refresh_bill_total(bill):
    if bill is None:
        return None
    bill.total_amount = sum(order.billable_amount for order in bill.orders) if bill.orders else bill.total_amount
    return bill


# --- Hierarchical grouping: To site -> From site -> (material, contractor rate) ---
# Shared by the bill snapshot and the orders print statement so both lay trips
# out the same way.

_GROUP_TOTAL_KEYS = (
    "trip_count", "total_quantity", "total_amount", "total_vehicle_amount",
    "total_diesel_amount", "total_advance_amount", "total_net_payable",
)


def _new_material_group(material, unit, contractor_rate):
    group = {"material": material, "unit": unit, "contractor_rate": contractor_rate, "orders": []}
    group.update({key: 0.0 for key in _GROUP_TOTAL_KEYS})
    return group


def _accumulate_material_group(group, order):
    group["orders"].append(order)
    group["trip_count"] += 1
    group["total_quantity"] += float(order.delivered_quantity or order.quantity or 0)
    group["total_amount"] += float(order.billable_amount)
    group["total_vehicle_amount"] += float(order.total_vehicle_amount())
    group["total_diesel_amount"] += float(order.total_diesel_amount())
    group["total_advance_amount"] += float(order.total_advance_amount())
    group["total_net_payable"] += float(order.remaining_vehicle_payment())


def _empty_totals():
    return {key: 0.0 for key in _GROUP_TOTAL_KEYS}


def _add_totals(acc, group):
    for key in _GROUP_TOTAL_KEYS:
        acc[key] += group[key]


def _sorted_material_groups(material_groups):
    return sorted(
        material_groups.values(),
        key=lambda g: (g["material"], g["contractor_rate"] if g["contractor_rate"] is not None else float("inf")),
    )


def group_owner_activity_by_vehicle(orders, diesel_rows=None, advance_rows=None):
    """Group an owner's trips (plus optional fuel-log diesel and vehicle
    advances) by vehicle, for owner statements and vehicle-owner bills.

    Returns vehicle groups sorted by vehicle number, each:
        {vehicle, vehicle_number, orders, diesel_rows, advance_rows,
         trip_count, total_delivered, gross, order_diesel, net_payable,
         standalone_diesel, advances, vehicle_payable}
    where net_payable = gross − order diesel, and
    vehicle_payable = net_payable − fuel-log diesel − advances to the vehicle."""
    groups = {}

    def group_for(vehicle, vehicle_id):
        key = vehicle_id if vehicle_id is not None else 0
        if key not in groups:
            groups[key] = {
                "vehicle": vehicle,
                "vehicle_number": vehicle.vehicle_number if vehicle else "—",
                "orders": [],
                "diesel_rows": [],
                "advance_rows": [],
                "trip_count": 0,
                "total_delivered": 0.0,
                "gross": 0.0,
                "order_diesel": 0.0,
                "net_payable": 0.0,
                "standalone_diesel": 0.0,
                "advances": 0.0,
                "vehicle_payable": 0.0,
            }
        return groups[key]

    for order in orders or []:
        group = group_for(order.vehicle, order.vehicle_id)
        group["orders"].append(order)
        group["trip_count"] += 1
        # Vehicle-side quantity (falls back to delivered qty when not adjusted).
        group["total_delivered"] += float(order.effective_vehicle_quantity)
        group["gross"] += float(order.total_vehicle_amount())
        group["order_diesel"] += float(order.total_diesel_amount())
        group["net_payable"] += float(order.remaining_vehicle_payment())

    for entry in diesel_rows or []:
        group = group_for(entry.vehicle, entry.vehicle_id)
        group["diesel_rows"].append(entry)
        group["standalone_diesel"] += float(entry.amount or 0)

    for txn in advance_rows or []:
        group = group_for(txn.vehicle, txn.vehicle_id)
        group["advance_rows"].append(txn)
        group["advances"] += float(txn.amount or 0)

    result = sorted(groups.values(), key=lambda g: g["vehicle_number"])
    for group in result:
        group["vehicle_payable"] = group["net_payable"] - group["standalone_diesel"] - group["advances"]
    return result


def group_orders_by_site_and_material(orders):
    """Nest orders by To site, then From site, then material + contractor rate.

    Returns a list of to-site groups, each:
        {to_site, from_groups: [{from_site, material_groups: [...], subtotal}], subtotal}
    where every material group is the same shape _group_orders_by_material yields,
    and subtotals aggregate the totals at the from-site and to-site levels."""
    to_groups = {}
    for order in orders:
        to_name = order.site.name if order.site else "—"
        to_key = order.site_id if order.site_id is not None else f"name:{to_name}"
        from_name = order.from_site.name if order.from_site else "—"
        from_key = order.from_site_id if order.from_site_id is not None else f"name:{from_name}"
        material = order.material_name or "Unknown"
        unit = (order.unit or "cft").upper()
        contractor_rate = round(order.contractor_rate, 4) if order.contractor_rate else None
        material_key = (material, unit, contractor_rate)

        to_group = to_groups.setdefault(to_key, {"to_site": to_name, "from_groups": {}})
        from_group = to_group["from_groups"].setdefault(from_key, {"from_site": from_name, "material_groups": {}})
        material_group = from_group["material_groups"].setdefault(material_key, _new_material_group(material, unit, contractor_rate))
        _accumulate_material_group(material_group, order)

    result = []
    for to_group in to_groups.values():
        from_list = []
        to_total = _empty_totals()
        for from_group in to_group["from_groups"].values():
            material_list = _sorted_material_groups(from_group["material_groups"])
            from_total = _empty_totals()
            for material_group in material_list:
                _add_totals(from_total, material_group)
                _add_totals(to_total, material_group)
            from_list.append({"from_site": from_group["from_site"], "material_groups": material_list, "subtotal": from_total})
        from_list.sort(key=lambda item: item["from_site"])
        result.append({"to_site": to_group["to_site"], "from_groups": from_list, "subtotal": to_total})
    result.sort(key=lambda item: item["to_site"])
    return result
