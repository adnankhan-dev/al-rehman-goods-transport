from sqlalchemy import func

from ..extensions import db
from ..models import DieselEntry, OrderDieselEntry, PetrolPump


class PetrolPumpRepository:
    def __init__(self, session=None):
        self.session = session or db.session

    def list_all(self):
        return self.session.query(PetrolPump).order_by(PetrolPump.name.asc()).all()

    def get(self, petrol_pump_id):
        return self.session.get(PetrolPump, petrol_pump_id)

    def get_by_name(self, name):
        normalized_name = (name or "").strip()
        if not normalized_name:
            return None
        return self.session.query(PetrolPump).filter(func.lower(PetrolPump.name) == normalized_name.lower()).first()

    def add(self, petrol_pump):
        self.session.add(petrol_pump)
        self.session.flush()
        return petrol_pump

    def delete(self, petrol_pump):
        self.session.delete(petrol_pump)

    def usage_count(self, petrol_pump_id):
        legacy_count = self.session.query(OrderDieselEntry).filter(OrderDieselEntry.petrol_pump_id == petrol_pump_id).count()
        live_count = self.session.query(DieselEntry).filter(DieselEntry.petrol_pump_id == petrol_pump_id).count()
        return legacy_count + live_count
