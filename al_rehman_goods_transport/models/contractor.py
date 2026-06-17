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
    
    # Relationships with back_populates
    orders = db.relationship('Order', back_populates='contractor')
    sites = db.relationship('Site', back_populates='contractor')
    
    def __repr__(self):
        return f'<Contractor {self.name}>'
