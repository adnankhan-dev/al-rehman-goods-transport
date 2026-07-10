from dataclasses import dataclass
from datetime import UTC, datetime, time

from sqlalchemy import func

from ..extensions import db
from ..models import Transaction, Vehicle
from ..repositories import CompanyRepository, LookupRepository, TransactionRepository
from .exceptions import NotFoundError, ValidationError


def _safe_amount(value):
    return float(value or 0.0)


def _utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


# Transaction types created by BillingService.settle_bill; when such a
# transaction carries a reference_id it points at the settled Bill.
_SETTLEMENT_TYPES = {"contractor_receipt", "plant_payment", "petrol_pump_payment", "vehicle_owner_payment"}


@dataclass
class TransactionInput:
    type: str
    amount: float
    description: str | None = None
    payment_method: str | None = None
    reference: str | None = None
    entity_type: str | None = None
    entity_id: int | None = None
    reference_id: int | None = None
    vehicle_id: int | None = None
    vehicle_owner_id: int | None = None
    contractor_id: int | None = None
    plant_id: int | None = None
    petrol_pump_id: int | None = None
    date: object | None = None
    is_system_generated: bool = False
    apply_financial_effect: bool = True


class TransactionService:
    def __init__(self, session=None):
        self.session = session or db.session
        self.transactions = TransactionRepository(self.session)
        self.lookups = LookupRepository(self.session)
        self.company = CompanyRepository(self.session)

    def list_transactions(self):
        return self.transactions.list_all()

    def get_transaction(self, transaction_id):
        transaction = self.transactions.get(transaction_id)
        if transaction is None:
            raise NotFoundError("Transaction not found.")
        return transaction

    def create_transaction(self, transaction_input: TransactionInput, commit=True, approval_status="approved"):
        """Create a transaction. Manual entries pass approval_status='pending' —
        they post NO balance effect and stay off the ledger until approved.
        Settlement and system-generated transactions keep the default 'approved'
        and apply immediately."""
        self._validate_transaction_input(transaction_input)
        kwargs = self._transaction_kwargs(transaction_input)
        kwargs["approval_status"] = approval_status
        transaction = Transaction(**kwargs)
        try:
            if approval_status == "approved":
                self._apply_transaction_effect(transaction, reverse=False, apply_financial_effect=transaction_input.apply_financial_effect)
            self.transactions.add(transaction)
            if commit:
                self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return transaction

    def approve_transaction(self, transaction_id, approver_id=None):
        """Approve a pending transaction: apply its balance effect and flip it live."""
        transaction = self.get_transaction(transaction_id)
        if transaction.approval_status == "approved":
            return transaction
        try:
            self._apply_transaction_effect(transaction, reverse=False, apply_financial_effect=True)
            self._adjust_bill_settlement(transaction.type, transaction.reference_id, _safe_amount(transaction.amount))
            transaction.approval_status = "approved"
            transaction.approved_by_id = approver_id
            transaction.approved_at = _utc_now()
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return transaction

    def reject_transaction(self, transaction_id):
        """Discard a pending transaction (no balance effect was applied)."""
        transaction = self.get_transaction(transaction_id)
        if transaction.approval_status == "approved":
            raise ValidationError("Approved transactions cannot be rejected. Delete it instead.")
        try:
            self.transactions.delete(transaction)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def pending_count(self):
        return self.session.query(func.count(Transaction.id)).filter(Transaction.approval_status == "pending").scalar() or 0

    def list_pending(self):
        return (
            self.session.query(Transaction)
            .filter(Transaction.approval_status == "pending")
            .order_by(Transaction.date.desc(), Transaction.id.desc())
            .all()
        )

    def update_transaction(self, transaction_id, transaction_input: TransactionInput):
        transaction = self.get_transaction(transaction_id)
        if transaction.is_system_generated:
            raise ValidationError("System-generated transactions cannot be edited.")

        self._validate_transaction_input(transaction_input)

        # A pending transaction has applied no effect yet — just rewrite its
        # fields; the effect is posted when it is approved.
        if transaction.approval_status == "pending":
            try:
                for key, value in self._transaction_kwargs(transaction_input).items():
                    setattr(transaction, key, value)
                self.session.commit()
            except Exception:
                self.session.rollback()
                raise
            return transaction

        previous_state = TransactionInput(
            type=transaction.type,
            amount=transaction.amount,
            entity_type=transaction.entity_type,
            entity_id=transaction.entity_id,
            reference_id=transaction.reference_id,
            vehicle_id=transaction.vehicle_id,
            vehicle_owner_id=transaction.vehicle_owner_id,
            contractor_id=transaction.contractor_id,
            plant_id=transaction.plant_id,
            petrol_pump_id=transaction.petrol_pump_id,
            apply_financial_effect=True,
        )

        self._validate_transaction_input(transaction_input)

        try:
            self._apply_transaction_effect(Transaction(**self._transaction_kwargs(previous_state)), reverse=True, apply_financial_effect=True)
            self._adjust_bill_settlement(previous_state.type, previous_state.reference_id, -_safe_amount(previous_state.amount))
            for key, value in self._transaction_kwargs(transaction_input).items():
                setattr(transaction, key, value)
            self._apply_transaction_effect(transaction, reverse=False, apply_financial_effect=transaction_input.apply_financial_effect)
            self._adjust_bill_settlement(transaction.type, transaction.reference_id, _safe_amount(transaction.amount))
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return transaction

    def delete_transaction(self, transaction_id):
        transaction = self.get_transaction(transaction_id)
        if transaction.is_system_generated:
            raise ValidationError("System-generated transactions cannot be deleted.")

        try:
            # A pending transaction applied no effect, so there is nothing to reverse.
            if transaction.approval_status != "pending":
                self._apply_transaction_effect(transaction, reverse=True, apply_financial_effect=True)
                self._adjust_bill_settlement(transaction.type, transaction.reference_id, -_safe_amount(transaction.amount))
            self.transactions.delete(transaction)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def sync_order_advance_transaction(self, order):
        existing_transaction = self.transactions.find_system_order_advance(order.id)
        vehicle = self.session.get(Vehicle, order.vehicle_id) if order.vehicle_id else None
        vehicle_owner_id = vehicle.owner_id if vehicle and vehicle.owner_id else None

        if _safe_amount(order.advance_amount) <= 0:
            if existing_transaction:
                self.transactions.delete(existing_transaction)
            return

        transaction_kwargs = {
            "type": "vehicle_advance",
            "entity_type": "vehicle",
            "entity_id": order.vehicle_id,
            "reference_id": order.id,
            "amount": _safe_amount(order.advance_amount),
            "description": f"Advance for order #{order.id}",
            "vehicle_id": order.vehicle_id,
            "vehicle_owner_id": vehicle_owner_id,
            "is_system_generated": True,
            "payment_method": None,
            "reference": order.builty_number,
            "contractor_id": None,
            "plant_id": None,
            "petrol_pump_id": None,
        }

        if existing_transaction:
            for key, value in transaction_kwargs.items():
                setattr(existing_transaction, key, value)
            return existing_transaction

        self.transactions.add(Transaction(**transaction_kwargs))
        return None

    def transactions_for_entity(self, entity_type, entity_id):
        return self.transactions.list_for_entity(entity_type, entity_id)

    def _transaction_kwargs(self, transaction_input: TransactionInput):
        kwargs = {
            "type": transaction_input.type,
            "amount": _safe_amount(transaction_input.amount),
            "description": transaction_input.description,
            "payment_method": transaction_input.payment_method,
            "reference": transaction_input.reference,
            "entity_type": transaction_input.entity_type,
            "entity_id": transaction_input.entity_id,
            "reference_id": transaction_input.reference_id,
            "vehicle_id": transaction_input.vehicle_id,
            "vehicle_owner_id": transaction_input.vehicle_owner_id,
            "contractor_id": transaction_input.contractor_id,
            "plant_id": transaction_input.plant_id,
            "petrol_pump_id": transaction_input.petrol_pump_id,
            "is_system_generated": transaction_input.is_system_generated,
        }
        # Only set date when supplied (else the model default `now` applies on
        # create, and the existing date is kept on update). A plain date is
        # promoted to a datetime so it stores correctly on Postgres.
        if transaction_input.date is not None:
            value = transaction_input.date
            if not isinstance(value, datetime):
                value = datetime.combine(value, time.min)
            kwargs["date"] = value
        return kwargs

    def _validate_transaction_input(self, transaction_input: TransactionInput):
        amount = _safe_amount(transaction_input.amount)
        if amount <= 0:
            raise ValidationError("Transaction amount must be greater than zero.")

        required_entity_fields = {
            "vehicle_payment": transaction_input.vehicle_id,
            "vehicle_receipt": transaction_input.vehicle_id,
            "vehicle_owner_payment": transaction_input.vehicle_owner_id,
            "vehicle_owner_receipt": transaction_input.vehicle_owner_id,
            "contractor_receipt": transaction_input.contractor_id,
            "contractor_payment": transaction_input.contractor_id,
            "plant_payment": transaction_input.plant_id,
            "plant_receipt": transaction_input.plant_id,
            "petrol_pump_payment": transaction_input.petrol_pump_id,
            "petrol_pump_receipt": transaction_input.petrol_pump_id,
        }
        required_entity = required_entity_fields.get(transaction_input.type, True)
        if required_entity is None or required_entity == 0:
            raise ValidationError("Select the linked account before posting this transaction.")

    def _apply_transaction_effect(self, transaction, reverse=False, apply_financial_effect=True):
        if not apply_financial_effect:
            return

        multiplier = -1 if reverse else 1
        company = self.company.get_singleton()
        amount = _safe_amount(transaction.amount) * multiplier

        if transaction.type == "initial_balance":
            company.balance = _safe_amount(company.balance) + amount
            return

        if transaction.type == "contractor_receipt":
            company.balance = _safe_amount(company.balance) + amount
            self._update_entity_balance("contractor", transaction.contractor_id or transaction.entity_id, -amount)
            return

        # Generic inverse of contractor_receipt: paying a contractor (advance/
        # refund). Money out; the contractor's receivable rises.
        if transaction.type == "contractor_payment":
            company.balance = _safe_amount(company.balance) - amount
            self._update_entity_balance("contractor", transaction.contractor_id or transaction.entity_id, amount)
            return

        # Generic inverse of plant_payment / petrol_pump_payment: money received
        # from a plant/pump (e.g. a refund). Money in; their payable rises.
        if transaction.type == "plant_receipt":
            company.balance = _safe_amount(company.balance) + amount
            self._update_entity_balance("plant", transaction.plant_id or transaction.entity_id, amount)
            return

        if transaction.type == "petrol_pump_receipt":
            company.balance = _safe_amount(company.balance) + amount
            self._update_entity_balance("petrol_pump", transaction.petrol_pump_id or transaction.entity_id, amount)
            return

        if transaction.type == "vehicle_payment":
            company.balance = _safe_amount(company.balance) - amount
            self._update_entity_balance("vehicle", transaction.vehicle_id or transaction.entity_id, -amount)
            vehicle = self.session.get(Vehicle, transaction.vehicle_id or transaction.entity_id) if (transaction.vehicle_id or transaction.entity_id) else None
            if vehicle and vehicle.owner_id:
                self._update_entity_balance("vehicle_owner", vehicle.owner_id, -amount)
            return

        # Generic inverse of vehicle_payment: money received against a vehicle.
        if transaction.type == "vehicle_receipt":
            company.balance = _safe_amount(company.balance) + amount
            self._update_entity_balance("vehicle", transaction.vehicle_id or transaction.entity_id, amount)
            vehicle = self.session.get(Vehicle, transaction.vehicle_id or transaction.entity_id) if (transaction.vehicle_id or transaction.entity_id) else None
            if vehicle and vehicle.owner_id:
                self._update_entity_balance("vehicle_owner", vehicle.owner_id, amount)
            return

        if transaction.type == "vehicle_owner_payment":
            company.balance = _safe_amount(company.balance) - amount
            self._update_entity_balance("vehicle_owner", transaction.vehicle_owner_id or transaction.entity_id, -amount)
            self._distribute_owner_payment(transaction.vehicle_owner_id or transaction.entity_id, amount)
            return

        if transaction.type == "vehicle_owner_receipt":
            # Owner pays us cash (e.g. for fuel taken from our pump). Money in,
            # and the owner's balance moves up (their negative balance reduces).
            company.balance = _safe_amount(company.balance) + amount
            self._update_entity_balance("vehicle_owner", transaction.vehicle_owner_id or transaction.entity_id, amount)
            self._distribute_owner_receipt(transaction.vehicle_owner_id or transaction.entity_id, amount)
            return

        if transaction.type == "plant_payment":
            company.balance = _safe_amount(company.balance) - amount
            self._update_entity_balance("plant", transaction.plant_id or transaction.entity_id, -amount)
            return

        if transaction.type == "petrol_pump_payment":
            company.balance = _safe_amount(company.balance) - amount
            self._update_entity_balance("petrol_pump", transaction.petrol_pump_id or transaction.entity_id, -amount)
            return

        if transaction.type in {"other_expense"}:
            company.balance = _safe_amount(company.balance) - amount

    def _adjust_bill_settlement(self, tx_type, bill_id, delta):
        """Mirror settlement transaction changes onto the linked bill.

        Bill settlements are the only transactions of these types that carry a
        reference_id, so this is a no-op for manually posted payments/receipts.
        Creation is excluded on purpose: settle_bill already bumps settled_amount.
        """
        if tx_type not in _SETTLEMENT_TYPES or not bill_id:
            return

        from ..models import Bill

        bill = self.session.get(Bill, bill_id)
        if bill is not None:
            bill.settled_amount = max(0.0, _safe_amount(bill.settled_amount) + delta)

    def _update_entity_balance(self, entity_type, entity_id, delta):
        if not entity_id:
            return

        model_map = {
            "contractor": __import__("al_rehman_goods_transport.models", fromlist=["Contractor"]).Contractor,
            "vehicle": __import__("al_rehman_goods_transport.models", fromlist=["Vehicle"]).Vehicle,
            "vehicle_owner": __import__("al_rehman_goods_transport.models", fromlist=["VehicleOwner"]).VehicleOwner,
            "plant": __import__("al_rehman_goods_transport.models", fromlist=["Plant"]).Plant,
            "petrol_pump": __import__("al_rehman_goods_transport.models", fromlist=["PetrolPump"]).PetrolPump,
        }
        model = model_map.get(entity_type)
        if model is None:
            return

        entity = self.session.get(model, entity_id)
        if entity:
            entity.balance = _safe_amount(getattr(entity, "balance", 0.0)) + delta

    def _distribute_owner_payment(self, owner_id, amount):
        if not owner_id:
            return

        from ..models import VehicleOwner

        owner = self.session.get(VehicleOwner, owner_id)
        if owner is None or not owner.vehicles:
            return

        positive_vehicles = [vehicle for vehicle in owner.vehicles if _safe_amount(vehicle.balance) > 0]
        total_balance = sum(_safe_amount(vehicle.balance) for vehicle in positive_vehicles)
        if total_balance <= 0:
            return

        remaining_amount = amount
        for index, vehicle in enumerate(positive_vehicles):
            if index == len(positive_vehicles) - 1:
                reduction = remaining_amount
            else:
                reduction = round(amount * (_safe_amount(vehicle.balance) / total_balance), 2)
                remaining_amount -= reduction
            vehicle.balance = _safe_amount(vehicle.balance) - reduction

    def _distribute_owner_receipt(self, owner_id, amount):
        """Owner paid us: credit it back to the vehicles that owe us (negative
        balances), proportionally, moving them toward zero."""
        if not owner_id:
            return

        from ..models import VehicleOwner

        owner = self.session.get(VehicleOwner, owner_id)
        if owner is None or not owner.vehicles:
            return

        negative_vehicles = [vehicle for vehicle in owner.vehicles if _safe_amount(vehicle.balance) < 0]
        total_owed = sum(-_safe_amount(vehicle.balance) for vehicle in negative_vehicles)
        if total_owed <= 0:
            return

        remaining_amount = amount
        for index, vehicle in enumerate(negative_vehicles):
            if index == len(negative_vehicles) - 1:
                addition = remaining_amount
            else:
                addition = round(amount * (-_safe_amount(vehicle.balance) / total_owed), 2)
                remaining_amount -= addition
            vehicle.balance = _safe_amount(vehicle.balance) + addition


def apply_transaction_effect(company, transaction):
    TransactionService()._apply_transaction_effect(transaction, reverse=False, apply_financial_effect=True)


def reverse_transaction_effect(company, transaction):
    TransactionService()._apply_transaction_effect(transaction, reverse=True, apply_financial_effect=True)
