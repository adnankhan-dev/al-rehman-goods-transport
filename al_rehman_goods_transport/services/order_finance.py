from dataclasses import dataclass

from ..extensions import db
from ..repositories import CompanyRepository


def _safe_amount(value):
    return float(value or 0.0)


def get_or_create_company():
    return CompanyRepository(db.session).get_singleton()


def gross_vehicle_amount(order):
    quantity = _safe_amount(order.delivered_quantity or order.quantity)
    return quantity * _safe_amount(order.vehicle_rate)


def contractor_receivable(order):
    quantity = _safe_amount(order.delivered_quantity or order.quantity)
    return quantity * _safe_amount(order.contractor_rate)


def net_vehicle_amount(order):
    return gross_vehicle_amount(order) - _safe_amount(order.commission)


def remaining_vehicle_payment(order):
    return net_vehicle_amount(order) - _safe_amount(order.advance_amount) - _safe_amount(order.total_diesel_amount())


@dataclass
class OrderFinanceSnapshot:
    vehicle_id: int | None
    contractor_id: int | None
    plant_id: int | None
    petrol_pump_id: int | None
    quantity: float | None
    delivered_quantity: float | None
    vehicle_rate: float | None
    contractor_rate: float | None
    advance_amount: float | None
    diesel_amount: float | None
    plant_amount: float | None
    commission: float | None
    status: str | None
    company_advance_applied: bool


class OrderFinanceState:
    def __init__(
        self,
        vehicle_id=None,
        contractor_id=None,
        plant_id=None,
        petrol_pump_id=None,
        quantity=None,
        delivered_quantity=None,
        vehicle_rate=None,
        contractor_rate=None,
        advance_amount=None,
        diesel_amount=None,
        plant_amount=None,
        commission=None,
        status=None,
    ):
        self.vehicle_id = vehicle_id
        self.contractor_id = contractor_id
        self.plant_id = plant_id
        self.petrol_pump_id = petrol_pump_id
        self.quantity = quantity
        self.delivered_quantity = delivered_quantity
        self.vehicle_rate = vehicle_rate
        self.contractor_rate = contractor_rate
        self.advance_amount = advance_amount
        self.diesel_amount = diesel_amount
        self.plant_amount = plant_amount
        self.commission = commission
        self.status = status

    def total_diesel_amount(self):
        return _safe_amount(self.diesel_amount)


def snapshot_order(order):
    return OrderFinanceSnapshot(
        vehicle_id=order.vehicle_id,
        contractor_id=order.contractor_id,
        plant_id=order.plant_id,
        petrol_pump_id=order.primary_diesel_entry.petrol_pump_id if getattr(order, "primary_diesel_entry", None) else None,
        quantity=order.quantity,
        delivered_quantity=order.delivered_quantity,
        vehicle_rate=order.vehicle_rate,
        contractor_rate=order.contractor_rate,
        advance_amount=order.advance_amount,
        diesel_amount=order.total_diesel_amount(),
        plant_amount=order.plant_amount,
        commission=order.commission,
        status=order.status,
        company_advance_applied=bool(order.company_advance_applied),
    )


def is_completed(order):
    return (order.status or "").lower() == "completed"


def _apply_entity_balance_delta(session, model, entity_id, delta):
    if not entity_id:
        return

    entity = session.get(model, entity_id)
    if entity:
        entity.balance = _safe_amount(entity.balance) + delta


def _apply_completed_balances(session, order, multiplier):
    if not is_completed(order):
        return

    from ..models import Contractor, PetrolPump, Plant, Vehicle, VehicleOwner

    vehicle_delta = remaining_vehicle_payment(order) * multiplier
    _apply_entity_balance_delta(session, Vehicle, order.vehicle_id, vehicle_delta)

    vehicle = session.get(Vehicle, order.vehicle_id) if order.vehicle_id else None
    if vehicle and vehicle.owner_id:
        _apply_entity_balance_delta(session, VehicleOwner, vehicle.owner_id, vehicle_delta)

    _apply_entity_balance_delta(session, Contractor, order.contractor_id, contractor_receivable(order) * multiplier)
    _apply_entity_balance_delta(session, Plant, order.plant_id, _safe_amount(order.plant_amount) * multiplier)

    petrol_pump_id = getattr(order, "petrol_pump_id", None)
    if petrol_pump_id is None:
        primary_diesel_entry = getattr(order, "primary_diesel_entry", None)
        petrol_pump_id = primary_diesel_entry.petrol_pump_id if primary_diesel_entry else None
        diesel_amount = _safe_amount(primary_diesel_entry.amount) if primary_diesel_entry else _safe_amount(getattr(order, "diesel_amount", 0))
    else:
        diesel_amount = _safe_amount(getattr(order, "diesel_amount", 0))
    if petrol_pump_id:
        _apply_entity_balance_delta(session, PetrolPump, petrol_pump_id, diesel_amount * multiplier)


def sync_order_financials(session, order, previous_snapshot=None):
    company = CompanyRepository(session).get_singleton()

    if previous_snapshot is not None:
        previous_state = OrderFinanceState(
            vehicle_id=previous_snapshot.vehicle_id,
            contractor_id=previous_snapshot.contractor_id,
            plant_id=previous_snapshot.plant_id,
            petrol_pump_id=previous_snapshot.petrol_pump_id,
            quantity=previous_snapshot.quantity,
            delivered_quantity=previous_snapshot.delivered_quantity,
            vehicle_rate=previous_snapshot.vehicle_rate,
            contractor_rate=previous_snapshot.contractor_rate,
            advance_amount=previous_snapshot.advance_amount,
            diesel_amount=previous_snapshot.diesel_amount,
            plant_amount=previous_snapshot.plant_amount,
            commission=previous_snapshot.commission,
            status=previous_snapshot.status,
        )
        _apply_completed_balances(session, previous_state, -1)
        old_advance = _safe_amount(previous_snapshot.advance_amount) if previous_snapshot.company_advance_applied else 0.0
    else:
        old_advance = 0.0

    _apply_completed_balances(session, order, 1)

    new_advance = _safe_amount(order.advance_amount)
    company.balance = _safe_amount(company.balance) + old_advance - new_advance
    order.company_advance_applied = new_advance > 0


def reverse_order_financials(session, order):
    company = CompanyRepository(session).get_singleton()
    _apply_completed_balances(session, order, -1)
    if order.company_advance_applied:
        company.balance = _safe_amount(company.balance) + _safe_amount(order.advance_amount)
        order.company_advance_applied = False


def apply_order_creation_balances(order):
    sync_order_financials(db.session, order)


def apply_order_update_balances(order, previous_vehicle_id, previous_advance_amount, previous_diesel_amount):
    previous_snapshot = OrderFinanceSnapshot(
        vehicle_id=previous_vehicle_id,
        contractor_id=order.contractor_id,
        plant_id=order.plant_id,
        petrol_pump_id=order.primary_diesel_entry.petrol_pump_id if getattr(order, "primary_diesel_entry", None) else None,
        quantity=order.quantity,
        delivered_quantity=order.delivered_quantity,
        vehicle_rate=order.vehicle_rate,
        contractor_rate=order.contractor_rate,
        advance_amount=previous_advance_amount,
        diesel_amount=previous_diesel_amount,
        plant_amount=order.plant_amount,
        commission=order.commission,
        status=order.status,
        company_advance_applied=True,
    )
    sync_order_financials(db.session, order, previous_snapshot)


def apply_completed_order_balances(order):
    _apply_completed_balances(db.session, order, 1)


def apply_order_state(order):
    sync_order_financials(db.session, order)


def reverse_order_state(order):
    reverse_order_financials(db.session, order)
