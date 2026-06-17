from html import escape
from io import BytesIO, StringIO

from datetime import UTC, datetime

from sqlalchemy.orm import joinedload

from ..extensions import db
from ..models import Bill, DieselEntry, Order, OrderLoading
from ..repositories import BillingRepository
from .exceptions import NotFoundError, ValidationError
from .finance_summary import balance_summary
from .transactions import TransactionInput, TransactionService


class BillingService:
    def __init__(self, session=None):
        self.session = session or db.session
        self.billing = BillingRepository(self.session)
        self.transactions = TransactionService(self.session)

    def list_bills(self):
        return self.billing.list_bills()

    def get_bill(self, bill_id):
        bill = self.billing.get_bill(bill_id)
        if bill is None:
            raise NotFoundError("Bill not found.")
        return bill

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
            diesel_activity_rows = self.billing.petrol_pump_activity_rows(bill.petrol_pump_id, bill.start_date, activity_end_date)
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

        return {
            "bill": bill,
            "orders": linked_orders,
            "orders_grouped": orders_grouped,
            "material_summary": material_summary,
            "linked_trip_count": len(linked_orders),
            "total_quantity": sum((order.delivered_quantity or order.quantity or 0) for order in linked_orders),
            "related_transactions": self.related_transactions(bill),
            "diesel_activity_rows": diesel_activity_rows,
            "standalone_diesel_rows": standalone_diesel_rows,
            "loading_activity_rows": loading_activity_rows,
            "vehicle_owner_activity_rows": vehicle_owner_activity_rows,
            "vehicle_owner_diesel_rows": vehicle_owner_diesel_rows,
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

        if selected_entity:
            if entity_type == "contractor":
                candidate_orders = self.billing.contractor_billable_orders(entity_id, site_ids, material_type, start_date, end_date)
            elif entity_type == "petrol_pump":
                candidate_diesel_entries = self.billing.unbilled_diesel_entries(entity_id, start_date, end_date)
            elif entity_type == "plant":
                candidate_plant_loadings = self.billing.unbilled_plant_loadings(entity_id, start_date, end_date)
            elif entity_type == "vehicle_owner":
                candidate_orders = self.billing.vehicle_owner_billable_orders(entity_id, start_date, end_date)
                candidate_vehicle_owner_diesel_entries = self.billing.diesel_entries_for_vehicle_owner(entity_id, start_date, end_date)

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
        }

    def create_bill(self, entity_type, entity_id, order_ids=None, entry_ids=None, loading_ids=None, site_ids=None, material_type=None, start_date=None, end_date=None, notes=None):
        entity = self._get_entity(entity_type, entity_id)
        bill_date = datetime.now(UTC).replace(tzinfo=None)

        selected_orders = []
        selected_entries = []
        selected_loadings = []

        if entity_type == "contractor":
            if not order_ids:
                raise ValidationError("Select at least one delivered trip to create a bill.")
            candidates = self.billing.contractor_billable_orders(entity_id, site_ids, material_type, start_date, end_date)
            eligible = {o.id for o in candidates}
            if any(oid not in eligible for oid in order_ids):
                raise ValidationError("Some selected trips are no longer available for billing.")
            selected_orders = [o for o in candidates if o.id in set(order_ids)]
            total_amount = sum(o.billable_amount for o in selected_orders)

        elif entity_type == "petrol_pump":
            if not entry_ids:
                raise ValidationError("Select at least one diesel entry to create a bill.")
            candidates = self.billing.unbilled_diesel_entries(entity_id, start_date, end_date)
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
            candidates = self.billing.unbilled_plant_loadings(entity_id, start_date, end_date)
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
            candidates = self.billing.vehicle_owner_billable_orders(entity_id, start_date, end_date)
            eligible = {o.id for o in candidates}
            if any(oid not in eligible for oid in order_ids):
                raise ValidationError("Some selected orders are no longer available for billing.")
            selected_orders = [o for o in candidates if o.id in set(order_ids)]
            trips_gross = sum(o.remaining_vehicle_payment() for o in selected_orders)
            # Auto-attach all unlinked diesel entries for the owner's vehicles in the period
            diesel_deductions = self.billing.diesel_entries_for_vehicle_owner(entity_id, start_date, end_date)
            diesel_total = sum(d.amount or 0 for d in diesel_deductions)
            total_amount = trips_gross - diesel_total

        else:
            total_amount = float(getattr(entity, "balance", 0.0) or 0.0)
            if total_amount <= 0:
                raise ValidationError("Selected account does not have an outstanding balance to bill.")

        bill = Bill(
            bill_number=self.billing.next_bill_number(bill_date),
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

    def settle_bill(self, bill_id, amount, payment_method=None, reference=None):
        bill = self.get_bill(bill_id)
        settlement_amount = float(amount or 0.0)
        if settlement_amount <= 0:
            raise ValidationError("Settlement amount must be greater than zero.")
        if settlement_amount > bill.outstanding_amount:
            raise ValidationError("Settlement amount cannot exceed the outstanding bill amount.")

        transaction_type = {
            "contractor": "contractor_receipt",
            "plant": "plant_payment",
            "petrol_pump": "petrol_pump_payment",
            "vehicle_owner": "vehicle_owner_payment",
        }.get(bill.entity_type)
        if transaction_type is None:
            raise ValidationError("This bill cannot be settled.")

        transaction_input = TransactionInput(
            type=transaction_type,
            amount=settlement_amount,
            description=f"Settlement for bill {bill.bill_number}",
            payment_method=payment_method,
            reference=reference,
            entity_type=bill.entity_type,
            entity_id=self._bill_entity_id(bill),
            reference_id=bill.id,
            contractor_id=bill.contractor_id,
            plant_id=bill.plant_id,
            petrol_pump_id=bill.petrol_pump_id,
            vehicle_owner_id=bill.vehicle_owner_id,
        )

        try:
            self.transactions.create_transaction(transaction_input, commit=False)
            bill.settled_amount = (bill.settled_amount or 0.0) + settlement_amount
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return bill

    def related_transactions(self, bill):
        return self.transactions.transactions_for_entity(bill.entity_type, self._bill_entity_id(bill))

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
                    f"<td>{_cell(f'{(order.delivered_quantity or order.quantity or 0):.2f} {order.unit.upper()}')}</td>"
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

    def export_bill_pdf(self, bill_id):
        snapshot = self.bill_snapshot(bill_id)
        bill = snapshot["bill"]
        orders_grouped = snapshot["orders_grouped"]
        balance_meta = snapshot["balance_summary"]

        def _cell(value):
            return escape("" if value is None else str(value))

        trip_rows = ""
        for group in orders_grouped:
            trip_rows += (
                f"<tr class='group-row'><td colspan='7'><b>{_cell(group['material'])}</b> "
                f"&mdash; {group['trip_count']} trip{'s' if group['trip_count'] != 1 else ''}</td></tr>"
            )
            for order in group["orders"]:
                trip_rows += (
                    "<tr>"
                    f"<td>{_cell((order.completion_date or order.order_date).strftime('%d-%m-%Y'))}</td>"
                    f"<td>{_cell(order.vehicle.vehicle_number if order.vehicle else '-')}</td>"
                    f"<td>{_cell(order.site.name if order.site else '-')}</td>"
                    f"<td>{_cell(order.receipt_number or '-')}</td>"
                    f"<td class='num'>{_cell(f'{(order.delivered_quantity or order.quantity or 0):.2f} {order.unit.upper()}')}</td>"
                    f"<td class='num'>{_cell(f'{(order.contractor_rate or 0):.2f}')}</td>"
                    f"<td class='num'>{_cell(f'{order.billable_amount:,.2f}')}</td>"
                    "</tr>"
                )
            trip_rows += (
                "<tr class='subtotal-row'>"
                f"<td colspan='4'>{_cell(group['material'])} subtotal</td>"
                f"<td class='num'>{_cell(f'{group['total_quantity']:.2f} {group['unit']}')}</td>"
                "<td></td>"
                f"<td class='num'>{_cell(f'{group['total_amount']:,.2f}')}</td>"
                "</tr>"
            )
        if not trip_rows:
            trip_rows = "<tr><td colspan='7'>No linked trips</td></tr>"

        html = f"""
        <html>
        <head>
        <style>
            @page {{ size: A4; margin: 1.6cm 1.4cm; }}
            body {{ font-family: Helvetica, Arial, sans-serif; font-size: 10pt; color: #111111; }}
            h1 {{ font-size: 16pt; margin: 0; }}
            h2 {{ font-size: 11pt; margin: 14pt 0 5pt 0; }}
            .muted {{ color: #555555; font-size: 9pt; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ border: 0.6pt solid #999999; padding: 4pt 6pt; font-size: 9pt; }}
            th {{ background-color: #e8eef7; text-align: left; }}
            td.num {{ text-align: right; }}
            .meta td {{ border: none; padding: 2pt 4pt; }}
            .meta td.label {{ color: #555555; width: 130pt; }}
            .group-row td {{ background-color: #f0f4fa; }}
            .subtotal-row td {{ background-color: #f7f7f7; font-weight: bold; }}
            .totals td {{ font-size: 10pt; }}
            .totals .grand td {{ background-color: #0b2742; color: #ffffff; font-weight: bold; }}
        </style>
        </head>
        <body>
            <h1>Al Rehman Goods Transport</h1>
            <div class="muted">Dispatch &middot; Accounts &middot; Reports</div>

            <h2>Bill {escape(bill.bill_number)}</h2>
            <table class="meta">
                <tr><td class="label">Billed To</td><td><b>{escape(bill.entity_name)}</b> ({escape(bill.entity_type.replace('_', ' ').title())})</td></tr>
                <tr><td class="label">Bill Date</td><td>{escape(bill.bill_date.strftime('%d %B %Y'))}</td></tr>
                <tr><td class="label">Period</td><td>{escape(bill.start_date.strftime('%d %b %Y') if bill.start_date else 'Start')} &mdash; {escape(bill.end_date.strftime('%d %b %Y') if bill.end_date else bill.bill_date.strftime('%d %b %Y'))}</td></tr>
                <tr><td class="label">Notes</td><td>{escape(bill.notes or '-')}</td></tr>
            </table>

            <h2>Trips</h2>
            <table>
                <thead>
                    <tr><th>Date</th><th>Vehicle</th><th>Site</th><th>Receipt</th><th>Quantity</th><th>Rate</th><th>Amount (Rs.)</th></tr>
                </thead>
                <tbody>{trip_rows}</tbody>
            </table>

            <h2>Summary</h2>
            <table class="totals">
                <tr><td>Total Amount</td><td class="num">Rs. {bill.total_amount:,.2f}</td></tr>
                <tr><td>Settled Amount</td><td class="num">Rs. {(bill.settled_amount or 0):,.2f}</td></tr>
                <tr class="grand"><td>Outstanding</td><td class="num">Rs. {bill.outstanding_amount:,.2f}</td></tr>
                <tr><td>Account Position</td><td class="num">{escape(balance_meta['label'])} Rs. {balance_meta['amount']:,.2f}</td></tr>
            </table>

            <p class="muted">Generated on {datetime.now(UTC).strftime('%d %B %Y %H:%M')} &middot; This is a computer-generated bill.</p>
        </body>
        </html>
        """

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


def generate_bill_number(bill_date=None):
    return BillingService().billing.next_bill_number(bill_date)


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
