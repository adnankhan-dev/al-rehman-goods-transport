from dataclasses import dataclass
from datetime import UTC, date, datetime
from io import StringIO

from ..extensions import db
from ..models import Order, OrderLoading
from ..repositories import LookupRepository, OrderRepository
from ..utils.uploads import save_upload
from .exceptions import ConflictError, NotFoundError, ValidationError
from .order_finance import reverse_order_financials, snapshot_order, sync_order_financials
from .transactions import TransactionService


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


def _safe_float(value, default=0.0):
    if value in (None, ""):
        return float(default)
    return float(value)


def _clean_text(value):
    cleaned = (value or "").strip()
    return cleaned or None


def _file_from_form(form_data, key):
    upload = form_data.get(key)
    if upload is None or not getattr(upload, "filename", ""):
        return None
    return upload


@dataclass
class OrderInput:
    order_date: date | None
    vehicle_id: int
    driver_name: str
    contractor_id: int
    site_id: int
    from_site_id: int | None
    material_id: int
    load_quantity: float
    unit: str
    advance_amount: float
    builty_number: str | None
    receipt_number: str | None
    delivered_quantity: float
    vehicle_rate: float
    contractor_rate: float
    plant_id: int | None
    plant_amount: float
    commission: float
    loading_image: object | None
    delivery_receipt_image: object | None
    remarks: str | None


class OrderService:
    def __init__(self, session=None):
        self.session = session or db.session
        self.orders = OrderRepository(self.session)
        self.lookups = LookupRepository(self.session)

    def list_orders(self):
        return self.orders.list_all()

    def list_orders_filtered(self, filters=None):
        filters = filters or {}
        return self.orders.list_filtered(filters)

    def list_orders_page(self, filters=None, page=1, per_page=50):
        filters = filters or {}
        items, total = self.orders.list_filtered_page(filters, page, per_page)
        from ..utils.pagination import build_pagination

        return build_pagination(items, page, per_page, total)

    def orders_summary(self, filters=None):
        """Totals for the filtered set plus the grand total of all orders."""
        filters = filters or {}
        summary = self.orders.filtered_summary(filters)
        summary["all_orders_count"] = self.orders.total_count()
        return summary

    def orders_pnl(self, filters=None):
        """Profit & loss for the filtered set (revenue, vehicle cost, plant, profit)."""
        return self.orders.filtered_pnl(filters or {})

    def get_order(self, order_id):
        order = self.orders.get(order_id)
        if order is None:
            raise NotFoundError("Order not found.")
        return order

    def order_filter_options(self):
        # Filters include archived sites so historical orders can still be filtered;
        # only the entry form (build_form_choices) hides archived sites.
        return {
            "contractors": self.lookups.list_contractors(),
            "sites": self.lookups.list_sites(include_archived=True),
            "from_sites": self.lookups.list_sites(business_only=True, include_archived=True),
            "materials": self.lookups.list_materials(),
            "vehicle_owners": self.lookups.list_vehicle_owners(),
        }

    def build_form_choices(self, contractor_id=None, site_id=None):
        vehicles = self.lookups.list_vehicles()
        contractors = self.lookups.list_contractors()

        selected_contractor_id = contractor_id if contractor_id not in (None, 0) else None
        if not selected_contractor_id and site_id:
            selected_site = next((site for site in self.lookups.list_sites() if site.id == site_id), None)
            selected_contractor_id = selected_site.contractor_id if selected_site else None

        return {
            "vehicle_choices": [(0, "Select Vehicle")] + [(vehicle.id, f"{vehicle.vehicle_number} - {vehicle.owner_display_name}") for vehicle in vehicles],
            "contractor_choices": [(0, "Select Contractor")] + [(contractor.id, contractor.name) for contractor in contractors],
            "site_choices": [(0, "Select Site")] + [(site.id, site.name) for site in self.lookups.list_sites(selected_contractor_id)],
            "from_site_choices": [(0, "Select From Site")] + [(site.id, site.name) for site in self.lookups.list_sites(business_only=True)],
            "material_choices": [(0, "Select Material")] + [(material.id, material.name) for material in self.lookups.list_materials()],
            "plant_choices": [(0, "Select Plant (Optional)")] + [(plant.id, plant.name) for plant in self.lookups.list_plants()],
        }

    def input_from_form(self, form, form_data):
        return OrderInput(
            order_date=form.order_date.data,
            vehicle_id=form.vehicle_id.data,
            driver_name=(form.driver_name.data or "").strip(),
            contractor_id=form.contractor_id.data,
            site_id=form.site_id.data,
            from_site_id=form.from_site_id.data if form.from_site_id.data not in (None, 0) else None,
            material_id=form.material_id.data,
            load_quantity=_safe_float(form.load_quantity.data),
            unit="cft",  # placeholder; the real unit is taken from the selected material in _write_order
            advance_amount=_safe_float(form.advance_amount.data),
            builty_number=_clean_text(form.builty_number.data),
            receipt_number=_clean_text(form.receipt_number.data),
            delivered_quantity=_safe_float(form.delivered_quantity.data),
            vehicle_rate=_safe_float(form.vehicle_rate.data),
            contractor_rate=_safe_float(form.contractor_rate.data),
            plant_id=form.plant_id.data if form.plant_id.data not in (None, 0) else None,
            plant_amount=_safe_float(form.plant_amount.data),
            commission=_safe_float(form.commission.data),
            loading_image=_file_from_form(form_data, "loading_image"),
            delivery_receipt_image=_file_from_form(form_data, "delivery_receipt_image"),
            remarks=_clean_text(form.remarks.data),
        )

    def create_order(self, order_input: OrderInput):
        order = Order(order_date=utc_now())
        try:
            self._write_order(order, order_input)
            self.orders.add(order)
            sync_order_financials(self.session, order)
            TransactionService(self.session).sync_order_advance_transaction(order)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return order

    def update_order(self, order_id, order_input: OrderInput):
        order = self.get_order(order_id)
        if order.is_billed:
            raise ValidationError("This order is on a contractor bill and cannot be edited. Remove it from the bill first.")
        if order.is_vehicle_owner_billed:
            raise ValidationError("This order is on a vehicle owner bill and cannot be edited. Remove it from the bill first.")
        previous_snapshot = snapshot_order(order)

        try:
            self._write_order(order, order_input)
            sync_order_financials(self.session, order, previous_snapshot)
            TransactionService(self.session).sync_order_advance_transaction(order)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return order

    def delete_order(self, order_id):
        order = self.get_order(order_id)
        if order.is_billed or order.is_vehicle_owner_billed:
            raise ValidationError("This order is already part of a bill and cannot be deleted from the order register.")

        try:
            reverse_order_financials(self.session, order)
            transaction_service = TransactionService(self.session)
            existing_advance = transaction_service.transactions.find_system_order_advance(order.id)
            if existing_advance:
                transaction_service.transactions.delete(existing_advance)
            self.orders.delete(order)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def _write_order(self, order, order_input: OrderInput):
        self._validate_order_input(order_input, exclude_order_id=order.id)
        material = self.lookups.get_material(order_input.material_id)
        if material is None:
            raise ValidationError("Selected material was not found.")

        order.order_date = self._resolved_order_datetime(order_input.order_date, order.order_date)
        order.vehicle_id = order_input.vehicle_id
        order.driver_name = order_input.driver_name
        order.contractor_id = order_input.contractor_id
        order.site_id = order_input.site_id
        order.from_site_id = order_input.from_site_id
        order.material_id = material.id
        order.material_type = material.name
        order.quantity = order_input.load_quantity
        # Unit is defined on the material master, never edited per order.
        order.unit = getattr(material, "unit", None) or "cft"
        order.advance_amount = order_input.advance_amount
        order.builty_number = order_input.builty_number
        order.receipt_number = order_input.receipt_number
        order.delivered_quantity = order_input.delivered_quantity
        order.vehicle_rate = order_input.vehicle_rate
        order.contractor_rate = order_input.contractor_rate
        order.plant_id = order_input.plant_id
        order.plant_amount = order_input.plant_amount if order_input.plant_id else 0.0
        order.commission = order_input.commission
        order.remarks = order_input.remarks
        order.status = "Completed"
        order.completion_date = order.completion_date or order.order_date or utc_now()

        if order_input.delivery_receipt_image is not None:
            order.delivery_receipt_image = save_upload(order_input.delivery_receipt_image, "deliveries")

        self._sync_loading(order, order_input)

    def _sync_loading(self, order, order_input: OrderInput):
        loading = order.primary_loading or OrderLoading(order=order)
        if loading not in order.loadings:
            order.loadings.append(loading)

        loading.load_quantity = order_input.load_quantity
        loading.plant_id = order_input.plant_id
        loading.plant_amount = order_input.plant_amount
        if order_input.loading_image is not None:
            loading.loading_image = save_upload(order_input.loading_image, "loadings")

    def _validate_order_input(self, order_input: OrderInput, exclude_order_id=None):
        duplicate_order = self.orders.get_by_builty_number(order_input.builty_number, exclude_id=exclude_order_id)
        if duplicate_order is not None:
            raise ConflictError("Builty number already exists.")

        destination_site = self.lookups.get_site(order_input.site_id)
        if destination_site is None:
            raise ValidationError("Selected site was not found.")
        if destination_site.contractor_id != order_input.contractor_id:
            raise ValidationError("Selected site does not belong to the selected contractor.")

        if order_input.from_site_id:
            from_site = self.lookups.get_site(order_input.from_site_id)
            if from_site is None:
                raise ValidationError("Selected from site was not found.")
            if not from_site.is_business_site:
                raise ValidationError("Selected from site must be associated with your business.")

        if order_input.plant_id and self.lookups.get_plant(order_input.plant_id) is None:
            raise ValidationError("Selected plant was not found.")
        if not order_input.plant_id and _safe_float(order_input.plant_amount) > 0:
            raise ValidationError("Select a plant before entering a plant amount.")

        if order_input.advance_amount < 0 or order_input.plant_amount < 0:
            raise ValidationError("Advance and plant amounts cannot be negative.")

    def _resolved_order_datetime(self, order_date_value: date | None, existing_value=None):
        if order_date_value is None:
            return existing_value or utc_now()

        base_time = (existing_value or utc_now()).time().replace(tzinfo=None)
        return datetime.combine(order_date_value, base_time)

    def export_orders_print_html(self, orders, filter_state):
        order_rows = "".join(
            (
                "<tr>"
                f"<td>{(order.order_date.strftime('%Y-%m-%d'))}</td>"
                f"<td>{(order.vehicle.vehicle_number if order.vehicle else '-')}"
                f"<br><span style='color:#64748b;font-size:0.85em;'>{(order.vehicle.owner_display_name if order.vehicle else '-')}</span></td>"
                f"<td>{order.contractor.name if order.contractor else '-'}</td>"
                f"<td>{order.from_site.name if order.from_site else '-'}</td>"
                f"<td>{order.site.name if order.site else '-'}</td>"
                f"<td>{order.material_name}</td>"
                f"<td>{order.receipt_number or '-'}</td>"
                f"<td>{(order.delivered_quantity or order.quantity or 0):.2f} {(order.unit or 'cft').upper()}</td>"
                f"<td>{'Billed' if order.is_billed else 'Ready for Bill'}</td>"
                "</tr>"
            )
            for order in orders
        ) or "<tr><td colspan='9'>No orders found.</td></tr>"

        active_filters = [
            f"Contractor: {filter_state.get('contractor_name')}" if filter_state.get("contractor_name") else None,
            f"To Site: {filter_state.get('site_name')}" if filter_state.get("site_name") else None,
            f"From Site: {filter_state.get('from_site_name')}" if filter_state.get("from_site_name") else None,
            f"Material: {filter_state.get('material_name')}" if filter_state.get("material_name") else None,
            f"Vehicle Owner: {filter_state.get('vehicle_owner_name')}" if filter_state.get("vehicle_owner_name") else None,
            f"Billing: {filter_state.get('billing_status')}" if filter_state.get("billing_status") else None,
            f"From: {filter_state.get('date_from')}" if filter_state.get("date_from") else None,
            f"To: {filter_state.get('date_to')}" if filter_state.get("date_to") else None,
            f"Search: {filter_state.get('search')}" if filter_state.get("search") else None,
        ]
        active_filters = [item for item in active_filters if item]

        html = StringIO()
        html.write(
            f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="utf-8">
                <title>Orders Print View</title>
                <style>
                    body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f8fafc; margin: 0; padding: 24px; color: #0f172a; }}
                    .shell {{ max-width: 1200px; margin: 0 auto; background: #fff; padding: 32px; border-radius: 20px; box-shadow: 0 20px 50px rgba(15,23,42,0.12); }}
                    .toolbar {{ display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }}
                    .letterhead {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; border-bottom: 2px solid #dbe4f0; padding-bottom: 16px; margin-bottom: 20px; }}
                    .letterhead-name {{ font-size: 1.5rem; font-weight: 800; color: #0b2742; letter-spacing: -0.01em; }}
                    .letterhead-line {{ color: #64748b; font-size: 0.82rem; line-height: 1.6; }}
                    .letterhead-meta {{ text-align: right; }}
                    .button {{ appearance: none; border: none; border-radius: 999px; background: #0f172a; color: #fff; padding: 10px 16px; cursor: pointer; font: inherit; text-decoration: none; }}
                    .button-light {{ background: #dbe4f0; color: #0f172a; }}
                    .filters {{ margin: 18px 0; color: #475569; }}
                    .metrics {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 20px; }}
                    .metric {{ border: 1px solid #dbe4f0; border-radius: 16px; padding: 12px 16px; background: #f8fbff; min-width: 180px; }}
                    table {{ width: 100%; border-collapse: collapse; }}
                    th, td {{ border: 1px solid #dbe4f0; padding: 10px 12px; text-align: left; }}
                    th {{ background: #e8f0fb; }}
                    @media print {{
                        body {{ background: #fff; padding: 0; }}
                        .shell {{ box-shadow: none; border-radius: 0; max-width: none; padding: 0; }}
                        .toolbar {{ display: none; }}
                    }}
                </style>
            </head>
            <body>
                <div class="shell">
                    <div class="toolbar">
                        <div style="text-transform: uppercase; letter-spacing: 0.14em; color: #64748b; font-size: 0.78rem;">Orders Register</div>
                        <button type="button" class="button" onclick="window.print()">Print Orders</button>
                    </div>
                    <header class="letterhead">
                        <div class="letterhead-brand">
                            <div class="letterhead-name">Al Rehman Goods Transport</div>
                            <div class="letterhead-line">Bahtr Mor Wah Cantt</div>
                            <div class="letterhead-line">Contact No. Ahsan Niazi 0307-2342827</div>
                            <div class="letterhead-line">Inam Khan - 0301-5749086</div>
                        </div>
                        <div class="letterhead-meta">
                            <div style="text-transform: uppercase; letter-spacing: 0.14em; color: #d97706; font-size: 0.74rem; font-weight: 700;">Orders Register</div>
                            <div style="color:#475569; font-size:0.82rem; margin-top:4px;">Printed: {datetime.now().strftime('%d %b %Y')}</div>
                            <div style="color:#475569; font-size:0.82rem;">Total Orders: {len(orders)}</div>
                        </div>
                    </header>
                    <div class="metrics">
                        <div class="metric"><strong>{len(orders)}</strong><div>Total Orders</div></div>
                        <div class="metric"><strong>{sum(1 for order in orders if order.is_billed)}</strong><div>Billed Orders</div></div>
                        <div class="metric"><strong>{sum((order.delivered_quantity or order.quantity or 0) for order in orders):.2f}</strong><div>Total Quantity</div></div>
                    </div>
                    <div class="filters"><strong>Active Filters:</strong> {' | '.join(active_filters) if active_filters else 'None'}</div>
                    <table>
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>Vehicle / Owner</th>
                                <th>Contractor</th>
                                <th>From Site</th>
                                <th>To Site</th>
                                <th>Material</th>
                                <th>Receipt</th>
                                <th>Delivered</th>
                                <th>Billing</th>
                            </tr>
                        </thead>
                        <tbody>{order_rows}</tbody>
                    </table>
                </div>
            </body>
            </html>
            """
        )
        return html.getvalue()
