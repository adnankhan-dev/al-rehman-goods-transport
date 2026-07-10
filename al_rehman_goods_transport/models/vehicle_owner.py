from ..extensions import db


class VehicleOwner(db.Model):
    __tablename__ = "vehicle_owner"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    balance = db.Column(db.Float, default=0.0, nullable=False)
    # Pre-ERP carry-forward balance (before the June cutover).
    opening_balance = db.Column(db.Float, default=0.0, server_default="0")
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    vehicles = db.relationship("Vehicle", back_populates="owner")
    transactions = db.relationship("Transaction", back_populates="vehicle_owner")

    @property
    def effective_balance(self):
        return float(self.balance or 0.0) + float(self.opening_balance or 0.0)

    def __repr__(self):
        return f"<VehicleOwner {self.name}>"
