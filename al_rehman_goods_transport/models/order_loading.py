from ..extensions import db


class OrderLoading(db.Model):
    __tablename__ = "order_loading"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    load_quantity = db.Column(db.Float, nullable=False, default=0.0)
    plant_id = db.Column(db.Integer, db.ForeignKey("plant.id"), nullable=True)
    plant_amount = db.Column(db.Float, nullable=False, default=0.0)
    loading_image = db.Column(db.String(255), nullable=True)
    bill_id = db.Column(db.Integer, db.ForeignKey("bill.id"), nullable=True)

    order = db.relationship("Order", back_populates="loadings")
    plant = db.relationship("Plant")
    bill = db.relationship("Bill", backref=db.backref("plant_loadings", lazy="dynamic"))

    def __repr__(self):
        return f"<OrderLoading order={self.order_id} quantity={self.load_quantity}>"
