from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..core.auth import require_permission
from ..core.flash import flash
from ..core.templating import render_template
from ..forms import PetrolPumpForm
from ..services import ConflictError, NotFoundError, PetrolPumpService, ValidationError


router = APIRouter()


@router.get("/petrol-pumps", name="petrol_pumps.index")
async def index(request: Request, _current_user=Depends(require_permission("petrol_pumps.view"))):
    return render_template(request, "petrol_pumps/list.html", petrol_pumps=PetrolPumpService().list_petrol_pumps())


@router.api_route("/petrol-pumps/create", methods=["GET", "POST"], name="petrol_pumps.create_petrol_pump")
async def create_petrol_pump(request: Request, _current_user=Depends(require_permission("petrol_pumps.create"))):
    form = PetrolPumpForm(await request.form() if request.method == "POST" else None)
    if request.method == "POST" and form.validate():
        try:
            PetrolPumpService().create_petrol_pump(form.name.data, opening_balance=form.opening_balance.data)
            flash(request, "Petrol pump created successfully!", "success")
            return RedirectResponse(url=str(request.url_for("petrol_pumps.index")), status_code=303)
        except (ConflictError, ValidationError) as exc:
            flash(request, str(exc), "warning")

    return render_template(request, "petrol_pumps/create.html", form=form)


@router.get("/petrol-pumps/{id}", name="petrol_pumps.view_petrol_pump")
async def view_petrol_pump(id: int, request: Request, _current_user=Depends(require_permission("petrol_pumps.view"))):
    try:
        context = PetrolPumpService().dashboard(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return render_template(request, "petrol_pumps/view.html", **context)


@router.get("/petrol-pumps/{id}/print", name="petrol_pumps.print_statement")
async def print_petrol_pump(id: int, request: Request, _current_user=Depends(require_permission("petrol_pumps.view"))):
    from datetime import datetime

    def _date(value):
        try:
            return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    date_from = _date(request.query_params.get("date_from"))
    date_to = _date(request.query_params.get("date_to"))
    try:
        context = PetrolPumpService().statement(id, date_from=date_from, date_to=date_to)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return render_template(request, "petrol_pumps/print.html", show_nav=False, now=datetime.now(), **context)


@router.api_route("/petrol-pumps/{id}/edit", methods=["GET", "POST"], name="petrol_pumps.edit_petrol_pump")
async def edit_petrol_pump(id: int, request: Request, _current_user=Depends(require_permission("petrol_pumps.edit"))):
    service = PetrolPumpService()
    try:
        petrol_pump = service.get_petrol_pump(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    form = PetrolPumpForm(await request.form() if request.method == "POST" else None, obj=petrol_pump)
    if request.method == "POST" and form.validate():
        try:
            service.update_petrol_pump(id, form.name.data, opening_balance=form.opening_balance.data)
            flash(request, "Petrol pump updated successfully!", "success")
            return RedirectResponse(url=str(request.url_for("petrol_pumps.index")), status_code=303)
        except (ConflictError, ValidationError) as exc:
            flash(request, str(exc), "warning")

    return render_template(request, "petrol_pumps/edit.html", form=form, petrol_pump=petrol_pump)


@router.post("/petrol-pumps/{id}/prices/add", name="petrol_pumps.add_price")
async def add_petrol_pump_price(id: int, request: Request, _current_user=Depends(require_permission("petrol_pumps.edit"))):
    from datetime import datetime as _dt

    def _date(value):
        try:
            return _dt.strptime((value or "").strip(), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    form_data = await request.form()
    service = PetrolPumpService()
    try:
        service.add_price(
            id,
            form_data.get("price"),
            _date(form_data.get("effective_from")),
            _date(form_data.get("effective_to")),
            form_data.get("notes"),
        )
        flash(request, "Fuel price saved.", "success")
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        flash(request, str(exc), "warning")
    return RedirectResponse(url=str(request.url_for("petrol_pumps.view_petrol_pump", id=id)), status_code=303)


@router.post("/petrol-pumps/{id}/prices/{price_id}/delete", name="petrol_pumps.delete_price")
async def delete_petrol_pump_price(id: int, price_id: int, request: Request, _current_user=Depends(require_permission("petrol_pumps.edit"))):
    service = PetrolPumpService()
    try:
        service.delete_price(id, price_id)
        flash(request, "Fuel price removed.", "success")
    except NotFoundError as exc:
        flash(request, str(exc), "warning")
    return RedirectResponse(url=str(request.url_for("petrol_pumps.view_petrol_pump", id=id)), status_code=303)


@router.post("/petrol-pumps/{id}/delete", name="petrol_pumps.delete_petrol_pump")
async def delete_petrol_pump(id: int, request: Request, _current_user=Depends(require_permission("petrol_pumps.delete"))):
    service = PetrolPumpService()
    try:
        service.delete_petrol_pump(id)
        flash(request, "Petrol pump deleted successfully!", "success")
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        flash(request, str(exc), "warning")
        return RedirectResponse(url=str(request.url_for("petrol_pumps.view_petrol_pump", id=id)), status_code=303)

    return RedirectResponse(url=str(request.url_for("petrol_pumps.index")), status_code=303)
