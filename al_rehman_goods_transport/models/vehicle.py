from ..extensions import db


class Vehicle(db.Model):
    __tablename__ = "vehicle"

    id = db.Column(db.Integer, primary_key=True)
    vehicle_number = db.Column(db.String(20), unique=True, nullable=False)
    owner_name = db.Column(db.String(100))
    owner_id = db.Column(db.Integer, db.ForeignKey("vehicle_owner.id"), nullable=True)
    vehicle_type = db.Column(db.String(50))
    capacity = db.Column(db.Float)
    insurance_details = db.Column(db.Text)
    fitness_certificate = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    balance = db.Column(db.Float, default=0.0)

    owner = db.relationship("VehicleOwner", back_populates="vehicles")
    orders = db.relationship("Order", back_populates="vehicle")

    @property
    def owner_display_name(self):
        if self.owner:
            return self.owner.name
        return self.owner_name or "Unassigned"

    def sync_owner_name(self):
        self.owner_name = self.owner.name if self.owner else (self.owner_name or None)

    def __repr__(self):
        return f"<Vehicle {self.vehicle_number}>"
