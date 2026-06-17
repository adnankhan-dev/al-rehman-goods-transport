from ..extensions import db
from ..models import Contractor, Material, PetrolPump, Plant, Site, Vehicle


class LookupRepository:
    def __init__(self, session=None):
        self.session = session or db.session

    def list_vehicles(self):
        return self.session.query(Vehicle).order_by(Vehicle.vehicle_number.asc()).all()

    def list_contractors(self):
        return self.session.query(Contractor).order_by(Contractor.name.asc()).all()

    def list_sites(self, contractor_id=None, business_only=False, include_archived=False):
        query = self.session.query(Site)
        if not include_archived:
            query = query.filter(Site.is_archived.is_(False))
        if business_only:
            query = query.filter(Site.is_business_site.is_(True))
        elif contractor_id:
            query = query.filter(Site.contractor_id == contractor_id)
        return query.order_by(Site.name.asc()).all()

    def list_plants(self):
        return self.session.query(Plant).order_by(Plant.name.asc()).all()

    def list_materials(self):
        return self.session.query(Material).order_by(Material.name.asc()).all()

    def list_petrol_pumps(self):
        return self.session.query(PetrolPump).order_by(PetrolPump.name.asc()).all()

    def get_material(self, material_id):
        return self.session.get(Material, material_id)

    def get_site(self, site_id):
        return self.session.get(Site, site_id)

    def get_petrol_pump(self, petrol_pump_id):
        return self.session.get(PetrolPump, petrol_pump_id)

    def get_plant(self, plant_id):
        return self.session.get(Plant, plant_id)
