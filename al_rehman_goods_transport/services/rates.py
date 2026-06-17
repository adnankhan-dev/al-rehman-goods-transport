from datetime import date

from ..extensions import db
from ..models import Contractor, ContractorRate, Material, Site
from .exceptions import NotFoundError


class RateService:
    def __init__(self, session=None):
        self.session = session or db.session

    def list_rates(self, contractor_id=None, site_id=None, from_site_id=None, material_id=None):
        query = ContractorRate.query
        if contractor_id:
            query = query.filter(ContractorRate.contractor_id == contractor_id)
        if site_id:
            query = query.filter(ContractorRate.site_id == site_id)
        if from_site_id:
            query = query.filter(ContractorRate.from_site_id == from_site_id)
        if material_id:
            query = query.filter(ContractorRate.material_id == material_id)
        return query.order_by(ContractorRate.effective_from.desc()).all()

    def get_rate(self, rate_id):
        rate = self.session.get(ContractorRate, rate_id)
        if rate is None:
            raise NotFoundError("Rate not found.")
        return rate

    def create_rate(self, data):
        rate = ContractorRate(**data)
        self.session.add(rate)
        self.session.commit()
        return rate

    def update_rate(self, rate_id, data):
        rate = self.get_rate(rate_id)
        for key, value in data.items():
            setattr(rate, key, value)
        self.session.commit()
        return rate

    def delete_rate(self, rate_id):
        rate = self.get_rate(rate_id)
        self.session.delete(rate)
        self.session.commit()

    def find_applicable_rate(self, contractor_id, site_id, from_site_id=None, material_id=None, check_date=None):
        """
        Return the best applicable rate for the given parameters on check_date.

        A scoped rate (one that fixes a from_site and/or material) is only
        excluded when the caller has selected a *conflicting* value. When the
        from_site or material is still unselected (None), it is treated as a
        wildcard so the rate still surfaces — this lets the order form suggest a
        saved rate as soon as the contractor and to-site are chosen, before the
        optional from_site/material are picked.

        Tie-breaking (best wins): an exact from_site match scores +2 and an exact
        material match +1, so a fully matching rate always beats a partial one.
        When two rates tie, a general (unconstrained) rate is preferred over a
        scoped-but-unmatched rate so it stays a safe default.
        """
        if check_date is None:
            check_date = date.today()

        candidates = (
            ContractorRate.query
            .filter(
                ContractorRate.contractor_id == contractor_id,
                ContractorRate.site_id == site_id,
                ContractorRate.effective_from <= check_date,
            )
            .filter(
                (ContractorRate.effective_to == None) | (ContractorRate.effective_to >= check_date)
            )
            .all()
        )

        best = None
        best_key = None

        for rate in candidates:
            # Exclude only on a real conflict: the caller picked a value that
            # differs from the rate's constraint. An unselected (None) input
            # does not conflict.
            if from_site_id and rate.from_site_id and rate.from_site_id != from_site_id:
                continue
            if material_id and rate.material_id and rate.material_id != material_id:
                continue

            match_score = 0
            if rate.from_site_id and rate.from_site_id == from_site_id:
                match_score += 2
            if rate.material_id and rate.material_id == material_id:
                match_score += 1

            is_general = 1 if not rate.from_site_id and not rate.material_id else 0
            key = (match_score, is_general)
            if best_key is None or key > best_key:
                best_key = key
                best = rate

        return best

    def build_form_choices(self):
        contractors = Contractor.query.order_by(Contractor.name).all()
        sites = Site.query.filter_by(is_business_site=False, is_archived=False).order_by(Site.name).all()
        from_sites = Site.query.filter_by(is_business_site=True, is_archived=False).order_by(Site.name).all()
        materials = Material.query.order_by(Material.name).all()
        return {
            "contractor_choices": [(0, "Select Contractor")] + [(c.id, c.name) for c in contractors],
            "site_choices": [(0, "Select To Site")] + [(s.id, s.name) for s in sites],
            "from_site_choices": [(0, "From Site (Optional)")] + [(s.id, s.name) for s in from_sites],
            "material_choices": [(0, "Any Material")] + [(m.id, m.name) for m in materials],
        }
