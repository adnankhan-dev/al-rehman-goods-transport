"""Shared period financials for bills and statements.

An entity's previous balance is COMPUTED from history: operational accruals
(trips, diesel, plant loadings) plus ledger transactions dated before the
period start. Pre-ERP balances are ordinary backdated ``previous_balance``
transactions, so they flow through the same math instead of living in a
manually-set column.

Balance conventions (positive value):
    contractor    -> the contractor owes us
    vehicle_owner -> we owe the owner
    petrol_pump   -> we owe the pump
    plant         -> we owe the plant
"""

from datetime import timedelta

from sqlalchemy import and_, or_

from ..extensions import db
from ..models import (
    Contractor,
    DieselEntry,
    Order,
    OrderLoading,
    PetrolPump,
    Plant,
    Transaction,
    Vehicle,
    VehicleOwner,
)


# Effect of each transaction type on the entity's balance, in that entity's
# own convention (mirrors TransactionService._apply_transaction_effect).
_TXN_SIGNS = {
    "contractor": {
        "contractor_receipt": -1,
        "contractor_payment": +1,
        "previous_balance": +1,
    },
    "vehicle_owner": {
        "vehicle_owner_receipt": +1,
        "vehicle_owner_payment": -1,
        "vehicle_receipt": +1,
        "vehicle_payment": -1,
        "vehicle_advance": -1,
        "previous_balance": +1,
    },
    "petrol_pump": {
        "petrol_pump_receipt": +1,
        "petrol_pump_payment": -1,
        "previous_balance": +1,
    },
    "plant": {
        "plant_receipt": +1,
        "plant_payment": -1,
        "previous_balance": +1,
    },
}

_ENTITY_MODELS = {
    "contractor": Contractor,
    "vehicle_owner": VehicleOwner,
    "petrol_pump": PetrolPump,
    "plant": Plant,
}


def _safe(value):
    return float(value or 0.0)


def _as_date(value):
    return value.date() if hasattr(value, "date") else value


def _order_date(order):
    return _as_date(order.completion_date or order.order_date)


def transaction_sign(entity_type, txn):
    """Signed direction of a transaction on the entity's balance (0 if n/a)."""
    return _TXN_SIGNS.get(entity_type, {}).get(txn.type, 0)


def entity_transactions(entity_type, entity_id, session=None):
    """All approved ledger transactions that move this entity's balance,
    matched by the FK column or the generic entity link (and, for owners,
    transactions posted against any of the owner's vehicles)."""
    session = session or db.session
    signs = _TXN_SIGNS.get(entity_type, {})
    if not signs:
        return []

    fk_column = {
        "contractor": Transaction.contractor_id,
        "vehicle_owner": Transaction.vehicle_owner_id,
        "petrol_pump": Transaction.petrol_pump_id,
        "plant": Transaction.plant_id,
    }[entity_type]

    conditions = [
        fk_column == entity_id,
        and_(Transaction.entity_type == entity_type, Transaction.entity_id == entity_id),
    ]
    if entity_type == "vehicle_owner":
        vehicle_ids = session.query(Vehicle.id).filter(Vehicle.owner_id == entity_id)
        conditions.append(Transaction.vehicle_id.in_(vehicle_ids))

    return (
        session.query(Transaction)
        .filter(
            Transaction.type.in_(signs.keys()),
            Transaction.approval_status == "approved",
            or_(*conditions),
        )
        .order_by(Transaction.date.asc(), Transaction.id.asc())
        .all()
    )


def _accrual_rows(entity_type, entity_id, session, exclude_bill_id=None):
    """(date, amount) accrual rows: what the entity earns/owes over time,
    excluding rows attached to the bill being rendered (its own amount is
    shown as the bill line, so it must not leak into the previous balance)."""
    rows = []

    if entity_type == "contractor":
        orders = (
            session.query(Order)
            .filter(Order.contractor_id == entity_id, Order.status == "Completed")
            .all()
        )
        for order in orders:
            if exclude_bill_id and order.bill_id == exclude_bill_id:
                continue
            rows.append((_order_date(order), _safe(order.billable_amount)))

    elif entity_type == "vehicle_owner":
        owner = session.get(VehicleOwner, entity_id)
        if owner is not None and owner.is_company_expense:
            # Our own vehicles: their fuel is a company running cost booked in
            # the P&L, not a payable to a third-party owner. No accruals here.
            return rows
        orders = (
            session.query(Order)
            .join(Vehicle, Vehicle.id == Order.vehicle_id)
            .filter(Vehicle.owner_id == entity_id, Order.status == "Completed")
            .all()
        )
        for order in orders:
            if exclude_bill_id and order.vehicle_owner_bill_id == exclude_bill_id:
                continue
            rows.append((_order_date(order), _safe(order.remaining_vehicle_payment())))
        diesel_rows = (
            session.query(DieselEntry)
            .join(Vehicle, Vehicle.id == DieselEntry.vehicle_id)
            .filter(Vehicle.owner_id == entity_id, DieselEntry.approval_status == "approved")
            .all()
        )
        for entry in diesel_rows:
            if exclude_bill_id and entry.vehicle_owner_bill_id == exclude_bill_id:
                continue
            # Fuel from our pump reduces what we owe the owner.
            rows.append((_as_date(entry.date), -_safe(entry.amount)))

    elif entity_type == "petrol_pump":
        # Order-linked diesel is retired: all fuel is recorded in the Fuel Log
        # (DieselEntry), so pump accruals come from there alone.
        diesel_rows = (
            session.query(DieselEntry)
            .filter(DieselEntry.petrol_pump_id == entity_id, DieselEntry.approval_status == "approved")
            .all()
        )
        for entry in diesel_rows:
            if exclude_bill_id and entry.bill_id == exclude_bill_id:
                continue
            rows.append((_as_date(entry.date), _safe(entry.amount)))

    elif entity_type == "plant":
        loadings = (
            session.query(OrderLoading)
            .join(Order, Order.id == OrderLoading.order_id)
            .filter(
                OrderLoading.plant_id == entity_id,
                Order.status == "Completed",
                OrderLoading.plant_amount > 0,
            )
            .all()
        )
        for loading in loadings:
            if exclude_bill_id and loading.bill_id == exclude_bill_id:
                continue
            rows.append((_order_date(loading.order) if loading.order else None, _safe(loading.plant_amount)))

    return rows


AGEING_BUCKETS = ((0, 30), (31, 60), (61, 90), (91, None))


def entity_ageing(entity_type, entity_id, as_of=None, session=None):
    """Age an account's outstanding balance into 0–30 / 31–60 / 61–90 / 90+
    day buckets, FIFO: receipts/payments knock out the OLDEST charges first,
    so whatever remains unpaid is aged by the date it was earned/charged.

    Returns {"buckets": [b0, b1, b2, b3], "total": float, "credit": float}
    where ``credit`` is any advance position (paid more than charged)."""
    from datetime import date as date_cls

    session = session or db.session
    as_of = _as_date(as_of) or date_cls.today()
    signs = _TXN_SIGNS.get(entity_type, {})

    charges = []          # (day, amount) that INCREASE what is owed
    credits_total = 0.0   # everything that reduces it

    for day, amount in _accrual_rows(entity_type, entity_id, session):
        if day is not None and day > as_of:
            continue
        if amount > 0:
            charges.append((day, amount))
        else:
            credits_total += -amount

    for txn in entity_transactions(entity_type, entity_id, session=session):
        day = _as_date(txn.date)
        if day is not None and day > as_of:
            continue
        signed = signs.get(txn.type, 0) * _safe(txn.amount)
        if signed > 0:
            charges.append((day, signed))
        else:
            credits_total += -signed

    # Oldest first; undated rows are treated as oldest.
    charges.sort(key=lambda row: (row[0] is not None, row[0] or as_of))

    remaining_credit = credits_total
    buckets = [0.0, 0.0, 0.0, 0.0]
    for day, amount in charges:
        unpaid = amount
        if remaining_credit > 0:
            applied = min(remaining_credit, unpaid)
            remaining_credit -= applied
            unpaid -= applied
        if unpaid <= 0.005:
            continue
        age_days = (as_of - day).days if day else 10**6
        for index, (low, high) in enumerate(AGEING_BUCKETS):
            if age_days >= low and (high is None or age_days <= high):
                buckets[index] += unpaid
                break

    return {
        "buckets": buckets,
        "total": sum(buckets),
        "credit": remaining_credit if remaining_credit > 0.005 else 0.0,
    }


def entity_period_financials(
    entity_type,
    entity_id,
    start_date=None,
    end_date=None,
    exclude_bill_id=None,
    period_activity_total=None,
    session=None,
):
    """Financial picture of an entity for a period.

    Returns a dict with:
        previous_balance   balance as on the day before ``start_date``
                           (accruals + signed transactions before the start)
        receipts / receipts_total    period "Received from <name>" transactions
        payments / payments_total    period "Payment paid to <name>" transactions
        adjustments / adjustments_total   period previous-balance postings
        period_activity    accrued amount inside the period (or the supplied
                           ``period_activity_total``, e.g. a bill's amount)
        txn_delta          signed effect of all period transactions
        current_total      previous_balance + period_activity + txn_delta
    """
    session = session or db.session
    start = _as_date(start_date)
    end = _as_date(end_date)
    signs = _TXN_SIGNS.get(entity_type, {})

    def before(day):
        return start is not None and day is not None and day < start

    def in_period(day):
        if day is None:
            return start is None and end is None
        if start and day < start:
            return False
        if end and day > end:
            return False
        return True

    accruals = _accrual_rows(entity_type, entity_id, session, exclude_bill_id=exclude_bill_id)
    txns = entity_transactions(entity_type, entity_id, session=session)

    previous_balance = sum(amount for day, amount in accruals if before(day))
    computed_period_activity = sum(amount for day, amount in accruals if in_period(day))

    receipts, payments, adjustments = [], [], []
    txn_delta = 0.0
    for txn in txns:
        day = _as_date(txn.date)
        sign = signs.get(txn.type, 0)
        if before(day):
            previous_balance += sign * _safe(txn.amount)
            continue
        if not in_period(day):
            continue
        txn_delta += sign * _safe(txn.amount)
        if txn.type == "previous_balance":
            adjustments.append(txn)
        elif txn.type.endswith("_receipt"):
            receipts.append(txn)
        else:
            payments.append(txn)

    period_activity = (
        float(period_activity_total)
        if period_activity_total is not None
        else computed_period_activity
    )
    current_total = previous_balance + period_activity + txn_delta

    entity_model = _ENTITY_MODELS.get(entity_type)
    entity = session.get(entity_model, entity_id) if entity_model else None

    return {
        "entity": entity,
        "entity_name": getattr(entity, "name", None) or "-",
        "entity_type": entity_type,
        "start_date": start,
        "end_date": end,
        "previous_as_on": (start - timedelta(days=1)) if start else None,
        "previous_balance": previous_balance,
        "receipts": receipts,
        "receipts_total": sum(_safe(t.amount) for t in receipts),
        "payments": payments,
        "payments_total": sum(_safe(t.amount) for t in payments),
        "adjustments": adjustments,
        "adjustments_total": sum(signs.get(t.type, 0) * _safe(t.amount) for t in adjustments),
        "receipt_sign": signs.get(f"{entity_type}_receipt", 0),
        "payment_sign": signs.get(f"{entity_type}_payment", 0),
        "period_activity": period_activity,
        "txn_delta": txn_delta,
        "current_total": current_total,
    }
