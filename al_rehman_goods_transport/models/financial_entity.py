from datetime import UTC, datetime

from ..extensions import db


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


# What kind of account a financial entity is. Drives how the ledger and the
# P&L treat money paid to it:
#   expense -> the payment IS a cost of doing business, hits the P&L
#   loan    -> the payment is a balance movement (they owe us / we owe them),
#              never a P&L expense
ENTITY_KINDS = {
    "expense": {
        "label": "Expense",
        "description": "Running costs — rent, repairs, salaries, utilities, services.",
        "is_expense": True,
    },
    "loan": {
        "label": "Loan",
        "description": "Money lent or borrowed. Moves the balance only, never profit.",
        "is_expense": False,
    },
}

ENTITY_KIND_CHOICES = [(key, meta["label"]) for key, meta in ENTITY_KINDS.items()]


class FinancialEntity(db.Model):
    __tablename__ = "financial_entity"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    entity_kind = db.Column(db.String(20), default="expense", server_default="expense", nullable=False)
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
    def kind_meta(self):
        return ENTITY_KINDS.get(self.entity_kind or "expense", ENTITY_KINDS["expense"])

    @property
    def kind_label(self):
        return self.kind_meta["label"]

    @property
    def is_expense_kind(self):
        return self.kind_meta["is_expense"]

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
