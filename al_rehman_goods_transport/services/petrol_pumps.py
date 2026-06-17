from ..extensions import db
from ..models import DieselEntry, PetrolPump
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
            "transactions": self.transactions.transactions_for_entity("petrol_pump", petrol_pump.id),
            "balance_summary": balance_summary("petrol_pump", petrol_pump.balance),
        }
