from datetime import datetime, timedelta

from ..extensions import db
from ..models import OrderLoading, Plant
from ..repositories import BillingRepository, PlantRepository
from .exceptions import NotFoundError, ValidationError
from .finance_summary import balance_summary
from .transactions import TransactionService


class PlantService:
    def __init__(self, session=None):
        self.session = session or db.session
        self.plants = PlantRepository(self.session)
        self.billing = BillingRepository(self.session)
        self.transactions = TransactionService(self.session)

    def list_plants(self):
        return self.plants.list_all()

    def get_plant(self, plant_id):
        plant = self.plants.get(plant_id)
        if plant is None:
            raise NotFoundError("Plant not found.")
        return plant

    def create_plant(self, **kwargs):
        plant = Plant(**kwargs)
        try:
            self.plants.add(plant)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return plant

    def update_plant(self, plant_id, **kwargs):
        plant = self.get_plant(plant_id)
        for key, value in kwargs.items():
            setattr(plant, key, value)
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return plant

    def delete_plant(self, plant_id):
        plant = self.get_plant(plant_id)
        has_loadings = self.session.query(OrderLoading.id).filter(OrderLoading.plant_id == plant_id).first() is not None
        if plant.orders or plant.bills or plant.transactions or has_loadings:
            raise ValidationError("This plant has orders, loadings, bills, or transactions and cannot be deleted.")
        try:
            self.plants.delete(plant)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def plant_dashboard(self, plant_id, material_type=None, start_date=None, end_date=None):
        plant = self.get_plant(plant_id)
        start_value = datetime.strptime(start_date, "%Y-%m-%d") if start_date else None
        end_value = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1) if end_date else None
        # Show every order that used this plant, even when no plant amount was
        # entered (previously such orders were hidden by a plant_amount > 0 filter).
        related_loadings = self.billing.plant_loadings_all(plant_id, start_value, end_value, material_type)
        available_materials = sorted({loading.order.material_type for loading in related_loadings if loading.order and loading.order.material_type} or {order.material_type for order in plant.orders if order.material_type})
        return {
            "plant": plant,
            "related_loadings": related_loadings,
            "available_materials": available_materials,
            "selected_material": material_type or "",
            "selected_start_date": start_date or "",
            "selected_end_date": end_date or "",
            "total_loaded_amount": sum(loading.plant_amount or 0 for loading in related_loadings),
            "transactions": self.transactions.transactions_for_entity("plant", plant.id),
            "balance_summary": balance_summary("plant", plant.balance),
        }

    def statement(self, plant_id, date_from=None, date_to=None):
        """Printable plant statement (mirrors the pump statement): loadings we
        owe the plant for, payments made, and net payable. With a date range it
        is a running statement (Previous Balance carries prior activity)."""
        plant = self.get_plant(plant_id)
        loadings_all = self.billing.plant_activity_rows(plant_id)
        payments_all = [
            t for t in self.transactions.transactions_for_entity("plant", plant.id)
            if t.type == "plant_payment"
        ]

        def _as_date(value):
            return value.date() if hasattr(value, "date") else value

        def load_date(loading):
            order = loading.order
            raw = (order.completion_date or order.order_date) if order else None
            return _as_date(raw) if raw else None

        def in_period(day):
            if day is None:
                return date_from is None and date_to is None
            if date_from and day < date_from:
                return False
            if date_to and day > date_to:
                return False
            return True

        def before_period(day):
            return date_from is not None and day is not None and day < date_from

        loadings = [l for l in loadings_all if in_period(load_date(l))]
        payments = [t for t in payments_all if in_period(_as_date(t.date))]
        total_charges = sum(float(l.plant_amount or 0) for l in loadings)

        # Previous balance and period receipts/payments from the shared
        # financials engine (history + backdated previous-balance entries).
        from .financials import entity_period_financials

        financial = entity_period_financials(
            "plant",
            plant.id,
            start_date=date_from,
            end_date=date_to,
            period_activity_total=total_charges,
            session=self.session,
        )

        return {
            "plant": plant,
            "loadings": loadings,
            "payments": payments,
            "financial": financial,
            "opening_balance": financial["previous_balance"],
            "total_charges": total_charges,
            "payments_total": financial["payments_total"],
            "net_payable": financial["current_total"],
            "period": {
                "date_from": date_from.strftime("%Y-%m-%d") if date_from else None,
                "date_to": date_to.strftime("%Y-%m-%d") if date_to else None,
            },
            "balance_summary": balance_summary("plant", plant.balance),
        }
