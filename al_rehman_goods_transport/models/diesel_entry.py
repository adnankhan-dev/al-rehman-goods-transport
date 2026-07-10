from datetime import UTC, datetime

from sqlalchemy import Date

from ..extensions import db


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


class DieselEntry(db.Model):
    __tablename__ = 'diesel_entry'

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicle.id'), nullable=False)
    petrol_pump_id = db.Column(db.Integer, db.ForeignKey('petrol_pump.id'), nullable=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=True)
    date = db.Column(Date, nullable=False)
    litres = db.Column(db.Float, nullable=True)
    amount = db.Column(db.Float, nullable=False)
    receipt_number = db.Column(db.String(50), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    balance_applied = db.Column(db.Boolean, default=False, nullable=False)
    vehicle_balance_applied = db.Column(db.Boolean, default=False, nullable=False)
    bill_id = db.Column(db.Integer, db.ForeignKey('bill.id'), nullable=True)
    vehicle_owner_bill_id = db.Column(db.Integer, db.ForeignKey('bill.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)

    # Approval workflow: a new entry is 'pending' and applies NO pump/vehicle
    # balance and is hidden from the fuel log and statements until approved.
    # Default 'approved' so existing rows are unchanged.
    approval_status = db.Column(db.String(20), default="approved", server_default="approved", nullable=False)
    approved_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)

    vehicle = db.relationship('Vehicle', backref=db.backref('diesel_entries_log', lazy='dynamic'))
    petrol_pump = db.relationship('PetrolPump', backref=db.backref('diesel_entries_log', lazy='dynamic'))
    order = db.relationship('Order', backref=db.backref('diesel_entries_log', lazy='dynamic'))
    bill = db.relationship('Bill', foreign_keys=[bill_id], backref=db.backref('pump_diesel_entries', lazy='dynamic'))
    vehicle_owner_bill = db.relationship('Bill', foreign_keys=[vehicle_owner_bill_id], backref=db.backref('owner_diesel_entries', lazy='dynamic'))

    approved_by = db.relationship('User', foreign_keys=[approved_by_id], lazy='joined')

    @property
    def is_pending_approval(self):
        return self.approval_status == "pending"

    def __repr__(self):
        return f'<DieselEntry {self.id}: vehicle={self.vehicle_id} amount={self.amount}>'
