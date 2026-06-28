from ..extensions import db


class PetrolPump(db.Model):
    __tablename__ = "petrol_pump"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    balance = db.Column(db.Float, default=0.0, nullable=False)
    # Amount already owed to this pump before tracked activity began. Folded into
    # `balance` and shown as a "Previous Balance" line on statements and bills.
    opening_balance = db.Column(db.Float, default=0.0, nullable=False)

    diesel_entries = db.relationship("OrderDieselEntry", back_populates="petrol_pump")
    transactions = db.relationship("Transaction", back_populates="petrol_pump")

    def __repr__(self):
        return f"<PetrolPump {self.name}>"
