from ..extensions import db
from ..models import Transaction


class TransactionRepository:
    def __init__(self, session=None):
        self.session = session or db.session

    def list_all(self):
        return self.session.query(Transaction).order_by(Transaction.date.desc(), Transaction.id.desc()).all()

    def get(self, transaction_id):
        return self.session.get(Transaction, transaction_id)

    def add(self, transaction):
        self.session.add(transaction)
        self.session.flush()
        return transaction

    def delete(self, transaction):
        self.session.delete(transaction)

    def find_system_order_advance(self, order_id):
        return (
            self.session.query(Transaction)
            .filter(
                Transaction.type == "vehicle_advance",
                Transaction.reference_id == order_id,
                Transaction.is_system_generated.is_(True),
            )
            .first()
        )

    def list_for_entity(self, entity_type, entity_id):
        if not entity_type or not entity_id:
            return []
        return (
            self.session.query(Transaction)
            .filter(Transaction.entity_type == entity_type, Transaction.entity_id == entity_id)
            .order_by(Transaction.date.desc(), Transaction.id.desc())
            .all()
        )
