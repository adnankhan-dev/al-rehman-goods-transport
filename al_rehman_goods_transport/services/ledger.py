from dataclasses import dataclass, field

from ..extensions import db
from ..models import Contractor, FinancialEntityTransaction, PetrolPump, Plant, Vehicle, VehicleOwner
from .billing import BillingService
from .finance_summary import balance_summary
from .transactions import TransactionService

# Transaction types that bring money INTO the company
_INFLOW_TX_TYPES = {
    "contractor_receipt", "vehicle_owner_receipt", "plant_receipt", "petrol_pump_receipt",
    "vehicle_receipt", "amount_received", "initial_balance",
}
# Transaction types that take money OUT of the company
_OUTFLOW_TX_TYPES = {
    "vehicle_owner_payment", "plant_payment", "petrol_pump_payment", "contractor_payment",
    "vehicle_advance", "vehicle_payment", "other_expense",
}


def _transaction_delta(tx_type: str, amount: float) -> float:
    if tx_type in _INFLOW_TX_TYPES:
        return +amount
    if tx_type in _OUTFLOW_TX_TYPES:
        return -amount
    return 0.0


def _fe_transaction_delta(fe_type: str, amount: float) -> float:
    if fe_type == "amount_received":
        return +amount
    if fe_type in ("loan_given", "expense_paid"):
        return -amount
    return 0.0


@dataclass
class LedgerEntry:
    kind: str
    id: int
    date: object
    entity_name: str
    entity_type: str
    amount: float
    status: str
    reference: str
    detail_route: str
    balance_delta: float = 0.0
    running_balance: float = field(default=0.0, compare=False)


class LedgerService:
    def __init__(self, billing_service=None, transaction_service=None):
        self.billing = billing_service or BillingService()
        self.transactions = transaction_service or TransactionService()

    def index_context(self, filters=None, page=1, per_page=50):
        from datetime import datetime

        from ..utils.pagination import paginate_list

        filters = filters or {}

        bills = self.billing.list_bills()
        transactions = self.transactions.list_transactions()
        fe_transactions = (
            db.session.query(FinancialEntityTransaction)
            .order_by(FinancialEntityTransaction.date.asc(), FinancialEntityTransaction.id.asc())
            .all()
        )

        all_entries = [
            LedgerEntry(
                kind="bill",
                id=bill.id,
                date=bill.bill_date,
                entity_name=bill.entity_name,
                entity_type=bill.entity_type,
                amount=bill.total_amount or 0.0,
                status="Pending" if bill.approval_status == "pending" else "Posted",
                reference=bill.bill_number,
                detail_route="bills.view_bill",
                balance_delta=0.0,
            )
            for bill in bills
        ] + [
            LedgerEntry(
                kind="transaction",
                id=transaction.id,
                date=transaction.date,
                entity_name=transaction.entity_name,
                entity_type=transaction.entity_type or "-",
                amount=transaction.amount or 0.0,
                status="System" if transaction.is_system_generated else "Posted",
                reference=transaction.reference or transaction.type_label,
                detail_route="transactions.view_transaction",
                balance_delta=_transaction_delta(transaction.type, transaction.amount or 0.0),
            )
            for transaction in transactions
        ] + [
            LedgerEntry(
                kind="ext. finance",
                id=tx.financial_entity_id,
                date=tx.date,
                entity_name=tx.entity.name if tx.entity else "Unknown",
                entity_type="financial entity",
                amount=tx.amount or 0.0,
                status=tx.type_short,
                reference=tx.reference or tx.transaction_type,
                detail_route="financial_entities.view",
                balance_delta=_fe_transaction_delta(tx.transaction_type, tx.amount or 0.0),
            )
            for tx in fe_transactions
        ]

        # Compute running company balance on the full unfiltered set (chronological)
        entries_chrono = sorted(all_entries, key=lambda x: (x.date, x.id))
        running = 0.0
        for entry in entries_chrono:
            running += entry.balance_delta
            entry.running_balance = running

        # The headline figure comes from the maintained Company.balance — the
        # single source of truth — not the recomputed running total, which only
        # covers flows that produced a ledger entry.
        from ..repositories import CompanyRepository

        company_balance = float(CompanyRepository(db.session).get_singleton().balance or 0.0)

        # Apply filters to produce the displayed subset
        ledger_entries = all_entries
        date_from = filters.get("date_from", "")
        date_to = filters.get("date_to", "")
        kind_filter = (filters.get("kind") or "").lower().strip()
        entity_type_filter = (filters.get("entity_type") or "").lower().strip()

        if date_from:
            try:
                dt_from = datetime.strptime(date_from, "%Y-%m-%d")
                ledger_entries = [e for e in ledger_entries if e.date >= dt_from]
            except ValueError:
                pass
        if date_to:
            try:
                dt_to = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
                ledger_entries = [e for e in ledger_entries if e.date <= dt_to]
            except ValueError:
                pass
        if kind_filter:
            ledger_entries = [e for e in ledger_entries if e.kind == kind_filter]
        if entity_type_filter:
            ledger_entries = [e for e in ledger_entries if e.entity_type.lower() == entity_type_filter]

        ledger_entries.sort(key=lambda x: (x.date, x.id), reverse=True)
        pagination = paginate_list(ledger_entries, page, per_page)

        # Balance position straight from the maintained entity balances (the
        # ledger is the single money record now that bill settlement is retired).
        contractor_receivables = sum(float(c.balance or 0) for c in Contractor.query.all())
        owner_payables = sum(float(o.balance or 0) for o in VehicleOwner.query.all())
        plant_payables = sum(float(p.balance or 0) for p in Plant.query.all())
        pump_payables = sum(float(p.balance or 0) for p in PetrolPump.query.all())
        net_position = contractor_receivables - owner_payables - plant_payables - pump_payables

        fe_tx_count = len(fe_transactions)
        fe_tx_amount = sum(tx.amount or 0.0 for tx in fe_transactions)

        return {
            "ledger_entries": pagination["items"],
            "ledger_pagination": pagination,
            "bill_count": len(bills),
            "transaction_count": len(transactions) + fe_tx_count,
            "open_bill_amount": sum(float(b.total_amount or 0) for b in bills),
            "posted_transaction_amount": sum(t.amount or 0.0 for t in transactions) + fe_tx_amount,
            "current_company_balance": company_balance,
            "balance_summary_panel": {
                "contractor_receivables": contractor_receivables,
                "owner_payables": owner_payables,
                "plant_payables": plant_payables,
                "pump_payables": pump_payables,
                "net_position": net_position,
            },
        }

    def create_context(self, mode, filter_state=None, selected_order_ids=None, selected_entry_ids=None, selected_loading_ids=None):
        filter_state = filter_state or {
            "entity_type": "contractor",
            "entity_id": None,
            "site_ids": [],
            "material_type": "",
            "start_date": None,
            "end_date": None,
            "notes": "",
        }

        bill_context = self.billing.bill_context(
            filter_state["entity_type"],
            filter_state["entity_id"],
            filter_state["site_ids"],
            filter_state["material_type"] or None,
            filter_state["start_date"],
            filter_state["end_date"],
        )
        filter_options = self.billing.filter_options(filter_state["entity_type"], filter_state["entity_id"])
        return {
            "mode": mode,
            "filter_state": filter_state,
            "selected_order_ids": selected_order_ids or [],
            "selected_entry_ids": selected_entry_ids or [],
            "selected_loading_ids": selected_loading_ids or [],
            "contractors": Contractor.query.order_by(Contractor.name.asc()).all(),
            "plants": Plant.query.order_by(Plant.name.asc()).all(),
            "petrol_pumps": PetrolPump.query.order_by(PetrolPump.name.asc()).all(),
            "vehicle_owners": VehicleOwner.query.order_by(VehicleOwner.name.asc()).all(),
            "vehicles": Vehicle.query.order_by(Vehicle.vehicle_number.asc()).all(),
            "available_sites": filter_options["sites"],
            "available_materials": filter_options["materials"],
            "entity_balance_summary": bill_context.get("entity_balance_summary") or balance_summary(filter_state["entity_type"], 0.0),
            **bill_context,
        }
