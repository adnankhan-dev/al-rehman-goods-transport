from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..core.auth import require_permission
from ..core.flash import flash
from ..core.templating import render_template
from ..models import TRANSACTION_TYPES
from ..services.exceptions import NotFoundError, ValidationError
from ..services.financial_entities import FinancialEntityService


router = APIRouter()


def _parse_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


@router.get("/financial-entities", name="financial_entities.index")
async def index(request: Request, _current_user=Depends(require_permission("ledger.view"))):
    service = FinancialEntityService()
    entities = service.list_entities()
    total_owed_to_us = sum(e.balance for e in entities if e.balance > 0)
    total_we_owe = sum(-e.balance for e in entities if e.balance < 0)
    return render_template(
        request,
        "financial_entities/list.html",
        entities=entities,
        total_owed_to_us=total_owed_to_us,
        total_we_owe=total_we_owe,
    )


@router.api_route("/financial-entities/create", methods=["GET", "POST"], name="financial_entities.create")
async def create(request: Request, _current_user=Depends(require_permission("ledger.create"))):
    service = FinancialEntityService()
    if request.method == "POST":
        data = await request.form()
        try:
            entity = service.create_entity(
                name=data.get("name", ""),
                phone=data.get("phone", ""),
                notes=data.get("notes", ""),
                initial_balance=_parse_float(data.get("initial_balance"), 0.0),
            )
            flash(request, f"Financial entity '{entity.name}' created.", "success")
            return RedirectResponse(url=str(request.url_for("financial_entities.view", id=entity.id)), status_code=303)
        except ValidationError as exc:
            flash(request, str(exc), "warning")
    return render_template(request, "financial_entities/create.html")


@router.get("/financial-entities/{id}", name="financial_entities.view")
async def view(id: int, request: Request, _current_user=Depends(require_permission("ledger.view"))):
    service = FinancialEntityService()
    try:
        entity = service.get_entity(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return render_template(
        request,
        "financial_entities/view.html",
        entity=entity,
        transaction_types=TRANSACTION_TYPES,
    )


@router.api_route("/financial-entities/{id}/edit", methods=["GET", "POST"], name="financial_entities.edit")
async def edit(id: int, request: Request, _current_user=Depends(require_permission("ledger.create"))):
    service = FinancialEntityService()
    try:
        entity = service.get_entity(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if request.method == "POST":
        data = await request.form()
        try:
            service.update_entity(
                id,
                name=data.get("name", ""),
                phone=data.get("phone", ""),
                notes=data.get("notes", ""),
            )
            flash(request, "Entity updated.", "success")
            return RedirectResponse(url=str(request.url_for("financial_entities.view", id=id)), status_code=303)
        except ValidationError as exc:
            flash(request, str(exc), "warning")
    return render_template(request, "financial_entities/edit.html", entity=entity)


@router.post("/financial-entities/{id}/delete", name="financial_entities.delete")
async def delete(id: int, request: Request, _current_user=Depends(require_permission("ledger.create"))):
    service = FinancialEntityService()
    try:
        entity = service.get_entity(id)
        name = entity.name
        service.delete_entity(id)
        flash(request, f"Deleted '{name}'.", "success")
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(url=str(request.url_for("financial_entities.index")), status_code=303)


@router.get("/financial-entities/{id}/statement", name="financial_entities.statement")
async def statement(id: int, request: Request, _current_user=Depends(require_permission("ledger.view"))):
    service = FinancialEntityService()
    try:
        entity = service.get_entity(id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    txs_chrono = sorted(entity.transactions, key=lambda t: (t.date, t.id))
    running = 0.0
    rows = []
    for tx in txs_chrono:
        running += tx.balance_delta
        rows.append({"tx": tx, "running_balance": running})
    return render_template(
        request,
        "financial_entities/statement.html",
        entity=entity,
        rows=rows,
        closing_balance=running,
        show_nav=False,
    )


@router.post("/financial-entities/{id}/transaction", name="financial_entities.post_transaction")
async def post_transaction(id: int, request: Request, _current_user=Depends(require_permission("ledger.create"))):
    service = FinancialEntityService()
    data = await request.form()
    try:
        tx = service.post_transaction(
            entity_id=id,
            transaction_type=data.get("transaction_type", ""),
            amount=_parse_float(data.get("amount")),
            date=_parse_date(data.get("date")),
            payment_method=data.get("payment_method", ""),
            reference=data.get("reference", ""),
            notes=data.get("notes", ""),
        )
        flash(request, f"Transaction posted: {tx.type_short} Rs. {tx.amount:,.2f}.", "success")
    except (NotFoundError, ValidationError) as exc:
        flash(request, str(exc), "warning")
    return RedirectResponse(url=str(request.url_for("financial_entities.view", id=id)), status_code=303)


@router.post("/financial-entities/{id}/transactions/{tx_id}/delete", name="financial_entities.delete_transaction")
async def delete_transaction(id: int, tx_id: int, request: Request, _current_user=Depends(require_permission("ledger.create"))):
    service = FinancialEntityService()
    try:
        service.delete_transaction(tx_id)
        flash(request, "Transaction removed and balance reversed.", "success")
    except (NotFoundError, ValidationError) as exc:
        flash(request, str(exc), "warning")
    return RedirectResponse(url=str(request.url_for("financial_entities.view", id=id)), status_code=303)
