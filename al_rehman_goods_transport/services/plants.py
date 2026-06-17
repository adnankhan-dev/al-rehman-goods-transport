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
        related_loadings = self.billing.plant_activity_rows(plant_id, start_value, end_value, material_type)
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
