from ..extensions import db

class Site(db.Model):
    __tablename__ = "site"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    address = db.Column(db.Text)
    contact_person = db.Column(db.String(100))
    phone = db.Column(db.String(50))
    contractor_id = db.Column(db.Integer, db.ForeignKey('contractor.id'), nullable=True)
    is_business_site = db.Column(db.Boolean, default=False, nullable=False)
    is_archived = db.Column(db.Boolean, default=False, nullable=False)
    
    # Relationships with back_populates
    orders = db.relationship('Order', back_populates='site', foreign_keys='Order.site_id')
    contractor = db.relationship('Contractor', back_populates='sites')
    
    def __repr__(self):
        return f'<Site {self.name}>'
