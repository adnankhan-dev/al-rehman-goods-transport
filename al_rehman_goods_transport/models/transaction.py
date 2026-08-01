from datetime import UTC, datetime

from ..extensions import db


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


class Transaction(db.Model):
    __tablename__ = "transaction"

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, default=utc_now, nullable=False)
    type = db.Column(db.String(50), nullable=False)
    entity_type = db.Column(db.String(50), nullable=True)
    entity_id = db.Column(db.Integer, nullable=True)
    reference_id = db.Column(db.Integer, nullable=True)
    amount = db.Column(db.Float, nullable=False)
    description = db.Column(db.Text)
    payment_method = db.Column(db.String(20))
    reference = db.Column(db.String(50))
    is_system_generated = db.Column(db.Boolean, default=False, nullable=False)

    # Approval workflow: a manually-posted transaction is 'pending' and applies
    # NO balance effect and is hidden from the ledger until approved. Default
    # 'approved' so existing rows and system-generated ones are unaffected.
    approval_status = db.Column(db.String(20), default="approved", server_default="approved", nullable=False)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)

    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicle.id"), nullable=True)
    vehicle_owner_id = db.Column(db.Integer, db.ForeignKey("vehicle_owner.id"), nullable=True)
    contractor_id = db.Column(db.Integer, db.ForeignKey("contractor.id"), nullable=True)
    plant_id = db.Column(db.Integer, db.ForeignKey("plant.id"), nullable=True)
    petrol_pump_id = db.Column(db.Integer, db.ForeignKey("petrol_pump.id"), nullable=True)
    financial_entity_id = db.Column(db.Integer, db.ForeignKey("financial_entity.id"), nullable=True)

    vehicle = db.relationship("Vehicle", backref=db.backref("transactions", lazy=True))
    vehicle_owner = db.relationship("VehicleOwner", back_populates="transactions")
    contractor = db.relationship("Contractor", backref=db.backref("transactions", lazy=True))
    plant = db.relationship("Plant", backref=db.backref("transactions", lazy=True))
    petrol_pump = db.relationship("PetrolPump", back_populates="transactions")
    financial_entity = db.relationship("FinancialEntity", backref=db.backref("ledger_transactions", lazy=True))

    @property
    def is_pending_approval(self):
        return self.approval_status == "pending"

    @property
    def entity_name(self):
        if self.vehicle:
            return self.vehicle.vehicle_number
        if self.vehicle_owner:
            return self.vehicle_owner.name
        if self.contractor:
            return self.contractor.name
        if self.plant:
            return self.plant.name
        if self.petrol_pump:
            return self.petrol_pump.name
        if self.financial_entity:
            return self.financial_entity.name
        return "-"

    @property
    def type_label(self):
        """Human wording for statements and bills: 'Received from <name>' /
        'Payment paid to <name>' instead of the raw transaction type."""
        transaction_type = self.type or ""
        name = self.entity_name
        has_name = name and name != "-"
        if transaction_type == "previous_balance":
            return f"Previous balance — {name}" if has_name else "Previous balance"
        if transaction_type == "vehicle_advance":
            return f"Advance paid to {name}" if has_name else "Vehicle advance"
        if transaction_type == "initial_balance":
            return "Initial balance"
        if transaction_type == "other_expense":
            return "Other expense"
        if transaction_type.endswith("_receipt") or transaction_type == "receipt":
            return f"Received from {name}" if has_name else "Received"
        if transaction_type.endswith("_payment") or transaction_type == "payment":
            return f"Payment paid to {name}" if has_name else "Payment paid"
        return transaction_type.replace("_", " ").title()

    def __repr__(self):
        return f"<Transaction {self.type} {self.amount}>"
