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
            PetrolPumpService().create_petrol_pump(form.name.data)
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
            service.update_petrol_pump(id, form.name.data)
            flash(request, "Petrol pump updated successfully!", "success")
            return RedirectResponse(url=str(request.url_for("petrol_pumps.index")), status_code=303)
        except (ConflictError, ValidationError) as exc:
            flash(request, str(exc), "warning")

    return render_template(request, "petrol_pumps/edit.html", form=form, petrol_pump=petrol_pump)


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
