from dataclasses import dataclass
from datetime import UTC, date, datetime
from io import StringIO

from html import escape

from ..extensions import db
from ..models import Order, OrderLoading
from ..repositories import BillingRepository, LookupRepository, OrderRepository
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

    def export_orders_print_html(self, orders, filter_state, include_profit=False):
        """Detailed transport statement: orders grouped by material (like a bill),
        showing contractor and vehicle rates per row, with end-of-statement
        payment summaries per contractor, per vehicle owner, and per plant. When
        include_profit is set, a net profit summary (revenue minus vehicle and
        plant costs) is appended."""
        from .billing import group_orders_by_site_and_material

        def money(value):
            return f"Rs. {float(value or 0):,.2f}"

        def rate_str(value, unit):
            return f"Rs. {float(value):,.2f}/{unit}" if value else "&mdash;"

        def parse_date(value):
            if not value:
                return None
            try:
                return datetime.strptime(value, "%Y-%m-%d").date()
            except (TypeError, ValueError):
                return None

        diesel_from = parse_date(filter_state.get("date_from"))
        diesel_to = parse_date(filter_state.get("date_to"))

        # ---- nested grouping: To site -> From site -> material (mirrors the bill) ----
        site_tree = group_orders_by_site_and_material(orders)

        def render_material_group(mg):
            unit = mg["unit"]
            rows = ""
            for order in mg["orders"]:
                qty = float(order.delivered_quantity or order.quantity or 0)
                owner = order.vehicle.owner_display_name if order.vehicle else "-"
                vehicle_no = order.vehicle.vehicle_number if order.vehicle else "-"
                rows += (
                    "<tr>"
                    f"<td>{(order.completion_date or order.order_date).strftime('%Y-%m-%d')}</td>"
                    f"<td><strong>{escape(vehicle_no)}</strong>"
                    f"<br><span class='sub'>{escape(owner)}</span>"
                    f"<br><span class='rate'>@ {rate_str(order.vehicle_rate, unit)}</span></td>"
                    f"<td>{escape(order.contractor.name if order.contractor else '-')}"
                    f"<br><span class='rate'>@ {rate_str(order.contractor_rate, unit)}</span></td>"
                    f"<td>{escape(order.receipt_number or '-')}</td>"
                    f"<td class='num'>{qty:.2f} {unit}</td>"
                    f"<td class='num'>{money(order.billable_amount)}</td>"
                    f"<td class='num'>{money(order.total_vehicle_amount())}</td>"
                    "</tr>"
                )
            breakdown = ""
            if mg["contractor_rate"]:
                breakdown = (
                    "<tr class='breakdown'><td colspan='7'>"
                    f"@ Rs. {mg['contractor_rate']:,.2f}/{unit} &times; {mg['total_quantity']:.2f} {unit} "
                    f"= {money(mg['total_amount'])} (contractor)"
                    "</td></tr>"
                )
            trips = int(mg["trip_count"])
            return f"""
                <div class="group">
                    <div class="group-head"><strong>{escape(mg['material'])}</strong>
                        <span class="chip">{trips} trip{'s' if trips != 1 else ''}</span></div>
                    <table>
                        <thead><tr>
                            <th>Date</th><th>Vehicle / Owner</th><th>Contractor</th>
                            <th>Receipt</th><th class="num">Delivered</th>
                            <th class="num">Contractor Amt</th><th class="num">Vehicle Amt</th>
                        </tr></thead>
                        <tbody>
                            {rows}
                            <tr class="subtotal"><td colspan="4"><strong>{escape(mg['material'])} Subtotal</strong></td>
                                <td class="num"><strong>{mg['total_quantity']:.2f} {unit}</strong></td>
                                <td class="num"><strong>{money(mg['total_amount'])}</strong></td>
                                <td class="num"><strong>{money(mg['total_vehicle_amount'])}</strong></td></tr>
                            {breakdown}
                        </tbody>
                    </table>
                </div>
            """

        def subtotal_line(label, totals, css):
            return (
                f"<div class='{css}'>{escape(label)} &mdash; "
                f"{totals['total_quantity']:.2f} qty &middot; Contractor {money(totals['total_amount'])} "
                f"&middot; Vehicle {money(totals['total_vehicle_amount'])}</div>"
            )

        groups_html = ""
        for to_group in site_tree:
            from_html = ""
            for from_group in to_group["from_groups"]:
                materials_html = "".join(render_material_group(mg) for mg in from_group["material_groups"])
                from_html += f"""
                    <div class="from-group">
                        <div class="from-head">From: {escape(from_group['from_site'])}</div>
                        {materials_html}
                        {subtotal_line('From ' + from_group['from_site'] + ' Subtotal', from_group['subtotal'], 'from-subtotal')}
                    </div>
                """
            groups_html += f"""
                <div class="site-group">
                    <div class="site-head">To: {escape(to_group['to_site'])}
                        <span class="chip">{int(to_group['subtotal']['trip_count'])} trips</span></div>
                    {from_html}
                    {subtotal_line('To ' + to_group['to_site'] + ' Subtotal', to_group['subtotal'], 'to-subtotal')}
                </div>
            """

        # ---- per-contractor payable summary ----
        contractor_summary = {}
        for order in orders:
            cid = order.contractor_id
            entry = contractor_summary.setdefault(cid, {
                "name": order.contractor.name if order.contractor else "Unassigned",
                "trips": 0, "qty": 0.0, "amount": 0.0,
            })
            entry["trips"] += 1
            entry["qty"] += float(order.delivered_quantity or order.quantity or 0)
            entry["amount"] += float(order.billable_amount)
        contractor_rows = "".join(
            f"<tr><td>{escape(c['name'])}</td><td class='num'>{c['trips']}</td>"
            f"<td class='num'>{c['qty']:.2f}</td><td class='num'>{money(c['amount'])}</td></tr>"
            for c in sorted(contractor_summary.values(), key=lambda x: x["name"])
        ) or "<tr><td colspan='4'>No contractors.</td></tr>"
        contractor_total = sum(c["amount"] for c in contractor_summary.values())

        # ---- per-vehicle-owner net payable summary (advances + diesel deducted) ----
        billing_repo = BillingRepository(self.session)
        owner_summary = {}
        for order in orders:
            owner = order.vehicle.owner if order.vehicle else None
            owner_id = owner.id if owner else None
            key = owner_id if owner_id is not None else f"name:{order.vehicle.owner_display_name if order.vehicle else 'Unassigned'}"
            entry = owner_summary.setdefault(key, {
                "id": owner_id,
                "name": owner.name if owner else (order.vehicle.owner_display_name if order.vehicle else "Unassigned"),
                "gross": 0.0, "advance": 0.0, "order_diesel": 0.0,
            })
            entry["gross"] += float(order.total_vehicle_amount())
            entry["advance"] += float(order.total_advance_amount())
            entry["order_diesel"] += float(order.total_diesel_amount())

        owner_rows = ""
        owner_total_net = 0.0
        for entry in sorted(owner_summary.values(), key=lambda x: x["name"]):
            standalone_diesel = 0.0
            if entry["id"] is not None:
                standalone_diesel = sum(
                    float(e.amount or 0)
                    for e in billing_repo.diesel_entries_for_vehicle_owner(entry["id"], diesel_from, diesel_to)
                )
            net = entry["gross"] - entry["advance"] - entry["order_diesel"] - standalone_diesel
            owner_total_net += net
            owner_rows += (
                f"<tr><td>{escape(entry['name'])}</td>"
                f"<td class='num'>{money(entry['gross'])}</td>"
                f"<td class='num'>&minus; {money(entry['advance'])}</td>"
                f"<td class='num'>&minus; {money(entry['order_diesel'])}</td>"
                f"<td class='num'>&minus; {money(standalone_diesel)}</td>"
                f"<td class='num'><strong>{money(net)}</strong></td></tr>"
            )
        owner_rows = owner_rows or "<tr><td colspan='6'>No vehicle owners.</td></tr>"

        # ---- per-plant payable summary (loading cost we pay the plant) ----
        plant_summary = {}
        for order in orders:
            if not order.plant_id and not (order.plant_amount or 0):
                continue
            key = order.plant_id if order.plant_id is not None else f"name:{order.plant.name if order.plant else 'Unassigned'}"
            entry = plant_summary.setdefault(key, {
                "name": order.plant.name if order.plant else "Unassigned",
                "trips": 0, "qty": 0.0, "amount": 0.0,
            })
            entry["trips"] += 1
            entry["qty"] += float(order.delivered_quantity or order.quantity or 0)
            entry["amount"] += float(order.plant_amount or 0)
        plant_rows = "".join(
            f"<tr><td>{escape(p['name'])}</td><td class='num'>{p['trips']}</td>"
            f"<td class='num'>{p['qty']:.2f}</td><td class='num'>{money(p['amount'])}</td></tr>"
            for p in sorted(plant_summary.values(), key=lambda x: x["name"])
        ) or "<tr><td colspan='4'>No plant charges.</td></tr>"
        plant_total = sum(p["amount"] for p in plant_summary.values())

        # ---- net profit (revenue minus vehicle and plant cost), gated by caller ----
        total_vehicle_cost = sum(float(order.total_vehicle_amount()) for order in orders)
        net_profit = contractor_total - total_vehicle_cost - plant_total
        profit_html = ""
        if include_profit:
            profit_html = f"""
                        <h2 class="section">Net Profit Summary</h2>
                        <table>
                            <tbody>
                                <tr><td>Revenue (Contractor receivable)</td><td class="num">{money(contractor_total)}</td></tr>
                                <tr><td>Vehicle Cost (owner payable)</td><td class="num">&minus; {money(total_vehicle_cost)}</td></tr>
                                <tr><td>Plant Cost (plant payable)</td><td class="num">&minus; {money(plant_total)}</td></tr>
                                <tr class="grand"><td>Net {'Profit' if net_profit >= 0 else 'Loss'}</td>
                                    <td class="num">{'&minus; ' if net_profit < 0 else ''}{money(abs(net_profit))}</td></tr>
                            </tbody>
                        </table>
            """

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
        total_qty = sum((order.delivered_quantity or order.quantity or 0) for order in orders)
        groups_html = groups_html or "<p style='color:#64748b;'>No orders found.</p>"

        html = StringIO()
        html.write(
            f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="utf-8">
                <title>Transport Statement</title>
                <style>
                    body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f8fafc; margin: 0; padding: 24px; color: #0f172a; }}
                    .shell {{ max-width: 1200px; margin: 0 auto; background: #fff; padding: 32px; border-radius: 20px; box-shadow: 0 20px 50px rgba(15,23,42,0.12); }}
                    .toolbar {{ display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }}
                    .letterhead {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; border-bottom: 2px solid #dbe4f0; padding-bottom: 16px; margin-bottom: 20px; }}
                    .letterhead-name {{ font-size: 1.5rem; font-weight: 800; color: #0b2742; letter-spacing: -0.01em; }}
                    .letterhead-line {{ color: #64748b; font-size: 0.82rem; line-height: 1.6; }}
                    .letterhead-meta {{ text-align: right; }}
                    .button {{ appearance: none; border: none; border-radius: 999px; background: #0f172a; color: #fff; padding: 10px 16px; cursor: pointer; font: inherit; text-decoration: none; }}
                    .filters {{ margin: 14px 0; color: #475569; font-size: 0.85rem; }}
                    .metrics {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 20px; }}
                    .metric {{ border: 1px solid #dbe4f0; border-radius: 16px; padding: 12px 16px; background: #f8fbff; min-width: 170px; }}
                    .metric strong {{ font-size: 1.15rem; }}
                    table {{ width: 100%; border-collapse: collapse; margin-top: 6px; }}
                    th, td {{ border: 1px solid #dbe4f0; padding: 8px 10px; text-align: left; font-size: 0.86rem; vertical-align: top; }}
                    th {{ background: #e8f0fb; }}
                    td.num, th.num {{ text-align: right; white-space: nowrap; }}
                    .sub {{ color: #64748b; font-size: 0.82em; }}
                    .rate {{ color: #0b6b3a; font-size: 0.8em; font-weight: 600; }}
                    .group {{ margin-bottom: 14px; }}
                    .group-head {{ display: flex; align-items: center; gap: 10px; font-size: 0.95rem; margin-top: 8px; }}
                    .chip {{ background: #e2e8f0; border-radius: 999px; padding: 2px 10px; font-size: 0.74rem; color: #334155; }}
                    .site-group {{ margin-bottom: 26px; border: 1px solid #cbd9ec; border-radius: 12px; padding: 12px 14px; background: #fcfdff; }}
                    .site-head {{ font-size: 1.12rem; font-weight: 800; color: #0b2742; display: flex; align-items: center; gap: 10px; }}
                    .from-group {{ margin: 12px 0 12px 6px; padding-left: 12px; border-left: 3px solid #dbe4f0; }}
                    .from-head {{ font-size: 0.98rem; font-weight: 700; color: #1d4ed8; margin-bottom: 4px; }}
                    .from-subtotal {{ text-align: right; font-size: 0.84rem; font-weight: 600; color: #334155; background: #eef2f8; border-radius: 8px; padding: 5px 10px; margin-top: 4px; }}
                    .to-subtotal {{ text-align: right; font-size: 0.9rem; font-weight: 800; color: #0b2742; background: #dbe7f5; border-radius: 8px; padding: 7px 10px; margin-top: 6px; }}
                    tr.subtotal td {{ background: #f1f5f9; }}
                    tr.breakdown td {{ background: #fff; color: #475569; text-align: right; font-size: 0.8rem; border-top: none; }}
                    .summary {{ margin-top: 28px; }}
                    .summary h3 {{ font-size: 1.05rem; margin: 18px 0 6px; color: #0b2742; }}
                    tr.grand td {{ background: #0b2742; color: #fff; font-weight: 700; }}
                    h2.section {{ font-size: 1.1rem; color: #0b2742; border-bottom: 2px solid #dbe4f0; padding-bottom: 6px; margin-top: 26px; }}
                    @media print {{
                        body {{ background: #fff; padding: 0; }}
                        .shell {{ box-shadow: none; border-radius: 0; max-width: none; padding: 0; }}
                        .toolbar {{ display: none; }}
                        /* Only keep table rows atomic. Avoiding breaks inside whole
                           groups forces a tall first group onto page 2, leaving page 1
                           blank right after the header. */
                        tr {{ break-inside: avoid; }}
                        thead {{ display: table-header-group; }}
                        .group-head {{ break-after: avoid; }}
                    }}
                </style>
            </head>
            <body>
                <div class="shell">
                    <div class="toolbar">
                        <div style="text-transform: uppercase; letter-spacing: 0.14em; color: #64748b; font-size: 0.78rem;">Transport Statement</div>
                        <button type="button" class="button" onclick="window.print()">Print / Save PDF</button>
                    </div>
                    <header class="letterhead">
                        <div class="letterhead-brand">
                            <div class="letterhead-name">Al Rehman Goods Transport</div>
                            <div class="letterhead-line">Bahtr Mor Wah Cantt</div>
                            <div class="letterhead-line">Contact No. Ahsan Niazi 0307-2342827</div>
                            <div class="letterhead-line">Inam Khan - 0301-5749086</div>
                        </div>
                        <div class="letterhead-meta">
                            <div style="text-transform: uppercase; letter-spacing: 0.14em; color: #d97706; font-size: 0.74rem; font-weight: 700;">Detailed Statement</div>
                            <div style="color:#475569; font-size:0.82rem; margin-top:4px;">Printed: {datetime.now().strftime('%d %b %Y')}</div>
                            <div style="color:#475569; font-size:0.82rem;">Total Trips: {len(orders)}</div>
                        </div>
                    </header>
                    <div class="metrics">
                        <div class="metric"><strong>{len(orders)}</strong><div>Total Trips</div></div>
                        <div class="metric"><strong>{total_qty:.2f}</strong><div>Total Quantity</div></div>
                        <div class="metric"><strong>{money(contractor_total)}</strong><div>Contractor Payable</div></div>
                        <div class="metric"><strong>{money(owner_total_net)}</strong><div>Vehicle Owner Payable</div></div>
                        <div class="metric"><strong>{money(plant_total)}</strong><div>Plant Payable</div></div>
                    </div>
                    <div class="filters"><strong>Active Filters:</strong> {' | '.join(active_filters) if active_filters else 'None'}</div>

                    <h2 class="section">Trips by Site</h2>
                    {groups_html}

                    <div class="summary">
                        <h2 class="section">Payment Summary &mdash; Contractors (Receivable)</h2>
                        <table>
                            <thead><tr><th>Contractor</th><th class="num">Trips</th><th class="num">Quantity</th><th class="num">Amount</th></tr></thead>
                            <tbody>
                                {contractor_rows}
                                <tr class="grand"><td>Grand Total</td><td class="num"></td><td class="num"></td><td class="num">{money(contractor_total)}</td></tr>
                            </tbody>
                        </table>

                        <h2 class="section">Payment Summary &mdash; Vehicle Owners (Payable)</h2>
                        <table>
                            <thead><tr>
                                <th>Vehicle Owner</th><th class="num">Gross Vehicle</th><th class="num">Advances</th>
                                <th class="num">Order Diesel</th><th class="num">Owner Diesel</th><th class="num">Net Payable</th>
                            </tr></thead>
                            <tbody>
                                {owner_rows}
                                <tr class="grand"><td>Grand Total</td><td class="num"></td><td class="num"></td><td class="num"></td><td class="num"></td><td class="num">{money(owner_total_net)}</td></tr>
                            </tbody>
                        </table>

                        <h2 class="section">Payment Summary &mdash; Plants (Payable)</h2>
                        <table>
                            <thead><tr><th>Plant</th><th class="num">Trips</th><th class="num">Quantity</th><th class="num">Plant Cost</th></tr></thead>
                            <tbody>
                                {plant_rows}
                                <tr class="grand"><td>Grand Total</td><td class="num"></td><td class="num"></td><td class="num">{money(plant_total)}</td></tr>
                            </tbody>
                        </table>
                        {profit_html}
                    </div>
                </div>
            </body>
            </html>
            """
        )
        return html.getvalue()
