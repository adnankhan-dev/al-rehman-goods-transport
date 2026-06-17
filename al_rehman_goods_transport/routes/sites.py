from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..core.auth import require_permission
from ..core.flash import flash
from ..core.templating import render_template
from ..extensions import db
from ..forms import SiteForm
from ..models import Contractor, Order, Site, Transaction


router = APIRouter()


def _populate_contractor_choices(form):
    form.contractor_id.choices = [(0, "No contractor link")] + [(c.id, c.name) for c in Contractor.query.order_by(Contractor.name.asc()).all()]


def _validate_site_form(form):
    if (form.contractor_id.data or 0) == 0 and not form.is_business_site.data:
        return "Select a contractor or mark the site as associated with your business."
    return None


@router.get("/sites", name="sites.sites")
async def sites(request: Request, _current_user=Depends(require_permission("sites.view"))):
    return render_template(request, "sites/list.html", sites=Site.query.all())


@router.api_route("/sites/create", methods=["GET", "POST"], name="sites.create_site")
async def create_site(request: Request, _current_user=Depends(require_permission("sites.create"))):
    form = SiteForm(await request.form() if request.method == "POST" else None)
    _populate_contractor_choices(form)

    if request.method == "POST" and form.validate():
        validation_error = _validate_site_form(form)
        if validation_error:
            flash(request, validation_error, "warning")
            return render_template(request, "sites/create.html", form=form, status_code=400)

        site = Site(
            name=form.name.data,
            address=form.address.data,
            contact_person=form.contact_person.data,
            phone=form.phone.data,
            contractor_id=form.contractor_id.data or None,
            is_business_site=bool(form.is_business_site.data),
            is_archived=bool(form.is_archived.data),
        )
        db.session.add(site)
        db.session.commit()
        flash(request, "Site created successfully!", "success")
        return RedirectResponse(url=str(request.url_for("sites.sites")), status_code=303)

    return render_template(request, "sites/create.html", form=form)


@router.get("/sites/{id}", name="sites.view_site")
async def view_site(id: int, request: Request, _current_user=Depends(require_permission("sites.view"))):
    site = db.session.get(Site, id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site not found")

    related_orders = sorted(
        Order.query.filter((Order.site_id == id) | (Order.from_site_id == id)).all(),
        key=lambda order: (order.completion_date or order.order_date, order.id),
        reverse=True,
    )
    completed_orders = [order for order in related_orders if order.status == "Completed"]
    total_orders = len(related_orders)
    completed_orders_count = len(completed_orders)
    total_quantity = sum(order.delivered_quantity for order in completed_orders if order.delivered_quantity)
    total_profit = sum(order.profit_amount() for order in completed_orders)
    related_transactions = []
    if site.contractor_id:
        related_transactions = (
            Transaction.query.filter_by(contractor_id=site.contractor_id)
            .order_by(Transaction.date.desc(), Transaction.id.desc())
            .all()
        )

    return render_template(
        request,
        "sites/view.html",
        site=site,
        total_orders=total_orders,
        completed_orders_count=completed_orders_count,
        total_quantity=total_quantity,
        total_profit=total_profit,
        related_orders=related_orders,
        related_transactions=related_transactions,
    )


@router.api_route("/sites/{id}/edit", methods=["GET", "POST"], name="sites.edit_site")
async def edit_site(id: int, request: Request, _current_user=Depends(require_permission("sites.edit"))):
    site = db.session.get(Site, id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site not found")

    form = SiteForm(await request.form() if request.method == "POST" else None, obj=site)
    _populate_contractor_choices(form)
    if request.method == "GET":
        form.contractor_id.data = site.contractor_id or 0

    if request.method == "POST" and form.validate():
        validation_error = _validate_site_form(form)
        if validation_error:
            flash(request, validation_error, "warning")
            return render_template(request, "sites/edit.html", form=form, site=site, status_code=400)

        site.name = form.name.data
        site.address = form.address.data
        site.contact_person = form.contact_person.data
        site.phone = form.phone.data
        site.contractor_id = form.contractor_id.data or None
        site.is_business_site = bool(form.is_business_site.data)
        site.is_archived = bool(form.is_archived.data)
        db.session.commit()
        flash(request, "Site updated successfully!", "success")
        return RedirectResponse(url=str(request.url_for("sites.sites")), status_code=303)

    return render_template(request, "sites/edit.html", form=form, site=site)


@router.post("/sites/{id}/archive", name="sites.archive_site")
async def archive_site(id: int, request: Request, _current_user=Depends(require_permission("sites.edit"))):
    site = db.session.get(Site, id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site not found")

    site.is_archived = not bool(site.is_archived)
    db.session.commit()
    state = "archived" if site.is_archived else "restored"
    flash(request, f"Site '{site.name}' {state}.", "success")
    return RedirectResponse(url=str(request.url_for("sites.sites")), status_code=303)


@router.post("/sites/{id}/delete", name="sites.delete_site")
async def delete_site(id: int, request: Request, _current_user=Depends(require_permission("sites.delete"))):
    site = db.session.get(Site, id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site not found")

    from ..models import ContractorRate

    in_use = (
        db.session.query(Order.id).filter((Order.site_id == id) | (Order.from_site_id == id)).first() is not None
        or db.session.query(ContractorRate.id).filter((ContractorRate.site_id == id) | (ContractorRate.from_site_id == id)).first() is not None
    )
    if in_use:
        flash(request, "This site is referenced by orders or rate schedules and cannot be deleted.", "danger")
        return RedirectResponse(url=str(request.url_for("sites.view_site", id=id)), status_code=303)

    db.session.delete(site)
    db.session.commit()
    flash(request, "Site deleted successfully!", "success")
    return RedirectResponse(url=str(request.url_for("sites.sites")), status_code=303)


@router.get("/api/sites/{contractor_id}", name="sites.get_sites")
async def get_sites(contractor_id: int, _current_user=Depends(require_permission("sites.view"))):
    # Used by the order entry form cascade — exclude archived sites to keep it clean.
    sites_query = (
        Site.query.filter_by(contractor_id=contractor_id)
        .filter(Site.is_archived.is_(False))
        .order_by(Site.name.asc())
        .all()
    )
    return [{"id": site.id, "name": site.name} for site in sites_query]
