from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..core.auth import require_permission
from ..core.flash import flash
from ..core.templating import render_template
from ..forms import EditOrderForm, OrderForm
from ..services import ConflictError, NotFoundError, OrderService, ValidationError
from ..services.audit import record_audit
from ..utils.pagination import parse_page


router = APIRouter()


def _parse_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


def _parse_int_list(source, *keys):
    """Collect a list of unique ints from one or more query keys (checkbox groups)."""
    values = []
    for key in keys:
        raw = source.getlist(key) if hasattr(source, "getlist") else [source.get(key)]
        for item in raw:
            parsed = _parse_int(item)
            if parsed is not None and parsed not in values:
                values.append(parsed)
    return values


def _order_filter_state(source):
    return {
        "contractor_id": _parse_int(source.get("contractor_id")),
        # Multi-select checkbox groups (also accept the legacy singular keys).
        "site_ids": _parse_int_list(source, "site_ids", "site_id"),
        "from_site_ids": _parse_int_list(source, "from_site_ids", "from_site_id"),
        "material_ids": _parse_int_list(source, "material_ids", "material_id"),
        "vehicle_owner_id": _parse_int(source.get("vehicle_owner_id")),
        "billing_status": (source.get("billing_status") or "").strip(),
        "date_from": _parse_date(source.get("date_from")),
        "date_to": _parse_date(source.get("date_to")),
        # Admin audit filters: who entered the order and on what entry date.
        "entered_by_id": _parse_int(source.get("entered_by_id")),
        "entry_date": _parse_date(source.get("entry_date")),
        "search": (source.get("search") or "").strip(),
    }


def _names_for_ids(options_list, ids):
    by_id = {item.id: item.name for item in options_list}
    return [by_id[i] for i in ids if i in by_id]


def _describe_order_filters(filter_state, options):
    contractor = next((item for item in options["contractors"] if item.id == filter_state["contractor_id"]), None)
    owner = next((item for item in options["vehicle_owners"] if item.id == filter_state["vehicle_owner_id"]), None)
    entered_by = next((u for u in options.get("users", []) if u.id == filter_state.get("entered_by_id")), None)
    site_names = _names_for_ids(options["sites"], filter_state["site_ids"])
    from_site_names = _names_for_ids(options["from_sites"], filter_state["from_site_ids"])
    material_names = _names_for_ids(options["materials"], filter_state["material_ids"])
    return {
        **filter_state,
        "contractor_name": contractor.name if contractor else None,
        "site_name": ", ".join(site_names) if site_names else None,
        "from_site_name": ", ".join(from_site_names) if from_site_names else None,
        "material_name": ", ".join(material_names) if material_names else None,
        "vehicle_owner_name": owner.name if owner else None,
        "entered_by_name": entered_by.username if entered_by else None,
        "entry_date": filter_state["entry_date"].strftime("%Y-%m-%d") if filter_state.get("entry_date") else None,
        "date_from": filter_state["date_from"].strftime("%Y-%m-%d") if filter_state["date_from"] else None,
        "date_to": filter_state["date_to"].strftime("%Y-%m-%d") if filter_state["date_to"] else None,
    }


def _populate_order_choices(form, service: OrderService):
    choices = service.build_form_choices(contractor_id=form.contractor_id.data, site_id=form.site_id.data)
    form.vehicle_id.choices = choices["vehicle_choices"]
    form.contractor_id.choices = choices["contractor_choices"]
    form.site_id.choices = choices["site_choices"]
    form.from_site_id.choices = choices["from_site_choices"]
    form.material_id.choices = choices["material_choices"]
    form.plant_id.choices = choices["plant_choices"]


def _material_units(service: OrderService):
    # Map of material id -> measuring unit (defined on the material master) so the
    # order form can show/lock the unit automatically when a material is chosen.
    return {material.id: (material.unit or "cft") for material in service.lookups.list_materials()}


@router.get("/orders", name="orders.orders")
async def orders(request: Request, current_user=Depends(require_permission("orders.view"))):
    service = OrderService()
    filter_state = _order_filter_state(request.query_params)
    page = parse_page(request.query_params.get("page"))
    pagination = service.list_orders_page(filter_state, page=page)
    summary = service.orders_summary(filter_state)
    filter_options = service.order_filter_options()
    filters_active = any(
        filter_state.get(key) for key in (
            "contractor_id", "site_ids", "from_site_ids", "material_ids",
            "vehicle_owner_id", "billing_status", "date_from", "date_to",
            "entered_by_id", "entry_date", "search",
        )
    )
    # The "Entered by" / "Entry date" audit filters are admin-only.
    is_admin = bool(getattr(current_user, "can", lambda _c: False)("settings.manage"))

    # Profit & Loss summary follows the same access as the Reports P&L: anyone who
    # can view Reports can see it; data-entry users (no reports.view) cannot.
    can_view_pnl = bool(getattr(current_user, "can", lambda _c: False)("reports.view"))
    pnl = service.orders_pnl(filter_state) if can_view_pnl else None
    pnl_filter_label = _pnl_filter_label(filter_state, filter_options) if can_view_pnl else ""

    return render_template(
        request,
        "orders/list.html",
        orders=pagination["items"],
        pagination=pagination,
        summary=summary,
        filters_active=filters_active,
        can_view_pnl=can_view_pnl,
        pnl=pnl,
        pnl_filter_label=pnl_filter_label,
        filter_state=filter_state,
        filter_options=filter_options,
        is_admin=is_admin,
        body_class="orders-list-page",
    )


def _pnl_filter_label(filter_state, options):
    desc = _describe_order_filters(filter_state, options)
    parts = []
    if desc.get("contractor_name"):
        parts.append(f"Contractor: {desc['contractor_name']}")
    if desc.get("from_site_name"):
        parts.append(f"From Sites: {desc['from_site_name']}")
    if desc.get("site_name"):
        parts.append(f"To Sites: {desc['site_name']}")
    if desc.get("material_name"):
        parts.append(f"Materials: {desc['material_name']}")
    if desc.get("vehicle_owner_name"):
        parts.append(f"Vehicle Owner: {desc['vehicle_owner_name']}")
    if filter_state.get("billing_status"):
        parts.append(f"Billing: {filter_state['billing_status'].replace('_', ' ').title()}")
    if desc.get("date_from"):
        parts.append(f"From: {desc['date_from']}")
    if desc.get("date_to"):
        parts.append(f"To: {desc['date_to']}")
    if filter_state.get("search"):
        parts.append(f"Search: {filter_state['search']}")
    return " · ".join(parts) if parts else "All orders"


@router.get("/orders/print", name="orders.print_orders")
async def print_orders(request: Request, current_user=Depends(require_permission("orders.view"))):
    service = OrderService()
    filter_state = _order_filter_state(request.query_params)
    orders_list = service.list_orders_filtered(filter_state)
    filter_options = service.order_filter_options()
    # Net profit is gated like the orders P&L card: only reports.view users see it.
    can_view_pnl = bool(getattr(current_user, "can", lambda _c: False)("reports.view"))
    return HTMLResponse(content=service.export_orders_print_html(
        orders_list, _describe_order_filters(filter_state, filter_options), include_profit=can_view_pnl
    ))


@router.api_route("/orders/create", methods=["GET", "POST"], name="orders.create_order")
async def create_order(request: Request, current_user=Depends(require_permission("orders.create"))):
    service = OrderService()
    form_data = await request.form() if request.method == "POST" else None
    form = OrderForm(form_data)
    if request.method == "GET":
        form.order_date.data = datetime.now().date()
    _populate_order_choices(form, service)

    if request.method == "POST" and form.validate():
        try:
            order = service.create_order(service.input_from_form(form, form_data), created_by_id=getattr(current_user, "id", None))
            record_audit(current_user, "create", "order", order.id, f"Order #{order.id} — {order.material_name}, {order.delivered_quantity or order.quantity} {order.unit}")
            flash(request, "Order added successfully!", "success")
            return RedirectResponse(url=str(request.url_for("orders.orders")), status_code=303)
        except (ConflictError, ValidationError, ValueError) as exc:
            flash(request, str(exc), "warning")

    return render_template(
        request,
        "orders/create.html",
        form=form,
        material_units=_material_units(service),
        status_code=400 if request.method == "POST" and form.errors else 200,
    )


@router.get("/orders/{id}", name="orders.view_order")
async def view_order(id: int, request: Request, _current_user=Depends(require_permission("orders.view"))):
    service = OrderService()
    try:
        order = service.get_order(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return render_template(request, "orders/view.html", order=order)


@router.api_route("/orders/{id}/edit", methods=["GET", "POST"], name="orders.edit_order")
async def edit_order(id: int, request: Request, current_user=Depends(require_permission("orders.edit"))):
    service = OrderService()
    try:
        order = service.get_order(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    form_data = await request.form() if request.method == "POST" else None
    form = EditOrderForm(form_data, obj=order)
    if request.method == "GET":
        form.order_date.data = order.order_date.date() if order.order_date else None
        form.material_id.data = order.material_id
        form.from_site_id.data = order.from_site_id or 0
        form.plant_id.data = order.plant_id or 0
        form.load_quantity.data = order.primary_loading.load_quantity if order.primary_loading else order.quantity
    _populate_order_choices(form, service)

    if request.method == "POST" and form.validate():
        try:
            service.update_order(id, service.input_from_form(form, form_data))
            record_audit(current_user, "update", "order", id, f"Order #{id} updated")
            flash(request, "Order updated successfully!", "success")
            return RedirectResponse(url=str(request.url_for("orders.orders")), status_code=303)
        except (ConflictError, ValidationError, ValueError) as exc:
            flash(request, str(exc), "warning")

    return render_template(
        request,
        "orders/edit.html",
        form=form,
        order=order,
        material_units=_material_units(service),
        status_code=400 if request.method == "POST" and form.errors else 200,
    )


@router.post("/orders/{id}/delete", name="orders.delete_order")
async def delete_order(id: int, request: Request, current_user=Depends(require_permission("orders.delete"))):
    service = OrderService()
    try:
        service.delete_order(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        flash(request, str(exc), "warning")
        return RedirectResponse(url=str(request.url_for("orders.view_order", id=id)), status_code=303)

    record_audit(current_user, "delete", "order", id, f"Order #{id} deleted")
    flash(request, "Order deleted successfully!", "success")
    return RedirectResponse(url=str(request.url_for("orders.orders")), status_code=303)
