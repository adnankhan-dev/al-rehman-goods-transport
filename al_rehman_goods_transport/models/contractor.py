from ..extensions import db

class Contractor(db.Model):
    __tablename__ = "contractor"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    contact_person = db.Column(db.String(100))
    phone = db.Column(db.String(15))
    email = db.Column(db.String(100))
    address = db.Column(db.Text)
    payment_terms = db.Column(db.String(50))
    balance = db.Column(db.Float, default=0.0)
    # Pre-ERP carry-forward balance (before the June cutover); folded into the
    # running balance and shown as "Previous Balance" on statements and bills.
    opening_balance = db.Column(db.Float, default=0.0, server_default="0")

    # Relationships with back_populates
    orders = db.relationship('Order', back_populates='contractor')
    sites = db.relationship('Site', back_populates='contractor')

    @property
    def effective_balance(self):
        """Running balance including the pre-ERP opening carry-forward."""
        return float(self.balance or 0.0) + float(self.opening_balance or 0.0)

    def __repr__(self):
        return f'<Contractor {self.name}>'
