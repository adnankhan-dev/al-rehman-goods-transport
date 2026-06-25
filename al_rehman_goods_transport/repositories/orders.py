from datetime import timedelta

from sqlalchemy import case, func

from ..extensions import db
from ..models import Contractor, Order, Site, Vehicle


class OrderRepository:
    def __init__(self, session=None):
        self.session = session or db.session

    def list_all(self):
        return self.session.query(Order).order_by(Order.order_date.desc(), Order.id.desc()).all()

    def list_filtered(self, filters):
        return self._filtered_query(filters).all()

    def list_filtered_page(self, filters, page, per_page):
        query = self._filtered_query(filters)
        total = query.count()
        items = query.offset(max(0, (page - 1)) * per_page).limit(per_page).all()
        return items, total

    def total_count(self):
        return self.session.query(func.count(Order.id)).scalar() or 0

    def filtered_pnl(self, filters):
        """Profit & loss over the FULL filtered set.

        Profit = revenue (contractor) - net vehicle amount - plant amount, where
        net vehicle = gross vehicle (qty * vehicle_rate) - commission. This mirrors
        Order.profit_amount() exactly (diesel/advance are payment methods inside
        the vehicle amount, not separate costs)."""
        delivered = func.coalesce(Order.delivered_quantity, Order.quantity)
        base = self._filtered_query(filters).order_by(None)
        revenue, gross_vehicle, commission, plant = base.with_entities(
            func.coalesce(func.sum(delivered * func.coalesce(Order.contractor_rate, 0.0)), 0.0),
            func.coalesce(func.sum(delivered * func.coalesce(Order.vehicle_rate, 0.0)), 0.0),
            func.coalesce(func.sum(func.coalesce(Order.commission, 0.0)), 0.0),
            func.coalesce(func.sum(func.coalesce(Order.plant_amount, 0.0)), 0.0),
        ).one()
        revenue = float(revenue or 0.0)
        net_vehicle = float(gross_vehicle or 0.0) - float(commission or 0.0)
        plant = float(plant or 0.0)
        return {
            "revenue": revenue,
            "net_vehicle": net_vehicle,
            "plant": plant,
            "profit": revenue - net_vehicle - plant,
        }

    def filtered_summary(self, filters):
        """Aggregates over the FULL filtered set (not just the current page)."""
        delivered = func.coalesce(Order.delivered_quantity, Order.quantity)
        base = self._filtered_query(filters).order_by(None)
        row = base.with_entities(
            func.count(Order.id),
            func.coalesce(func.sum(delivered), 0.0),
            func.coalesce(func.sum(delivered * func.coalesce(Order.contractor_rate, 0.0)), 0.0),
            func.coalesce(func.sum(case((Order.bill_id.isnot(None), 1), else_=0)), 0),
        ).one()
        total, total_delivered, total_amount, billed_count = row
        return {
            "total_trips": int(total or 0),
            "total_delivered": float(total_delivered or 0.0),
            "total_amount": float(total_amount or 0.0),
            "billed_count": int(billed_count or 0),
            "unbilled_count": int((total or 0) - (billed_count or 0)),
        }

    def _filtered_query(self, filters):
        query = self.session.query(Order)

        if filters.get("contractor_id"):
            query = query.filter(Order.contractor_id == filters["contractor_id"])
        # Multi-select filters (lists). Fall back to the legacy singular keys.
        site_ids = filters.get("site_ids") or ([filters["site_id"]] if filters.get("site_id") else [])
        if site_ids:
            query = query.filter(Order.site_id.in_(site_ids))
        from_site_ids = filters.get("from_site_ids") or ([filters["from_site_id"]] if filters.get("from_site_id") else [])
        if from_site_ids:
            query = query.filter(Order.from_site_id.in_(from_site_ids))
        material_ids = filters.get("material_ids") or ([filters["material_id"]] if filters.get("material_id") else [])
        if material_ids:
            query = query.filter(Order.material_id.in_(material_ids))
        if filters.get("vehicle_owner_id"):
            query = query.filter(
                Order.vehicle_id.in_(
                    self.session.query(Vehicle.id).filter(Vehicle.owner_id == filters["vehicle_owner_id"])
                )
            )
        if filters.get("entered_by_id"):
            query = query.filter(Order.created_by_id == filters["entered_by_id"])
        if filters.get("entry_date"):
            entry_start = filters["entry_date"]
            query = query.filter(
                Order.created_at >= entry_start,
                Order.created_at < (entry_start + timedelta(days=1)),
            )
        if filters.get("billing_status") == "billed":
            query = query.filter(Order.bill_id.is_not(None))
        elif filters.get("billing_status") == "unbilled":
            query = query.filter(Order.bill_id.is_(None))
        if filters.get("date_from"):
            query = query.filter(Order.order_date >= filters["date_from"])
        if filters.get("date_to"):
            query = query.filter(Order.order_date < (filters["date_to"] + timedelta(days=1)))
        if filters.get("search"):
            search_term = f"%{filters['search']}%"
            vehicle_ids = self.session.query(Vehicle.id).filter(Vehicle.vehicle_number.ilike(search_term))
            contractor_ids = self.session.query(Contractor.id).filter(Contractor.name.ilike(search_term))
            site_ids_q = self.session.query(Site.id).filter(Site.name.ilike(search_term))
            query = query.filter(
                Order.driver_name.ilike(search_term)
                | Order.builty_number.ilike(search_term)
                | Order.receipt_number.ilike(search_term)
                | Order.material_type.ilike(search_term)
                | Order.driver_contact.ilike(search_term)
                | Order.remarks.ilike(search_term)
                | Order.vehicle_id.in_(vehicle_ids)
                | Order.contractor_id.in_(contractor_ids)
                | Order.site_id.in_(site_ids_q)
                | Order.from_site_id.in_(site_ids_q)
            )

        return query.order_by(Order.order_date.desc(), Order.id.desc())

    def get(self, order_id):
        return self.session.get(Order, order_id)

    def add(self, order):
        self.session.add(order)
        self.session.flush()
        return order

    def delete(self, order):
        self.session.delete(order)

    def get_by_builty_number(self, builty_number, exclude_id=None):
        normalized = (builty_number or "").strip()
        if not normalized:
            return None

        query = self.session.query(Order).filter(Order.builty_number == normalized)
        if exclude_id is not None:
            query = query.filter(Order.id != exclude_id)
        return query.first()
