from collections import defaultdict

from ..extensions import db
from ..models import (
    Contractor,
    DieselEntry,
    FinancialEntity,
    FinancialEntityTransaction,
    Order,
    PetrolPump,
    Plant,
    Transaction,
    Vehicle,
    VehicleOwner,
)
from ..repositories import CompanyRepository
from .exceptions import NotFoundError, ValidationError
from .order_finance import contractor_receivable, remaining_vehicle_payment

TOLERANCE = 0.01

_COMPANY_INFLOW_TYPES = ("initial_balance", "contractor_receipt", "vehicle_owner_receipt")
_COMPANY_OUTFLOW_TYPES = (
    "vehicle_payment",
    "vehicle_owner_payment",
    "plant_payment",
    "petrol_pump_payment",
    "other_expense",
    "vehicle_advance",
)


def _safe(value):
    return float(value or 0.0)


class ReconciliationService:
    """Compare stored balances against balances recomputed from recorded
    activity (orders, diesel entries, transactions).

    Opening balances entered at entity creation are not derivable from
    activity, so an entity created with an opening balance shows that amount
    as a standing difference — the page copy explains this to the admin.

    Individual vehicles are intentionally excluded: owner payments are
    distributed across vehicles proportionally without per-vehicle records,
    so only the owner-level figure is well-defined.
    """

    def __init__(self, session=None):
        self.session = session or db.session

    def build_report(self):
        completed_orders = self.session.query(Order).filter(Order.status == "Completed").all()
        transactions = self.session.query(Transaction).all()
        diesel_entries = self.session.query(DieselEntry).all()
        vehicles = {vehicle.id: vehicle for vehicle in self.session.query(Vehicle).all()}

        contractor_expected = defaultdict(float)
        owner_expected = defaultdict(float)
        plant_expected = defaultdict(float)
        pump_expected = defaultdict(float)

        for order in completed_orders:
            contractor_expected[order.contractor_id] += contractor_receivable(order)
            if order.plant_id:
                plant_expected[order.plant_id] += _safe(order.plant_amount)
            vehicle = vehicles.get(order.vehicle_id)
            if vehicle and vehicle.owner_id:
                owner_expected[vehicle.owner_id] += remaining_vehicle_payment(order)
            # The balance engine credits the pump from the primary legacy entry.
            primary = order.primary_diesel_entry
            if primary and primary.petrol_pump_id:
                pump_expected[primary.petrol_pump_id] += _safe(primary.amount)

        for entry in diesel_entries:
            amount = _safe(entry.amount)
            if entry.balance_applied and entry.petrol_pump_id:
                pump_expected[entry.petrol_pump_id] += amount
            if entry.vehicle_balance_applied:
                vehicle = vehicles.get(entry.vehicle_id)
                if vehicle and vehicle.owner_id:
                    owner_expected[vehicle.owner_id] -= amount

        company_expected = 0.0
        for tx in transactions:
            amount = _safe(tx.amount)
            if tx.type == "contractor_receipt":
                contractor_expected[tx.contractor_id or tx.entity_id] -= amount
            elif tx.type == "plant_payment":
                plant_expected[tx.plant_id or tx.entity_id] -= amount
            elif tx.type == "petrol_pump_payment":
                pump_expected[tx.petrol_pump_id or tx.entity_id] -= amount
            elif tx.type == "vehicle_owner_payment":
                owner_expected[tx.vehicle_owner_id or tx.entity_id] -= amount
            elif tx.type == "vehicle_owner_receipt":
                owner_expected[tx.vehicle_owner_id or tx.entity_id] += amount
            elif tx.type == "vehicle_payment":
                vehicle = vehicles.get(tx.vehicle_id or tx.entity_id)
                if vehicle and vehicle.owner_id:
                    owner_expected[vehicle.owner_id] -= amount

            if tx.type in _COMPANY_INFLOW_TYPES:
                company_expected += amount
            elif tx.type in _COMPANY_OUTFLOW_TYPES:
                company_expected -= amount

        for fe_tx in self.session.query(FinancialEntityTransaction).all():
            amount = _safe(fe_tx.amount)
            if fe_tx.direction == "in":
                company_expected += amount
            elif fe_tx.direction == "out":
                company_expected -= amount

        rows = []
        company = CompanyRepository(self.session).get_singleton()
        rows.append(self._row("company", company.id, "Company Cash", company.balance, company_expected))

        for contractor in self.session.query(Contractor).order_by(Contractor.name.asc()).all():
            rows.append(self._row("contractor", contractor.id, contractor.name, contractor.balance, contractor_expected.get(contractor.id, 0.0)))
        for owner in self.session.query(VehicleOwner).order_by(VehicleOwner.name.asc()).all():
            rows.append(self._row("vehicle_owner", owner.id, owner.name, owner.balance, owner_expected.get(owner.id, 0.0)))
        for plant in self.session.query(Plant).order_by(Plant.name.asc()).all():
            rows.append(self._row("plant", plant.id, plant.name, plant.balance, plant_expected.get(plant.id, 0.0)))
        for pump in self.session.query(PetrolPump).order_by(PetrolPump.name.asc()).all():
            expected = pump_expected.get(pump.id, 0.0) + float(pump.opening_balance or 0)
            rows.append(self._row("petrol_pump", pump.id, pump.name, pump.balance, expected))
        for entity in self.session.query(FinancialEntity).order_by(FinancialEntity.name.asc()).all():
            expected = sum(_safe(tx.balance_delta) for tx in entity.transactions)
            rows.append(self._row("financial_entity", entity.id, entity.name, entity.balance, expected))

        drift_count = sum(1 for row in rows if row["has_drift"])
        return {"rows": rows, "drift_count": drift_count}

    def repair(self, entity_type, entity_id):
        model_map = {
            "contractor": Contractor,
            "vehicle_owner": VehicleOwner,
            "plant": Plant,
            "petrol_pump": PetrolPump,
            "financial_entity": FinancialEntity,
        }

        report = self.build_report()
        row = next(
            (item for item in report["rows"] if item["entity_type"] == entity_type and item["entity_id"] == entity_id),
            None,
        )
        if row is None:
            raise NotFoundError("Account not found in the reconciliation report.")
        if not row["has_drift"]:
            raise ValidationError("This account already matches its recorded activity.")

        if entity_type == "company":
            target = CompanyRepository(self.session).get_singleton()
        else:
            model = model_map.get(entity_type)
            if model is None:
                raise ValidationError("Unknown account type.")
            target = self.session.get(model, entity_id)
            if target is None:
                raise NotFoundError("Account not found.")

        try:
            target.balance = row["expected_balance"]
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return row

    def _row(self, entity_type, entity_id, name, stored, expected):
        stored_value = _safe(stored)
        expected_value = round(float(expected), 2)
        difference = round(stored_value - expected_value, 2)
        return {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "name": name,
            "stored_balance": stored_value,
            "expected_balance": expected_value,
            "difference": difference,
            "has_drift": abs(difference) > TOLERANCE,
        }
