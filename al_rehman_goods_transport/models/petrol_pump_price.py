from datetime import UTC, date, datetime

from sqlalchemy import Date

from ..extensions import db


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


class PetrolPumpPrice(db.Model):
    """Per-pump diesel price effective over a date range.

    Each petrol pump has its own price history; the price effective on a fuel
    entry's date is used to auto-calculate litres from amount (and vice versa)."""

    __tablename__ = "petrol_pump_price"

    id = db.Column(db.Integer, primary_key=True)
    petrol_pump_id = db.Column(db.Integer, db.ForeignKey("petrol_pump.id"), nullable=False)
    price = db.Column(db.Float, nullable=False)  # price per litre
    effective_from = db.Column(Date, nullable=False)
    effective_to = db.Column(Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)

    petrol_pump = db.relationship("PetrolPump", backref=db.backref("prices", lazy="dynamic"))

    def __repr__(self):
        return f"<PetrolPumpPrice pump={self.petrol_pump_id} {self.price}>"

    def covers_date(self, check_date):
        if check_date < self.effective_from:
            return False
        if self.effective_to and check_date > self.effective_to:
            return False
        return True

    @property
    def is_currently_active(self):
        return self.covers_date(date.today())
