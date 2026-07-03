from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..core.auth import require_permission
from ..core.flash import flash
from ..core.templating import render_template
from ..extensions import db
from ..forms import ContractorRateForm
from ..models import Contractor, Vehicle
from ..services import NotFoundError, RateService

router = APIRouter()


def _populate_rate_choices(form, service: RateService):
    choices = service.build_form_choices()
    form.contractor_id.choices = choices["contractor_choices"]
    form.site_id.choices = choices["site_choices"]
    form.from_site_id.choices = choices["from_site_choices"]
    form.material_id.choices = choices["material_choices"]
    form.vehicle_owner_id.choices = choices["vehicle_owner_choices"]


def _rate_data_from_form(form):
    return {
        "contractor_id": form.contractor_id.data,
        "site_id": form.site_id.data,
        "from_site_id": form.from_site_id.data if form.from_site_id.data not in (None, 0) else None,
        "material_id": form.material_id.data if form.material_id.data not in (None, 0) else None,
        "vehicle_owner_id": form.vehicle_owner_id.data if form.vehicle_owner_id.data not in (None, 0) else None,
        "unit": form.unit.data,
        "rate": form.rate.data,
        "vehicle_rate": form.vehicle_rate.data or None,
        "effective_from": form.effective_from.data,
        "effective_to": form.effective_to.data or None,
        "notes": (form.notes.data or "").strip() or None,
    }


@router.get("/rates", name="rates.index")
async def rates_index(request: Request, _=Depends(require_permission("rates.view"))):
    service = RateService()
    raw_contractor_id = request.query_params.get("contractor_id")
    contractor_id = int(raw_contractor_id) if raw_contractor_id and raw_contractor_id.isdigit() else None
    rates = service.list_rates(contractor_id=contractor_id)
    contractors = Contractor.query.order_by(Contractor.name).all()
    return render_template(
        request,
        "rates/list.html",
        rates=rates,
        contractors=contractors,
        selected_contractor_id=contractor_id,
        today=date.today(),
    )


@router.api_route("/rates/create", methods=["GET", "POST"], name="rates.create")
async def create_rate(request: Request, _=Depends(require_permission("rates.create"))):
    service = RateService()
    form = ContractorRateForm(await request.form() if request.method == "POST" else None)
    _populate_rate_choices(form, service)

    if request.method == "GET":
        form.effective_from.data = date.today()

    if request.method == "POST" and form.validate():
        service.create_rate(_rate_data_from_form(form))
        flash(request, "Rate saved successfully!", "success")
        return RedirectResponse(url=str(request.url_for("rates.index")), status_code=303)

    return render_template(request, "rates/create.html", form=form)


@router.get("/rates/{id}", name="rates.view")
async def view_rate(id: int, request: Request, _=Depends(require_permission("rates.view"))):
    service = RateService()
    try:
        rate = service.get_rate(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return render_template(request, "rates/view.html", rate=rate, today=date.today())


@router.api_route("/rates/{id}/edit", methods=["GET", "POST"], name="rates.edit")
async def edit_rate(id: int, request: Request, _=Depends(require_permission("rates.edit"))):
    service = RateService()
    try:
        rate = service.get_rate(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    form = ContractorRateForm(await request.form() if request.method == "POST" else None, obj=rate)
    _populate_rate_choices(form, service)

    if request.method == "GET":
        form.contractor_id.data = rate.contractor_id
        form.site_id.data = rate.site_id
        form.from_site_id.data = rate.from_site_id or 0
        form.material_id.data = rate.material_id or 0
        form.vehicle_owner_id.data = rate.vehicle_owner_id or 0
        form.effective_from.data = rate.effective_from
        form.effective_to.data = rate.effective_to

    if request.method == "POST" and form.validate():
        service.update_rate(id, _rate_data_from_form(form))
        flash(request, "Rate updated successfully!", "success")
        return RedirectResponse(url=str(request.url_for("rates.view", id=id)), status_code=303)

    return render_template(request, "rates/edit.html", form=form, rate=rate)


@router.post("/rates/{id}/delete", name="rates.delete")
async def delete_rate(id: int, request: Request, _=Depends(require_permission("rates.delete"))):
    service = RateService()
    try:
        service.delete_rate(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    flash(request, "Rate deleted.", "success")
    return RedirectResponse(url=str(request.url_for("rates.index")), status_code=303)


@router.get("/rates/contractor/{contractor_id}/print", name="rates.print_contractor")
async def print_contractor_rates(contractor_id: int, request: Request, _=Depends(require_permission("rates.view"))):
    contractor = db.session.get(Contractor, contractor_id)
    if contractor is None:
        raise HTTPException(status_code=404, detail="Contractor not found")
    service = RateService()
    rates = service.list_rates(contractor_id=contractor_id)
    return render_template(
        request,
        "rates/print_contractor.html",
        contractor=contractor,
        rates=rates,
        today=date.today(),
        now=datetime.now(),
    )


@router.get("/api/rates/lookup", name="rates.api_lookup")
async def api_rate_lookup(request: Request, _=Depends(require_permission("orders.view"))):
    """Return the most applicable contractor rate for the given order parameters."""
    params = request.query_params

    def _int(key):
        val = params.get(key, "")
        try:
            return int(val) or None
        except (ValueError, TypeError):
            return None

    contractor_id = _int("contractor_id")
    site_id = _int("site_id")
    from_site_id = _int("from_site_id")
    material_id = _int("material_id")
    vehicle_owner_id = _int("vehicle_owner_id")
    order_date_str = params.get("order_date", "")

    # The order form knows the vehicle, not the owner — resolve it here.
    if not vehicle_owner_id:
        vehicle_id = _int("vehicle_id")
        if vehicle_id:
            vehicle = db.session.get(Vehicle, vehicle_id)
            vehicle_owner_id = vehicle.owner_id if vehicle else None

    if not contractor_id or not site_id:
        return JSONResponse({"found": False, "rate": None})

    try:
        check_date = date.fromisoformat(order_date_str) if order_date_str else date.today()
    except ValueError:
        check_date = date.today()

    service = RateService()
    rate = service.find_applicable_rate(contractor_id, site_id, from_site_id, material_id, check_date, vehicle_owner_id=vehicle_owner_id)

    if rate:
        return JSONResponse({
            "found": True,
            "rate": rate.rate,
            "vehicle_rate": rate.vehicle_rate,
            "unit": rate.unit,
            "rate_id": rate.id,
            "effective_from": rate.effective_from.isoformat(),
            "effective_to": rate.effective_to.isoformat() if rate.effective_to else None,
            "notes": rate.notes or "",
            "from_site": rate.from_site.name if rate.from_site else None,
            "material": rate.material.name if rate.material else None,
            "vehicle_owner": rate.vehicle_owner.name if rate.vehicle_owner else None,
        })

    return JSONResponse({"found": False, "rate": None})
