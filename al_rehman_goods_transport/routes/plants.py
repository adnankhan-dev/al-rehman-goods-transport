from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..core.auth import require_permission
from ..core.flash import flash
from ..core.templating import render_template
from ..forms import PlantForm
from ..services import NotFoundError, PlantService, ValidationError


router = APIRouter()


@router.get("/plants", name="plants.plants")
async def plants(request: Request, _current_user=Depends(require_permission("plants.view"))):
    return render_template(request, "plants/list.html", plants=PlantService().list_plants())


@router.api_route("/plants/create", methods=["GET", "POST"], name="plants.create_plant")
async def create_plant(request: Request, _current_user=Depends(require_permission("plants.create"))):
    form = PlantForm(await request.form() if request.method == "POST" else None)
    if request.method == "POST" and form.validate():
        PlantService().create_plant(
            name=form.name.data,
            address=form.address.data,
            contact_person=form.contact_person.data,
            phone=form.phone.data,
            payment_terms=form.payment_terms.data,
            balance=form.balance.data or 0.0,
        )
        flash(request, "Plant created successfully!", "success")
        return RedirectResponse(url=str(request.url_for("plants.plants")), status_code=303)

    return render_template(request, "plants/create.html", form=form)


@router.get("/plants/{id}/print", name="plants.print_statement")
async def print_plant(id: int, request: Request, _current_user=Depends(require_permission("plants.view"))):
    from datetime import datetime

    def _date(value):
        try:
            return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    date_from = _date(request.query_params.get("date_from"))
    date_to = _date(request.query_params.get("date_to"))
    try:
        context = PlantService().statement(id, date_from=date_from, date_to=date_to)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return render_template(request, "plants/print.html", show_nav=False, now=datetime.now(), **context)


@router.get("/plants/{id}", name="plants.view_plant")
async def view_plant(id: int, request: Request, _current_user=Depends(require_permission("plants.view"))):
    try:
        context = PlantService().plant_dashboard(
            id,
            material_type=request.query_params.get("material_type"),
            start_date=request.query_params.get("start_date"),
            end_date=request.query_params.get("end_date"),
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return render_template(request, "plants/view.html", **context)


@router.api_route("/plants/{id}/edit", methods=["GET", "POST"], name="plants.edit_plant")
async def edit_plant(id: int, request: Request, _current_user=Depends(require_permission("plants.edit"))):
    service = PlantService()
    try:
        plant = service.get_plant(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    form = PlantForm(await request.form() if request.method == "POST" else None, obj=plant)
    # The running balance is maintained by loadings and payments; editing
    # master data must never overwrite it. Adjustments go through the ledger.
    del form.balance
    if request.method == "POST" and form.validate():
        service.update_plant(
            id,
            name=form.name.data,
            address=form.address.data,
            contact_person=form.contact_person.data,
            phone=form.phone.data,
            payment_terms=form.payment_terms.data,
        )
        flash(request, "Plant updated successfully!", "success")
        return RedirectResponse(url=str(request.url_for("plants.plants")), status_code=303)

    return render_template(request, "plants/edit.html", form=form, plant=plant)


@router.post("/plants/{id}/delete", name="plants.delete_plant")
async def delete_plant(id: int, request: Request, _current_user=Depends(require_permission("plants.delete"))):
    service = PlantService()
    try:
        service.delete_plant(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        flash(request, str(exc), "danger")
        return RedirectResponse(url=str(request.url_for("plants.view_plant", id=id)), status_code=303)

    flash(request, "Plant deleted successfully!", "success")
    return RedirectResponse(url=str(request.url_for("plants.plants")), status_code=303)
