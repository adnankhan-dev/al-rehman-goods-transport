from datetime import UTC, datetime

from sqlalchemy import Date

from ..extensions import db


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


class ContractorRate(db.Model):
    __tablename__ = 'contractor_rate'

    id = db.Column(db.Integer, primary_key=True)
    contractor_id = db.Column(db.Integer, db.ForeignKey('contractor.id'), nullable=False)
    site_id = db.Column(db.Integer, db.ForeignKey('site.id'), nullable=False)
    from_site_id = db.Column(db.Integer, db.ForeignKey('site.id'), nullable=True)
    material_id = db.Column(db.Integer, db.ForeignKey('material.id'), nullable=True)
    # NULL = rate applies to any vehicle owner on this route.
    vehicle_owner_id = db.Column(db.Integer, db.ForeignKey('vehicle_owner.id'), nullable=True)
    unit = db.Column(db.String(10), default='cft', nullable=False)
    rate = db.Column(db.Float, nullable=False)
    vehicle_rate = db.Column(db.Float, nullable=True)
    effective_from = db.Column(Date, nullable=False)
    effective_to = db.Column(Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)

    contractor = db.relationship('Contractor', backref=db.backref('rates', lazy='dynamic'))
    site = db.relationship('Site', foreign_keys=[site_id])
    from_site = db.relationship('Site', foreign_keys=[from_site_id])
    material = db.relationship('Material', backref=db.backref('rates', lazy='dynamic'))
    vehicle_owner = db.relationship('VehicleOwner', backref=db.backref('rates', lazy='dynamic'))

    def __repr__(self):
        return f'<ContractorRate {self.id}: {self.rate}>'

    @property
    def is_currently_active(self):
        from datetime import date
        today = date.today()
        return self.covers_date(today)

    def covers_date(self, check_date):
        if check_date < self.effective_from:
            return False
        if self.effective_to and check_date > self.effective_to:
            return False
        return True
