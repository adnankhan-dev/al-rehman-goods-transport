from sqlalchemy import Index, text

from ..extensions import db


class OrderDieselEntry(db.Model):
    __tablename__ = "order_diesel_entry"
    __table_args__ = (
        Index(
            "uq_diesel_receipt_per_pump",
            "petrol_pump_id",
            "receipt_number",
            unique=True,
            sqlite_where=text("petrol_pump_id IS NOT NULL AND receipt_number IS NOT NULL AND receipt_number != ''"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False, default=0.0)
    litres = db.Column(db.Float, nullable=True)
    receipt_number = db.Column(db.String(50), nullable=True)
    receipt_image = db.Column(db.String(255), nullable=True)
    petrol_pump_id = db.Column(db.Integer, db.ForeignKey("petrol_pump.id"), nullable=True)

    order = db.relationship("Order", back_populates="diesel_entries")
    petrol_pump = db.relationship("PetrolPump", back_populates="diesel_entries")

    def __repr__(self):
        return f"<OrderDieselEntry order={self.order_id} amount={self.amount}>"
