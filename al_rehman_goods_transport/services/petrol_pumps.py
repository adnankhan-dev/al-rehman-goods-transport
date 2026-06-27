from ..extensions import db
from ..models import DieselEntry, PetrolPump, PetrolPumpPrice
from ..repositories import BillingRepository, PetrolPumpRepository
from .exceptions import ConflictError, NotFoundError, ValidationError
from .finance_summary import balance_summary
from .transactions import TransactionService


class PetrolPumpService:
    def __init__(self, session=None):
        self.session = session or db.session
        self.petrol_pumps = PetrolPumpRepository(self.session)
        self.billing = BillingRepository(self.session)
        self.transactions = TransactionService(self.session)

    def list_petrol_pumps(self):
        return self.petrol_pumps.list_all()

    def get_petrol_pump(self, petrol_pump_id):
        petrol_pump = self.petrol_pumps.get(petrol_pump_id)
        if petrol_pump is None:
            raise NotFoundError("Petrol pump not found.")
        return petrol_pump

    def create_petrol_pump(self, name):
        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValidationError("Petrol pump name is required.")
        if self.petrol_pumps.get_by_name(normalized_name):
            raise ConflictError("Petrol pump name already exists.")

        petrol_pump = PetrolPump(name=normalized_name)
        try:
            self.petrol_pumps.add(petrol_pump)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return petrol_pump

    def update_petrol_pump(self, petrol_pump_id, name):
        petrol_pump = self.get_petrol_pump(petrol_pump_id)
        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValidationError("Petrol pump name is required.")

        duplicate = self.petrol_pumps.get_by_name(normalized_name)
        if duplicate and duplicate.id != petrol_pump.id:
            raise ConflictError("Petrol pump name already exists.")

        petrol_pump.name = normalized_name
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return petrol_pump

    def delete_petrol_pump(self, petrol_pump_id):
        petrol_pump = self.get_petrol_pump(petrol_pump_id)
        if self.petrol_pumps.usage_count(petrol_pump.id):
            raise ValidationError("This petrol pump is already linked to diesel entries and cannot be deleted.")

        try:
            self.petrol_pumps.delete(petrol_pump)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def dashboard(self, petrol_pump_id):
        petrol_pump = self.get_petrol_pump(petrol_pump_id)
        diesel_entries = self.billing.petrol_pump_activity_rows(petrol_pump.id)
        standalone_entries = (
            DieselEntry.query
            .filter(DieselEntry.petrol_pump_id == petrol_pump.id)
            .order_by(DieselEntry.date.desc(), DieselEntry.id.desc())
            .all()
        )
        return {
            "petrol_pump": petrol_pump,
            "diesel_entries": diesel_entries,
            "standalone_entries": standalone_entries,
            "prices": self.list_prices(petrol_pump.id),
            "transactions": self.transactions.transactions_for_entity("petrol_pump", petrol_pump.id),
            "balance_summary": balance_summary("petrol_pump", petrol_pump.balance),
        }

    # --- Per-pump diesel prices (date-range) ---

    def list_prices(self, petrol_pump_id):
        return (
            self.session.query(PetrolPumpPrice)
            .filter(PetrolPumpPrice.petrol_pump_id == petrol_pump_id)
            .order_by(PetrolPumpPrice.effective_from.desc(), PetrolPumpPrice.id.desc())
            .all()
        )

    def add_price(self, petrol_pump_id, price, effective_from, effective_to=None, notes=None):
        self.get_petrol_pump(petrol_pump_id)  # ensures it exists
        try:
            price_value = float(price)
        except (TypeError, ValueError):
            raise ValidationError("Enter a valid price per litre.")
        if price_value <= 0:
            raise ValidationError("Price per litre must be greater than zero.")
        if not effective_from:
            raise ValidationError("Effective-from date is required.")
        if effective_to and effective_to < effective_from:
            raise ValidationError("Effective-to date cannot be before the effective-from date.")

        record = PetrolPumpPrice(
            petrol_pump_id=petrol_pump_id,
            price=price_value,
            effective_from=effective_from,
            effective_to=effective_to or None,
            notes=(notes or "").strip() or None,
        )
        try:
            self.session.add(record)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return record

    def delete_price(self, petrol_pump_id, price_id):
        record = self.session.get(PetrolPumpPrice, price_id)
        if record is None or record.petrol_pump_id != petrol_pump_id:
            raise NotFoundError("Price entry not found.")
        try:
            self.session.delete(record)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def price_for_date(self, petrol_pump_id, on_date):
        """The price effective for a pump on a given date (latest start wins)."""
        if on_date is None:
            return None
        candidates = [p for p in self.list_prices(petrol_pump_id) if p.covers_date(on_date)]
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.effective_from).price

    def prices_map(self):
        """{pump_id: [{from, to, price}, ...]} for client-side auto-fill."""
        result = {}
        for record in self.session.query(PetrolPumpPrice).all():
            result.setdefault(record.petrol_pump_id, []).append({
                "from": record.effective_from.isoformat(),
                "to": record.effective_to.isoformat() if record.effective_to else None,
                "price": record.price,
            })
        return result
