from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..core.auth import require_permission
from ..core.flash import flash
from ..core.templating import render_template
from ..forms import MaterialForm
from ..services import ConflictError, MaterialService, NotFoundError, ValidationError


router = APIRouter()


@router.get("/materials", name="materials.index")
async def index(request: Request, _current_user=Depends(require_permission("materials.view"))):
    return render_template(request, "materials/list.html", materials=MaterialService().list_materials())


@router.api_route("/materials/create", methods=["GET", "POST"], name="materials.create_material")
async def create_material(request: Request, _current_user=Depends(require_permission("materials.create"))):
    form = MaterialForm(await request.form() if request.method == "POST" else None)
    if request.method == "POST" and form.validate():
        try:
            MaterialService().create_material(form.name.data, form.unit.data)
            flash(request, "Material created successfully!", "success")
            return RedirectResponse(url=str(request.url_for("materials.index")), status_code=303)
        except (ConflictError, ValidationError) as exc:
            flash(request, str(exc), "warning")

    return render_template(request, "materials/create.html", form=form)


@router.get("/materials/{id}", name="materials.view_material")
async def view_material(id: int, request: Request, _current_user=Depends(require_permission("materials.view"))):
    try:
        material = MaterialService().get_material(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    related_orders = sorted(
        material.orders,
        key=lambda order: (order.completion_date or order.order_date, order.id),
        reverse=True,
    )
    completed_orders = [order for order in related_orders if (order.status or "").lower() == "completed"]
    billed_orders = [order for order in completed_orders if order.is_billed]
    stats = {
        "total_orders": len(related_orders),
        "completed_count": len(completed_orders),
        "billed_count": len(billed_orders),
        "unbilled_count": len(completed_orders) - len(billed_orders),
        "total_delivered": sum((o.delivered_quantity or o.quantity or 0) for o in completed_orders),
        "total_revenue": sum(o.total_contractor_amount() for o in completed_orders),
        "contractor_count": len({o.contractor_id for o in completed_orders if o.contractor_id}),
    }
    return render_template(
        request,
        "materials/view.html",
        material=material,
        stats=stats,
        recent_orders=related_orders[:20],
    )


@router.api_route("/materials/{id}/edit", methods=["GET", "POST"], name="materials.edit_material")
async def edit_material(id: int, request: Request, _current_user=Depends(require_permission("materials.edit"))):
    service = MaterialService()
    try:
        material = service.get_material(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    form = MaterialForm(await request.form() if request.method == "POST" else None, obj=material)
    if request.method == "POST" and form.validate():
        try:
            service.update_material(id, form.name.data, form.unit.data)
            flash(request, "Material updated successfully!", "success")
            return RedirectResponse(url=str(request.url_for("materials.index")), status_code=303)
        except (ConflictError, ValidationError) as exc:
            flash(request, str(exc), "warning")

    return render_template(request, "materials/edit.html", form=form, material=material)


@router.post("/materials/{id}/delete", name="materials.delete_material")
async def delete_material(id: int, request: Request, _current_user=Depends(require_permission("materials.delete"))):
    service = MaterialService()
    try:
        service.delete_material(id)
        flash(request, "Material deleted successfully!", "success")
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        flash(request, str(exc), "warning")
        return RedirectResponse(url=str(request.url_for("materials.view_material", id=id)), status_code=303)

    return RedirectResponse(url=str(request.url_for("materials.index")), status_code=303)
