from ..extensions import db

class Plant(db.Model):
    __tablename__ = "plant"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    address = db.Column(db.Text)
    contact_person = db.Column(db.String(100))
    phone = db.Column(db.String(15))
    payment_terms = db.Column(db.String(50))
    balance = db.Column(db.Float, default=0.0)
    # Pre-ERP carry-forward balance (before the June cutover).
    opening_balance = db.Column(db.Float, default=0.0, server_default="0")

    # Relationship with back_populates
    orders = db.relationship('Order', back_populates='plant')

    @property
    def effective_balance(self):
        return float(self.balance or 0.0) + float(self.opening_balance or 0.0)

    def __repr__(self):
        return f'<Plant {self.name}>'
