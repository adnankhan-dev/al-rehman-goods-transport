from datetime import UTC, datetime

from ..extensions import db


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


TRANSACTION_TYPES = {
    "loan_given": {
        "label": "Loan Given to Entity",
        "short": "Loan Given",
        "balance_delta": +1,   # entity_balance += amount (they owe us)
        "direction": "out",    # money left company
    },
    "amount_received": {
        "label": "Amount Received from Entity",
        "short": "Received",
        "balance_delta": -1,   # entity_balance -= amount (they owe us less / we owe them more)
        "direction": "in",     # money came to company
    },
    "expense_paid": {
        "label": "Expense / Service Payment",
        "short": "Expense Paid",
        "balance_delta": 0,    # no balance change
        "direction": "out",    # money left company
    },
}


class FinancialEntityTransaction(db.Model):
    __tablename__ = "financial_entity_transaction"

    id = db.Column(db.Integer, primary_key=True)
    financial_entity_id = db.Column(db.Integer, db.ForeignKey("financial_entity.id"), nullable=False)
    transaction_type = db.Column(db.String(30), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.DateTime, default=utc_now, nullable=False)
    payment_method = db.Column(db.String(30), nullable=True)
    reference = db.Column(db.String(100), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    entity = db.relationship("FinancialEntity", back_populates="transactions")

    @property
    def type_meta(self):
        return TRANSACTION_TYPES.get(self.transaction_type, {})

    @property
    def type_label(self):
        return self.type_meta.get("label", self.transaction_type)

    @property
    def type_short(self):
        return self.type_meta.get("short", self.transaction_type)

    @property
    def balance_delta(self):
        return self.type_meta.get("balance_delta", 0) * float(self.amount or 0)

    @property
    def direction(self):
        return self.type_meta.get("direction", "")

    def __repr__(self):
        return f"<FETx {self.transaction_type} {self.amount}>"
