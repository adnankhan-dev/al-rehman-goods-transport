from datetime import UTC, datetime

from ..extensions import db


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


class FinancialEntity(db.Model):
    __tablename__ = "financial_entity"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    phone = db.Column(db.String(30), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    balance = db.Column(db.Float, default=0.0, nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    transactions = db.relationship(
        "FinancialEntityTransaction",
        back_populates="entity",
        cascade="all, delete-orphan",
        order_by="FinancialEntityTransaction.date.desc()",
    )

    @property
    def balance_label(self):
        if self.balance > 0.005:
            return "They owe us"
        if self.balance < -0.005:
            return "We owe them"
        return "Settled"

    @property
    def total_loaned(self):
        return sum(t.amount for t in self.transactions if t.transaction_type == "loan_given")

    @property
    def total_received(self):
        return sum(t.amount for t in self.transactions if t.transaction_type == "amount_received")

    @property
    def total_expenses(self):
        return sum(t.amount for t in self.transactions if t.transaction_type == "expense_paid")

    def __repr__(self):
        return f"<FinancialEntity {self.name}>"
