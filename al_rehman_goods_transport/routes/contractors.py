from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..core.auth import require_permission
from ..core.flash import flash
from ..core.templating import render_template
from ..extensions import db
from ..forms import ContractorForm
from ..models import Contractor


router = APIRouter()


@router.get("/contractors", name="contractors.contractors")
async def contractors(request: Request, _current_user=Depends(require_permission("contractors.view"))):
    return render_template(request, "contractors/list.html", contractors=Contractor.query.all())


@router.api_route("/contractors/create", methods=["GET", "POST"], name="contractors.create_contractor")
async def create_contractor(request: Request, _current_user=Depends(require_permission("contractors.create"))):
    form = ContractorForm(await request.form() if request.method == "POST" else None)
    if request.method == "POST" and form.validate():
        contractor = Contractor(
            name=form.name.data,
            contact_person=form.contact_person.data,
            phone=form.phone.data,
            email=form.email.data,
            address=form.address.data,
            payment_terms=form.payment_terms.data,
            balance=form.balance.data or 0.0,
            opening_balance=form.opening_balance.data or 0.0,
        )
        db.session.add(contractor)
        db.session.commit()
        flash(request, "Contractor created successfully!", "success")
        return RedirectResponse(url=str(request.url_for("contractors.contractors")), status_code=303)

    return render_template(request, "contractors/create.html", form=form)


@router.get("/contractors/{id}", name="contractors.view_contractor")
async def view_contractor(id: int, request: Request, _current_user=Depends(require_permission("contractors.view"))):
    contractor = db.session.get(Contractor, id)
    if contractor is None:
        raise HTTPException(status_code=404, detail="Contractor not found")

    completed_orders = sorted(
        [order for order in contractor.orders if order.status == "Completed"],
        key=lambda order: (order.completion_date or order.order_date, order.id),
        reverse=True,
    )
    total_owed = sum(order.total_contractor_amount() for order in completed_orders)
    billed_orders = [order for order in completed_orders if order.is_billed]
    unbilled_orders = [order for order in completed_orders if not order.is_billed]
    recent_bills = sorted(contractor.bills, key=lambda bill: (bill.bill_date, bill.id), reverse=True)[:5]
    related_transactions = sorted(contractor.transactions, key=lambda transaction: (transaction.date, transaction.id), reverse=True)
    return render_template(
        request,
        "contractors/view.html",
        contractor=contractor,
        total_owed=total_owed,
        completed_orders=completed_orders,
        billed_amount=sum(order.billable_amount for order in billed_orders),
        unbilled_amount=sum(order.billable_amount for order in unbilled_orders),
        billed_count=len(billed_orders),
        unbilled_count=len(unbilled_orders),
        recent_bills=recent_bills,
        related_transactions=related_transactions,
    )


@router.api_route("/contractors/{id}/edit", methods=["GET", "POST"], name="contractors.edit_contractor")
async def edit_contractor(id: int, request: Request, _current_user=Depends(require_permission("contractors.edit"))):
    contractor = db.session.get(Contractor, id)
    if contractor is None:
        raise HTTPException(status_code=404, detail="Contractor not found")

    form = ContractorForm(await request.form() if request.method == "POST" else None, obj=contractor)
    # The running balance is maintained by orders and receipts; editing master
    # data must never overwrite it. Adjustments go through the ledger.
    del form.balance
    if request.method == "POST" and form.validate():
        form.populate_obj(contractor)
        db.session.commit()
        flash(request, "Contractor updated successfully!", "success")
        return RedirectResponse(url=str(request.url_for("contractors.contractors")), status_code=303)

    return render_template(request, "contractors/edit.html", form=form, contractor=contractor)


@router.post("/contractors/{id}/delete", name="contractors.delete_contractor")
async def delete_contractor(id: int, request: Request, _current_user=Depends(require_permission("contractors.delete"))):
    contractor = db.session.get(Contractor, id)
    if contractor is None:
        raise HTTPException(status_code=404, detail="Contractor not found")

    if contractor.orders or contractor.sites or contractor.bills or contractor.transactions:
        flash(request, "This contractor has orders, sites, bills, or transactions and cannot be deleted.", "danger")
        return RedirectResponse(url=str(request.url_for("contractors.view_contractor", id=id)), status_code=303)

    db.session.delete(contractor)
    db.session.commit()
    flash(request, "Contractor deleted successfully!", "success")
    return RedirectResponse(url=str(request.url_for("contractors.contractors")), status_code=303)
