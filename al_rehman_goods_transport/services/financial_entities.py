from datetime import UTC, datetime

from ..extensions import db
from ..models import FinancialEntity, FinancialEntityTransaction, TRANSACTION_TYPES
from ..repositories import CompanyRepository
from .exceptions import NotFoundError, ValidationError


def _safe(value):
    return float(value or 0.0)


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


class FinancialEntityService:
    def __init__(self, session=None):
        self.session = session or db.session

    def list_entities(self):
        return self.session.query(FinancialEntity).order_by(FinancialEntity.name.asc()).all()

    def get_entity(self, entity_id):
        entity = self.session.get(FinancialEntity, entity_id)
        if entity is None:
            raise NotFoundError("Financial entity not found.")
        return entity

    def create_entity(self, name, phone=None, notes=None, initial_balance=0.0):
        name = (name or "").strip()
        if not name:
            raise ValidationError("Name is required.")
        existing = self.session.query(FinancialEntity).filter(FinancialEntity.name == name).first()
        if existing:
            raise ValidationError(f"A financial entity named '{name}' already exists.")
        entity = FinancialEntity(
            name=name,
            phone=(phone or "").strip() or None,
            notes=(notes or "").strip() or None,
            balance=_safe(initial_balance),
        )
        try:
            self.session.add(entity)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return entity

    def update_entity(self, entity_id, name, phone=None, notes=None):
        entity = self.get_entity(entity_id)
        name = (name or "").strip()
        if not name:
            raise ValidationError("Name is required.")
        duplicate = (
            self.session.query(FinancialEntity)
            .filter(FinancialEntity.name == name, FinancialEntity.id != entity_id)
            .first()
        )
        if duplicate:
            raise ValidationError(f"A financial entity named '{name}' already exists.")
        try:
            entity.name = name
            entity.phone = (phone or "").strip() or None
            entity.notes = (notes or "").strip() or None
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return entity

    def delete_entity(self, entity_id):
        entity = self.get_entity(entity_id)
        try:
            self.session.delete(entity)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def post_transaction(self, entity_id, transaction_type, amount, date=None, payment_method=None, reference=None, notes=None):
        entity = self.get_entity(entity_id)
        if transaction_type not in TRANSACTION_TYPES:
            raise ValidationError(f"Invalid transaction type: {transaction_type}")
        amount = _safe(amount)
        if amount <= 0:
            raise ValidationError("Amount must be greater than zero.")

        tx = FinancialEntityTransaction(
            financial_entity_id=entity_id,
            transaction_type=transaction_type,
            amount=amount,
            date=date or utc_now(),
            payment_method=(payment_method or "").strip() or None,
            reference=(reference or "").strip() or None,
            notes=(notes or "").strip() or None,
        )

        meta = TRANSACTION_TYPES[transaction_type]
        balance_delta = meta["balance_delta"] * amount

        try:
            self.session.add(tx)
            entity.balance = _safe(entity.balance) + balance_delta
            self._apply_company_cash_effect(meta.get("direction"), amount)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return tx

    def delete_transaction(self, transaction_id):
        tx = self.session.get(FinancialEntityTransaction, transaction_id)
        if tx is None:
            raise NotFoundError("Transaction not found.")
        entity = tx.entity
        meta = TRANSACTION_TYPES.get(tx.transaction_type, {})
        balance_delta = meta.get("balance_delta", 0) * _safe(tx.amount)
        try:
            entity.balance = _safe(entity.balance) - balance_delta
            self._apply_company_cash_effect(meta.get("direction"), -_safe(tx.amount))
            self.session.delete(tx)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def _apply_company_cash_effect(self, direction, amount):
        """Loans/receipts/expenses with external entities are real cash movement
        and must hit Company.balance, otherwise the ledger and the company
        balance disagree. Pass a negative amount to reverse."""
        if direction not in ("in", "out") or not amount:
            return
        company = CompanyRepository(self.session).get_singleton()
        delta = amount if direction == "in" else -amount
        company.balance = _safe(company.balance) + delta
