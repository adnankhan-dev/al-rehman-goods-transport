from datetime import timedelta

from sqlalchemy import and_, func, or_

from ..extensions import db
from ..models import Contractor, DieselEntry, FinancialEntity, Material, Order, OrderDieselEntry, OrderLoading, PetrolPump, Plant, Site, Vehicle, VehicleOwner


class ReportRepository:
    def __init__(self, session=None):
        self.session = session or db.session

    def completed_orders_between(self, start_date, end_date):
        return (
            self.session.query(Order)
            .filter(
                Order.status == "Completed",
                func.coalesce(Order.completion_date, Order.order_date).between(start_date, end_date),
            )
            .all()
        )

    def list_contractors(self):
        return self.session.query(Contractor).order_by(Contractor.name.asc()).all()

    def list_vehicles(self):
        return self.session.query(Vehicle).order_by(Vehicle.vehicle_number.asc()).all()

    def list_plants(self):
        return self.session.query(Plant).order_by(Plant.name.asc()).all()

    def list_sites(self):
        return self.session.query(Site).order_by(Site.name.asc()).all()

    def list_materials(self):
        return self.session.query(Material).order_by(Material.name.asc()).all()

    def list_petrol_pumps(self):
        return self.session.query(PetrolPump).order_by(PetrolPump.name.asc()).all()

    def list_vehicle_owners(self):
        return self.session.query(VehicleOwner).order_by(VehicleOwner.name.asc()).all()

    def list_financial_entities(self):
        return self.session.query(FinancialEntity).order_by(FinancialEntity.name.asc()).all()

    def filter_options(self):
        return {
            "contractors": self.list_contractors(),
            "sites": self.list_sites(),
            "from_sites": (
                self.session.query(Site)
                .filter(Site.is_business_site.is_(True))
                .order_by(Site.name.asc())
                .all()
            ),
            "materials": self.list_materials(),
            "vehicles": self.list_vehicles(),
            "plants": self.list_plants(),
            "petrol_pumps": self.list_petrol_pumps(),
        }

    def completed_orders_filtered(self, filters):
        query = self.session.query(Order).filter(Order.status == "Completed")
        query = self._apply_order_filters(query, filters)
        return query.order_by(Order.order_date.desc(), Order.id.desc()).all()

    def diesel_entries_filtered(self, filters):
        query = (
            self.session.query(OrderDieselEntry)
            .join(Order, Order.id == OrderDieselEntry.order_id)
            .filter(
                Order.status == "Completed",
                OrderDieselEntry.amount > 0,
            )
        )
        query = self._apply_order_filters(query, filters)
        if filters.get("petrol_pump_id"):
            query = query.filter(OrderDieselEntry.petrol_pump_id == filters["petrol_pump_id"])

        search_value = (filters.get("search") or "").strip()
        if search_value:
            search_term = f"%{search_value}%"
            query = query.filter(
                or_(
                    OrderDieselEntry.receipt_number.ilike(search_term),
                    Order.vehicle.has(Vehicle.vehicle_number.ilike(search_term)),
                    Order.contractor.has(Contractor.name.ilike(search_term)),
                )
            )

        return query.order_by(
            func.coalesce(Order.completion_date, Order.order_date).desc(),
            OrderDieselEntry.id.desc(),
        ).all()

    def module_diesel_totals_by_order(self, order_ids):
        """Sum of live DieselEntry amounts per linked order, batched."""
        if not order_ids:
            return {}
        rows = (
            self.session.query(DieselEntry.order_id, func.sum(DieselEntry.amount))
            .filter(DieselEntry.order_id.in_(order_ids))
            .group_by(DieselEntry.order_id)
            .all()
        )
        return {order_id: float(total or 0) for order_id, total in rows}

    def standalone_diesel_entries_filtered(self, filters):
        """Diesel module entries (live flow). Order-based filters apply via the
        optional linked order; entries without an order are excluded only when
        such a filter is active."""
        query = self.session.query(DieselEntry).filter(DieselEntry.approval_status == "approved")

        order_filter_keys = ("contractor_id", "site_id", "from_site_id", "material_id", "billing_status")
        if any(filters.get(key) for key in order_filter_keys):
            query = query.join(Order, Order.id == DieselEntry.order_id)
            if filters.get("contractor_id"):
                query = query.filter(Order.contractor_id == filters["contractor_id"])
            if filters.get("site_id"):
                query = query.filter(Order.site_id == filters["site_id"])
            if filters.get("from_site_id"):
                query = query.filter(Order.from_site_id == filters["from_site_id"])
            if filters.get("material_id"):
                query = query.filter(Order.material_id == filters["material_id"])
            if filters.get("billing_status") == "billed":
                query = query.filter(Order.bill_id.is_not(None))
            elif filters.get("billing_status") == "unbilled":
                query = query.filter(Order.bill_id.is_(None))

        if filters.get("vehicle_id"):
            query = query.filter(DieselEntry.vehicle_id == filters["vehicle_id"])
        if filters.get("petrol_pump_id"):
            query = query.filter(DieselEntry.petrol_pump_id == filters["petrol_pump_id"])
        if filters.get("date_from"):
            query = query.filter(DieselEntry.date >= filters["date_from"].date())
        if filters.get("date_to"):
            query = query.filter(DieselEntry.date <= filters["date_to"].date())

        search_value = (filters.get("search") or "").strip()
        if search_value:
            search_term = f"%{search_value}%"
            query = query.filter(
                or_(
                    DieselEntry.receipt_number.ilike(search_term),
                    DieselEntry.vehicle.has(Vehicle.vehicle_number.ilike(search_term)),
                )
            )

        return query.order_by(DieselEntry.date.desc(), DieselEntry.id.desc()).all()

    def plant_loadings_filtered(self, filters):
        query = (
            self.session.query(OrderLoading)
            .join(Order, Order.id == OrderLoading.order_id)
            .filter(
                Order.status == "Completed",
                OrderLoading.plant_id.is_not(None),
            )
        )
        query = self._apply_order_filters(query, filters)
        if filters.get("plant_id"):
            query = query.filter(OrderLoading.plant_id == filters["plant_id"])

        search_value = (filters.get("search") or "").strip()
        if search_value:
            search_term = f"%{search_value}%"
            query = query.filter(
                or_(
                    OrderLoading.plant.has(Plant.name.ilike(search_term)),
                    Order.vehicle.has(Vehicle.vehicle_number.ilike(search_term)),
                    Order.contractor.has(Contractor.name.ilike(search_term)),
                )
            )

        return query.order_by(
            func.coalesce(Order.completion_date, Order.order_date).desc(),
            OrderLoading.id.desc(),
        ).all()

    def company_vehicle_diesel_filtered(self, filters):
        """Fuel-log entries for vehicles held under a company-expense owner.

        These are our OWN vehicles, so their fuel is a running cost of the
        business rather than something deducted from a third-party owner's
        payable — it belongs in the P&L as an expense in its own right."""
        query = (
            self.session.query(DieselEntry)
            .join(Vehicle, Vehicle.id == DieselEntry.vehicle_id)
            .join(VehicleOwner, VehicleOwner.id == Vehicle.owner_id)
            .filter(
                DieselEntry.approval_status == "approved",
                VehicleOwner.is_company_expense.is_(True),
            )
        )
        if filters.get("vehicle_id"):
            query = query.filter(DieselEntry.vehicle_id == filters["vehicle_id"])
        if filters.get("petrol_pump_id"):
            query = query.filter(DieselEntry.petrol_pump_id == filters["petrol_pump_id"])
        if filters.get("date_from"):
            query = query.filter(DieselEntry.date >= filters["date_from"].date())
        if filters.get("date_to"):
            query = query.filter(DieselEntry.date <= filters["date_to"].date())
        return query.order_by(DieselEntry.date.desc(), DieselEntry.id.desc()).all()

    def overhead_expense_transactions(self, filters):
        """Approved ledger postings that are true business expenses.

        Covers 'Other Expense' entries and payments to Expense-kind financial
        entities (rent, repairs, salaries, services). Loan entities are
        excluded: lending money moves a balance, it does not consume profit."""
        from ..models import Transaction

        expense_entity_ids = (
            self.session.query(FinancialEntity.id).filter(FinancialEntity.entity_kind == "expense")
        )
        query = self.session.query(Transaction).filter(
            Transaction.approval_status == "approved",
            or_(
                Transaction.type == "other_expense",
                and_(
                    Transaction.type == "financial_entity_payment",
                    Transaction.financial_entity_id.in_(expense_entity_ids),
                ),
            ),
        )
        if filters.get("date_from"):
            query = query.filter(Transaction.date >= filters["date_from"])
        if filters.get("date_to"):
            query = query.filter(Transaction.date < (filters["date_to"] + timedelta(days=1)))
        return query.order_by(Transaction.date.desc(), Transaction.id.desc()).all()

    def _apply_order_filters(self, query, filters):
        if filters.get("contractor_id"):
            query = query.filter(Order.contractor_id == filters["contractor_id"])
        if filters.get("site_id"):
            query = query.filter(Order.site_id == filters["site_id"])
        if filters.get("from_site_id"):
            query = query.filter(Order.from_site_id == filters["from_site_id"])
        if filters.get("material_id"):
            query = query.filter(Order.material_id == filters["material_id"])
        if filters.get("vehicle_id"):
            query = query.filter(Order.vehicle_id == filters["vehicle_id"])
        if filters.get("billing_status") == "billed":
            query = query.filter(Order.bill_id.is_not(None))
        elif filters.get("billing_status") == "unbilled":
            query = query.filter(Order.bill_id.is_(None))
        if filters.get("date_from"):
            query = query.filter(func.coalesce(Order.completion_date, Order.order_date) >= filters["date_from"])
        if filters.get("date_to"):
            query = query.filter(func.coalesce(Order.completion_date, Order.order_date) < (filters["date_to"] + timedelta(days=1)))
        if filters.get("search"):
            search_term = f"%{filters['search']}%"
            query = query.filter(
                or_(
                    Order.driver_name.ilike(search_term),
                    Order.builty_number.ilike(search_term),
                    Order.receipt_number.ilike(search_term),
                    Order.material_type.ilike(search_term),
                    Order.vehicle.has(Vehicle.vehicle_number.ilike(search_term)),
                    Order.contractor.has(Contractor.name.ilike(search_term)),
                    Order.site.has(Site.name.ilike(search_term)),
                    Order.from_site.has(Site.name.ilike(search_term)),
                )
            )
        return query
