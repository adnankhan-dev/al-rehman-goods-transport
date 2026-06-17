from sqlalchemy import func

from ..extensions import db
from ..models import Material, Order


class MaterialRepository:
    def __init__(self, session=None):
        self.session = session or db.session

    def list_all(self):
        return self.session.query(Material).order_by(Material.name.asc()).all()

    def get(self, material_id):
        return self.session.get(Material, material_id)

    def get_by_name(self, name):
        normalized_name = (name or "").strip()
        if not normalized_name:
            return None
        return self.session.query(Material).filter(func.lower(Material.name) == normalized_name.lower()).first()

    def add(self, material):
        self.session.add(material)
        self.session.flush()
        return material

    def delete(self, material):
        self.session.delete(material)

    def usage_count(self, material_id):
        return self.session.query(Order).filter(Order.material_id == material_id).count()
