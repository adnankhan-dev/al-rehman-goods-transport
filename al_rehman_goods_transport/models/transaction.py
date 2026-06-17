from datetime import UTC, datetime

from ..extensions import db


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


class Transaction(db.Model):
    __tablename__ = "transaction"

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, default=utc_now, nullable=False)
    type = db.Column(db.String(50), nullable=False)
    entity_type = db.Column(db.String(50), nullable=True)
    entity_id = db.Column(db.Integer, nullable=True)
    reference_id = db.Column(db.Integer, nullable=True)
    amount = db.Column(db.Float, nullable=False)
    description = db.Column(db.Text)
    payment_method = db.Column(db.String(20))
    reference = db.Column(db.String(50))
    is_system_generated = db.Column(db.Boolean, default=False, nullable=False)

    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicle.id"), nullable=True)
    vehicle_owner_id = db.Column(db.Integer, db.ForeignKey("vehicle_owner.id"), nullable=True)
    contractor_id = db.Column(db.Integer, db.ForeignKey("contractor.id"), nullable=True)
    plant_id = db.Column(db.Integer, db.ForeignKey("plant.id"), nullable=True)
    petrol_pump_id = db.Column(db.Integer, db.ForeignKey("petrol_pump.id"), nullable=True)

    vehicle = db.relationship("Vehicle", backref=db.backref("transactions", lazy=True))
    vehicle_owner = db.relationship("VehicleOwner", back_populates="transactions")
    contractor = db.relationship("Contractor", backref=db.backref("transactions", lazy=True))
    plant = db.relationship("Plant", backref=db.backref("transactions", lazy=True))
    petrol_pump = db.relationship("PetrolPump", back_populates="transactions")

    @property
    def entity_name(self):
        if self.vehicle:
            return self.vehicle.vehicle_number
        if self.vehicle_owner:
            return self.vehicle_owner.name
        if self.contractor:
            return self.contractor.name
        if self.plant:
            return self.plant.name
        if self.petrol_pump:
            return self.petrol_pump.name
        return "-"

    def __repr__(self):
        return f"<Transaction {self.type} {self.amount}>"
