from datetime import UTC, datetime

from ..extensions import db


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


class Bill(db.Model):
    __tablename__ = "bill"

    id = db.Column(db.Integer, primary_key=True)
    bill_number = db.Column(db.String(50), unique=True, nullable=False)
    entity_type = db.Column(db.String(30), default="contractor", nullable=False)
    contractor_id = db.Column(db.Integer, db.ForeignKey("contractor.id"), nullable=True)
    plant_id = db.Column(db.Integer, db.ForeignKey("plant.id"), nullable=True)
    petrol_pump_id = db.Column(db.Integer, db.ForeignKey("petrol_pump.id"), nullable=True)
    vehicle_owner_id = db.Column(db.Integer, db.ForeignKey("vehicle_owner.id"), nullable=True)
    bill_date = db.Column(db.DateTime, default=utc_now, nullable=False)
    start_date = db.Column(db.DateTime, nullable=True)
    end_date = db.Column(db.DateTime, nullable=True)
    material_type = db.Column(db.String(100), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    total_amount = db.Column(db.Float, default=0.0, nullable=False)
    settled_amount = db.Column(db.Float, default=0.0, nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    contractor = db.relationship("Contractor", backref=db.backref("bills", lazy=True))
    plant = db.relationship("Plant", backref=db.backref("bills", lazy=True))
    petrol_pump = db.relationship("PetrolPump", backref=db.backref("bills", lazy=True))
    vehicle_owner = db.relationship("VehicleOwner", backref=db.backref("bills", lazy=True))
    orders = db.relationship("Order", back_populates="bill", foreign_keys="Order.bill_id")

    @property
    def entity_name(self):
        if self.entity_type == "contractor" and self.contractor:
            return self.contractor.name
        if self.entity_type == "plant" and self.plant:
            return self.plant.name
        if self.entity_type == "petrol_pump" and self.petrol_pump:
            return self.petrol_pump.name
        if self.entity_type == "vehicle_owner" and self.vehicle_owner:
            return self.vehicle_owner.name
        return "Unassigned"

    @property
    def outstanding_amount(self):
        return (self.total_amount or 0.0) - (self.settled_amount or 0.0)

    @property
    def order_count(self):
        return len(self.orders or [])

    def __repr__(self):
        return f"<Bill {self.bill_number}>"
