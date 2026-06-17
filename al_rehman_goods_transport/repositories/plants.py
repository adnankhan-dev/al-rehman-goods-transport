from sqlalchemy import func

from ..extensions import db
from ..models import Order, Plant


class PlantRepository:
    def __init__(self, session=None):
        self.session = session or db.session

    def list_all(self):
        return self.session.query(Plant).order_by(Plant.name.asc()).all()

    def get(self, plant_id):
        return self.session.get(Plant, plant_id)

    def add(self, plant):
        self.session.add(plant)
        self.session.flush()
        return plant

    def delete(self, plant):
        self.session.delete(plant)

    def related_orders(self, plant_id, material_type=None, start_date=None, end_date=None):
        query = (
            self.session.query(Order)
            .filter(Order.plant_id == plant_id, Order.status == "Completed")
            .order_by(Order.completion_date.desc(), Order.order_date.desc(), Order.id.desc())
        )
        if material_type:
            query = query.filter(Order.material_type == material_type)
        if start_date is not None:
            query = query.filter(func.coalesce(Order.completion_date, Order.order_date) >= start_date)
        if end_date is not None:
            query = query.filter(func.coalesce(Order.completion_date, Order.order_date) <= end_date)
        return query.all()
