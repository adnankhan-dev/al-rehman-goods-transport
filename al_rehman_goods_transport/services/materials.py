from ..extensions import db
from ..models import Material
from ..repositories import MaterialRepository
from .exceptions import ConflictError, NotFoundError, ValidationError


class MaterialService:
    def __init__(self, session=None):
        self.session = session or db.session
        self.materials = MaterialRepository(self.session)

    def list_materials(self):
        return self.materials.list_all()

    def get_material(self, material_id):
        material = self.materials.get(material_id)
        if material is None:
            raise NotFoundError("Material not found.")
        return material

    def _normalize_unit(self, unit):
        normalized = (unit or "").strip().lower()
        return normalized if normalized in ("cft", "ton") else "cft"

    def create_material(self, name, unit="cft"):
        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValidationError("Material name is required.")
        if self.materials.get_by_name(normalized_name):
            raise ConflictError("Material name already exists.")

        material = Material(name=normalized_name, unit=self._normalize_unit(unit))
        try:
            self.materials.add(material)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return material

    def update_material(self, material_id, name, unit=None):
        material = self.get_material(material_id)
        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValidationError("Material name is required.")

        duplicate = self.materials.get_by_name(normalized_name)
        if duplicate and duplicate.id != material.id:
            raise ConflictError("Material name already exists.")

        material.name = normalized_name
        if unit is not None:
            material.unit = self._normalize_unit(unit)
        for order in material.orders:
            order.material_type = normalized_name

        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return material

    def delete_material(self, material_id):
        material = self.get_material(material_id)
        if self.materials.usage_count(material.id):
            raise ValidationError("This material is already linked to orders and cannot be deleted.")

        try:
            self.materials.delete(material)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
