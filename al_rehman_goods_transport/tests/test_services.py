import os
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

PACKAGE_PARENT = Path(__file__).resolve().parents[2]
TEST_DB_PATH = Path(__file__).resolve().parent / "_test_app.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"
os.environ["SECRET_KEY"] = "test-secret"

if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

from al_rehman_goods_transport import db
from al_rehman_goods_transport.core.database import Base, engine  # engine rebound to the test DB in tests/__init__.py
from al_rehman_goods_transport.models import AppSetting, Bill, Company, Contractor, DieselEntry, Material, Order, OrderDieselEntry, PetrolPump, Plant, Site, Transaction, Vehicle, VehicleOwner
from al_rehman_goods_transport.services import BillingService, DieselService, OrderService, ReportService, SettingsService, TransactionInput, TransactionService, ValidationError
from al_rehman_goods_transport.services.order_finance import (
    apply_completed_order_balances,
    apply_order_creation_balances,
    apply_order_update_balances,
)
from al_rehman_goods_transport.services.billing import create_contractor_bill
from al_rehman_goods_transport.services.dashboard import get_dashboard_metrics
from al_rehman_goods_transport.services.financial_entities import FinancialEntityService
from al_rehman_goods_transport.services.orders import OrderInput
from al_rehman_goods_transport.services.reconciliation import ReconciliationService
from al_rehman_goods_transport.services.rates import RateService
from al_rehman_goods_transport.models import ContractorRate


class OrderFinanceServiceTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

        contractor = Contractor(name="Contractor One")
        material = Material(name="Sand")
        owner = VehicleOwner(name="Owner One")
        vehicle_one = Vehicle(vehicle_number="ABC-001")
        vehicle_two = Vehicle(vehicle_number="ABC-002")
        pump = PetrolPump(name="Pump One")
        plant = Plant(name="Plant One")
        db.session.add_all([contractor, material, owner, vehicle_one, vehicle_two, pump, plant])
        db.session.flush()
        vehicle_one.owner_id = owner.id
        vehicle_one.owner_name = owner.name
        vehicle_two.owner_id = owner.id
        vehicle_two.owner_name = owner.name

        site = Site(name="Site One", contractor_id=contractor.id)
        from_site = Site(name="Origin Site", contractor_id=None, is_business_site=True)
        diesel_setting = AppSetting(key=SettingsService.DIESEL_RATE_KEY, value="250.00")
        db.session.add_all([site, from_site, diesel_setting])
        db.session.commit()

        self.contractor_id = contractor.id
        self.vehicle_one_id = vehicle_one.id
        self.vehicle_two_id = vehicle_two.id
        self.owner_id = owner.id
        self.pump_id = pump.id
        self.plant_id = plant.id
        self.site_id = site.id
        self.from_site_id = from_site.id
        self.material_id = material.id

    def tearDown(self):
        db.session.remove()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        if TEST_DB_PATH.exists():
            TEST_DB_PATH.unlink()

    def test_order_lifecycle_updates_balances_consistently(self):
        order = Order(
            vehicle_id=self.vehicle_one_id,
            contractor_id=self.contractor_id,
            site_id=self.site_id,
            driver_name="Driver",
            material_id=self.material_id,
            material_type="Sand",
            quantity=100,
            delivered_quantity=100,
            advance_amount=200,
            diesel_amount=50,
            contractor_rate=15,
            vehicle_rate=10,
            plant_id=self.plant_id,
            plant_amount=100,
            commission=30,
            status="Completed",
        )
        order.diesel_entries = [OrderDieselEntry(amount=50)]
        db.session.add(order)
        apply_order_creation_balances(order)
        db.session.commit()

        company = Company.query.first()
        vehicle_one = db.session.get(Vehicle, self.vehicle_one_id)
        contractor = db.session.get(Contractor, self.contractor_id)
        plant = db.session.get(Plant, self.plant_id)
        self.assertEqual(company.balance, -200)
        self.assertEqual(vehicle_one.balance, 720)
        self.assertEqual(contractor.balance, 1500)
        self.assertEqual(plant.balance, 100)

        order.vehicle_id = self.vehicle_two_id
        order.advance_amount = 300
        order.diesel_amount = 40
        order.diesel_entries[0].amount = 40
        apply_order_update_balances(order, self.vehicle_one_id, 200, 50)
        db.session.commit()

        vehicle_one = db.session.get(Vehicle, self.vehicle_one_id)
        vehicle_two = db.session.get(Vehicle, self.vehicle_two_id)
        company = Company.query.first()
        contractor = db.session.get(Contractor, self.contractor_id)
        plant = db.session.get(Plant, self.plant_id)
        self.assertEqual(vehicle_one.balance, 0)
        self.assertEqual(vehicle_two.balance, 630)
        self.assertEqual(company.balance, -300)
        self.assertEqual(contractor.balance, 1500)
        self.assertEqual(plant.balance, 100)

        apply_completed_order_balances(order)
        db.session.commit()
        self.assertEqual(db.session.get(Vehicle, self.vehicle_two_id).balance, 1260)

    def test_order_service_creates_advance_transaction_and_creditor_balances(self):
        service = OrderService()
        order_input = service.input_from_form(
            type("FormStub", (), {
                "vehicle_id": type("Field", (), {"data": self.vehicle_one_id})(),
                "order_date": type("Field", (), {"data": date(2026, 4, 11)})(),
                "driver_name": type("Field", (), {"data": "Driver"})(),
                "contractor_id": type("Field", (), {"data": self.contractor_id})(),
                "site_id": type("Field", (), {"data": self.site_id})(),
                "from_site_id": type("Field", (), {"data": self.from_site_id})(),
                "material_id": type("Field", (), {"data": self.material_id})(),
                "load_quantity": type("Field", (), {"data": 100})(),
                "unit": type("Field", (), {"data": "cft"})(),
                "advance_amount": type("Field", (), {"data": 250})(),
                "builty_number": type("Field", (), {"data": "B-100"})(),
                "receipt_number": type("Field", (), {"data": "DR-100"})(),
                "delivered_quantity": type("Field", (), {"data": 100})(),
                "vehicle_rate": type("Field", (), {"data": 10})(),
                "contractor_rate": type("Field", (), {"data": 15})(),
                "plant_id": type("Field", (), {"data": self.plant_id})(),
                "plant_amount": type("Field", (), {"data": 90})(),
                "commission": type("Field", (), {"data": 10})(),
                "remarks": type("Field", (), {"data": "Night dispatch"})(),
            }),
            {"loading_image": None, "delivery_receipt_image": None},
        )
        order = service.create_order(order_input)
        # New orders are held for approval and post no financials until approved.
        self.assertEqual(order.approval_status, "pending")
        self.assertIsNone(order.contractor_rate)
        self.assertEqual(db.session.get(VehicleOwner, self.owner_id).balance, 0)

        # Approving with the confirmed rates posts the financials.
        service.approve_order(order.id, contractor_rate=15, vehicle_rate=10)

        company = Company.query.first()
        owner = db.session.get(VehicleOwner, self.owner_id)
        advance_tx_count = db.session.query(Transaction).filter(Transaction.type == "vehicle_advance").count()

        self.assertEqual(order.approval_status, "approved")
        self.assertEqual(order.status, "Completed")
        self.assertEqual(order.remarks, "Night dispatch")
        self.assertEqual(order.from_site_id, self.from_site_id)
        self.assertEqual(order.order_date.date().isoformat(), "2026-04-11")
        # Advances are no longer captured on the order (moved to the Ledger), so
        # the form advance is ignored and no advance transaction is created.
        self.assertEqual(order.advance_amount, 0)
        self.assertEqual(advance_tx_count, 0)
        self.assertEqual(company.balance, 0)
        # The owner is credited for the vehicle amount of the trip after approval.
        self.assertGreater(owner.balance, 0)

    def test_order_service_allows_order_without_plant_and_keeps_loading_detail(self):
        service = OrderService()
        order_input = service.input_from_form(
            type("FormStub", (), {
                "vehicle_id": type("Field", (), {"data": self.vehicle_one_id})(),
                "order_date": type("Field", (), {"data": date(2026, 4, 12)})(),
                "driver_name": type("Field", (), {"data": "Driver"})(),
                "contractor_id": type("Field", (), {"data": self.contractor_id})(),
                "site_id": type("Field", (), {"data": self.site_id})(),
                "from_site_id": type("Field", (), {"data": self.from_site_id})(),
                "material_id": type("Field", (), {"data": self.material_id})(),
                "load_quantity": type("Field", (), {"data": 125})(),
                "unit": type("Field", (), {"data": "cft"})(),
                "advance_amount": type("Field", (), {"data": 0})(),
                "builty_number": type("Field", (), {"data": "B-PLANT-0"})(),
                "receipt_number": type("Field", (), {"data": "DR-PLANT-0"})(),
                "delivered_quantity": type("Field", (), {"data": 125})(),
                "vehicle_rate": type("Field", (), {"data": 10})(),
                "contractor_rate": type("Field", (), {"data": 15})(),
                "plant_id": type("Field", (), {"data": 0})(),
                "plant_amount": type("Field", (), {"data": 0})(),
                "commission": type("Field", (), {"data": 5})(),
                "remarks": type("Field", (), {"data": "No crush plant required"})(),
            }),
            {"loading_image": None, "delivery_receipt_image": None},
        )

        order = service.create_order(order_input)

        self.assertIsNone(order.plant_id)
        self.assertEqual(order.plant_amount, 0)
        self.assertEqual(order.order_date.date().isoformat(), "2026-04-12")
        self.assertIsNotNone(order.primary_loading)
        self.assertIsNone(order.primary_loading.plant_id)
        self.assertEqual(order.primary_loading.plant_amount, 0)

    def test_order_service_requires_plant_when_positive_plant_amount_is_entered(self):
        service = OrderService()
        order_input = service.input_from_form(
            type("FormStub", (), {
                "vehicle_id": type("Field", (), {"data": self.vehicle_one_id})(),
                "order_date": type("Field", (), {"data": date(2026, 4, 13)})(),
                "driver_name": type("Field", (), {"data": "Driver"})(),
                "contractor_id": type("Field", (), {"data": self.contractor_id})(),
                "site_id": type("Field", (), {"data": self.site_id})(),
                "from_site_id": type("Field", (), {"data": self.from_site_id})(),
                "material_id": type("Field", (), {"data": self.material_id})(),
                "load_quantity": type("Field", (), {"data": 125})(),
                "unit": type("Field", (), {"data": "cft"})(),
                "advance_amount": type("Field", (), {"data": 0})(),
                "builty_number": type("Field", (), {"data": "B-PLANT-MISSING"})(),
                "receipt_number": type("Field", (), {"data": "DR-PLANT-MISSING"})(),
                "delivered_quantity": type("Field", (), {"data": 125})(),
                "vehicle_rate": type("Field", (), {"data": 10})(),
                "contractor_rate": type("Field", (), {"data": 15})(),
                "plant_id": type("Field", (), {"data": 0})(),
                "plant_amount": type("Field", (), {"data": 300})(),
                "commission": type("Field", (), {"data": 5})(),
                "remarks": type("Field", (), {"data": "Plant amount without plant"})(),
            }),
            {"loading_image": None, "delivery_receipt_image": None},
        )

        with self.assertRaises(ValidationError):
            service.create_order(order_input)

    def test_create_contractor_bill_marks_selected_orders_as_billed(self):
        order = Order(
            vehicle_id=self.vehicle_one_id,
            contractor_id=self.contractor_id,
            site_id=self.site_id,
            driver_name="Driver",
            material_id=self.material_id,
            material_type="Sand",
            quantity=100,
            delivered_quantity=100,
            contractor_rate=15,
            vehicle_rate=10,
            plant_id=self.plant_id,
            plant_amount=120,
            status="Completed",
        )
        db.session.add(order)
        db.session.commit()

        bill, selected_orders = create_contractor_bill(
            self.contractor_id,
            [order.id],
            [self.site_id],
            "Sand",
            start_date=datetime(2026, 4, 1),
            end_date=datetime(2026, 4, 30),
        )
        db.session.commit()

        db.session.refresh(order)
        self.assertEqual(len(selected_orders), 1)
        self.assertIsInstance(bill, Bill)
        self.assertTrue(order.is_billed)
        self.assertEqual(order.bill_id, bill.id)
        self.assertEqual(bill.total_amount, 1500)

    def test_delete_bill_releases_linked_orders_for_rebilling(self):
        order = Order(
            vehicle_id=self.vehicle_one_id,
            contractor_id=self.contractor_id,
            site_id=self.site_id,
            driver_name="Driver",
            material_id=self.material_id,
            material_type="Sand",
            quantity=100,
            delivered_quantity=100,
            contractor_rate=15,
            vehicle_rate=10,
            plant_id=self.plant_id,
            plant_amount=120,
            status="Completed",
        )
        db.session.add(order)
        db.session.commit()

        billing_service = BillingService()
        bill, _ = billing_service.create_bill(
            "contractor", self.contractor_id, order_ids=[order.id],
            start_date=datetime(2026, 4, 1), end_date=datetime(2026, 4, 30),
        )
        bill_id = bill.id

        db.session.refresh(order)
        self.assertTrue(order.is_billed)

        billing_service.delete_bill(bill_id)

        db.session.refresh(order)
        self.assertIsNone(db.session.get(Bill, bill_id))
        self.assertFalse(order.is_billed)
        self.assertIsNone(order.bill_id)
        self.assertIsNone(order.billed_at)

        # Released trip is billable again — a fresh bill can be created.
        rebill, _ = billing_service.create_bill(
            "contractor", self.contractor_id, order_ids=[order.id],
            start_date=datetime(2026, 4, 1), end_date=datetime(2026, 4, 30),
        )
        self.assertIsInstance(rebill, Bill)

    def test_settled_bill_cannot_be_deleted(self):
        pump = db.session.get(PetrolPump, self.pump_id)
        pump.balance = 500
        entry = DieselEntry(
            vehicle_id=self.vehicle_one_id,
            petrol_pump_id=self.pump_id,
            date=date(2026, 4, 10),
            amount=500,
            balance_applied=True,
            vehicle_balance_applied=True,
        )
        db.session.add(entry)
        db.session.commit()

        billing_service = BillingService()
        bill, _ = billing_service.create_bill(
            "petrol_pump", self.pump_id, entry_ids=[entry.id],
            start_date=datetime(2026, 4, 1), end_date=datetime(2026, 4, 30),
        )
        billing_service.approve_bill(bill.id)
        # Settlement is retired; a legacy bill that carries a settled amount
        # still cannot be deleted (its history must be reversed first).
        bill.settled_amount = 200
        db.session.commit()

        with self.assertRaises(ValidationError):
            billing_service.delete_bill(bill.id)
        self.assertIsNotNone(db.session.get(Bill, bill.id))

    def test_pump_bill_reflects_ledger_payments_in_financials(self):
        pump = db.session.get(PetrolPump, self.pump_id)
        pump.balance = 500
        entry = DieselEntry(
            vehicle_id=self.vehicle_one_id,
            petrol_pump_id=self.pump_id,
            date=date(2026, 4, 10),
            amount=500,
            balance_applied=True,
            vehicle_balance_applied=True,
        )
        db.session.add(entry)
        db.session.commit()

        billing_service = BillingService()
        bill, _ = billing_service.create_bill(
            "petrol_pump", self.pump_id, entry_ids=[entry.id], notes="Fuel bill",
            start_date=datetime(2026, 4, 1), end_date=datetime(2026, 4, 30),
        )
        billing_service.approve_bill(bill.id)

        # Money moves only through the ledger; the bill reflects it by date.
        TransactionService().create_transaction(
            TransactionInput(
                type="petrol_pump_payment",
                amount=200,
                petrol_pump_id=self.pump_id,
                entity_type="petrol_pump",
                entity_id=self.pump_id,
                date=date(2026, 4, 15),
            )
        )
        db.session.commit()

        snapshot = billing_service.bill_snapshot(bill.id)
        financial = snapshot["financial_summary"]
        db.session.refresh(pump)
        company = Company.query.first()

        self.assertEqual(financial["payments_total"], 200)
        self.assertEqual(financial["current_total"], 300)  # 0 previous + 500 bill - 200 paid
        self.assertEqual(pump.balance, 300)
        self.assertEqual(company.balance, -200)

    def test_petrol_pump_bill_snapshot_derives_linked_trips_from_diesel_activity(self):
        order = Order(
            vehicle_id=self.vehicle_one_id,
            contractor_id=self.contractor_id,
            site_id=self.site_id,
            driver_name="Driver",
            material_id=self.material_id,
            material_type="Sand",
            quantity=100,
            delivered_quantity=100,
            advance_amount=0,
            diesel_amount=80,
            contractor_rate=15,
            vehicle_rate=10,
            plant_id=self.plant_id,
            plant_amount=90,
            status="Completed",
        )
        order.diesel_entries = [OrderDieselEntry(amount=80, litres=0.32, petrol_pump_id=self.pump_id, receipt_number="P-001")]
        db.session.add(order)
        db.session.flush()

        pump = db.session.get(PetrolPump, self.pump_id)
        pump.balance = 80
        standalone_entry = DieselEntry(
            vehicle_id=self.vehicle_one_id,
            petrol_pump_id=self.pump_id,
            order_id=order.id,
            date=date(2026, 4, 10),
            amount=80,
            balance_applied=True,
            vehicle_balance_applied=True,
        )
        db.session.add(standalone_entry)
        db.session.commit()

        billing_service = BillingService()
        bill, _ = billing_service.create_bill(
            "petrol_pump", self.pump_id, entry_ids=[standalone_entry.id], notes="Fuel bill",
            start_date=datetime(2026, 4, 1), end_date=datetime(2026, 4, 30),
        )
        snapshot = billing_service.bill_snapshot(bill.id)

        # Order-linked diesel is retired — pump bills are Fuel-Log documents.
        self.assertEqual(snapshot["linked_trip_count"], 0)
        self.assertEqual(len(snapshot["standalone_diesel_rows"]), 1)
        self.assertEqual(snapshot["standalone_diesel_rows"][0].id, standalone_entry.id)

    def test_contractor_receipt_can_create_advance_position(self):
        contractor = db.session.get(Contractor, self.contractor_id)
        TransactionService().create_transaction(
            TransactionInput(
                type="contractor_receipt",
                amount=400,
                contractor_id=self.contractor_id,
                entity_type="contractor",
                entity_id=self.contractor_id,
                reference="ADV-CTR-1",
                description="Advance from contractor",
            )
        )

        db.session.refresh(contractor)
        company = Company.query.first()

        self.assertEqual(company.balance, 400)
        self.assertEqual(contractor.balance, -400)

    def test_financial_report_uses_vehicle_and_plant_costs_only(self):
        # Order-linked diesel and advances are retired: a trip's expense side is
        # the full vehicle amount plus plant charges; fuel lives in the Fuel Log
        # against the pump/owner accounts and advances in the ledger.
        order = Order(
            vehicle_id=self.vehicle_one_id,
            contractor_id=self.contractor_id,
            site_id=self.site_id,
            driver_name="Driver",
            material_id=self.material_id,
            material_type="Sand",
            quantity=100,
            delivered_quantity=100,
            contractor_rate=500,
            vehicle_rate=400,
            plant_id=self.plant_id,
            plant_amount=110,
            status="Completed",
        )
        db.session.add(order)
        db.session.flush()
        # Fuel-Log diesel for the same vehicle must NOT change the trip P&L.
        db.session.add(DieselEntry(
            vehicle_id=self.vehicle_one_id,
            petrol_pump_id=self.pump_id,
            order_id=order.id,
            date=date(2026, 4, 11),
            amount=300,
            balance_applied=True,
            vehicle_balance_applied=True,
        ))
        db.session.commit()

        report = ReportService().financial_report(order.order_date - timedelta(minutes=1), order.order_date + timedelta(days=1))

        self.assertEqual(report["expenses_by_category"]["Vehicle Payments"], 40000)
        self.assertEqual(report["expenses_by_category"]["Plant Payments"], 110)
        self.assertNotIn("Diesel", report["expenses_by_category"])
        self.assertNotIn("Advances", report["expenses_by_category"])
        self.assertEqual(report["total_expenses"], 40110)
        self.assertEqual(report["total_profit"], 50000 - 40110)

    def test_scoped_rate_surfaces_before_from_site_and_material_chosen(self):
        from datetime import date as date_type

        rate = ContractorRate(
            contractor_id=self.contractor_id,
            site_id=self.site_id,
            from_site_id=self.from_site_id,
            material_id=self.material_id,
            unit="cft",
            rate=110,
            effective_from=date_type(2026, 1, 1),
        )
        db.session.add(rate)
        db.session.commit()

        service = RateService()
        check = date_type(2026, 6, 13)

        # Picking only contractor + to-site should already surface the scoped rate.
        suggested = service.find_applicable_rate(self.contractor_id, self.site_id, check_date=check)
        self.assertIsNotNone(suggested)
        self.assertEqual(suggested.rate, 110)

        # A full exact match still resolves to it.
        full = service.find_applicable_rate(self.contractor_id, self.site_id, self.from_site_id, self.material_id, check_date=check)
        self.assertEqual(full.id, rate.id)

        # A conflicting from-site selection excludes the scoped rate.
        conflict = service.find_applicable_rate(self.contractor_id, self.site_id, from_site_id=999999, check_date=check)
        self.assertIsNone(conflict)

    def test_exact_rate_beats_general_when_both_match(self):
        from datetime import date as date_type

        general = ContractorRate(contractor_id=self.contractor_id, site_id=self.site_id, unit="cft", rate=100, effective_from=date_type(2026, 1, 1))
        scoped = ContractorRate(contractor_id=self.contractor_id, site_id=self.site_id, material_id=self.material_id, unit="cft", rate=130, effective_from=date_type(2026, 1, 1))
        db.session.add_all([general, scoped])
        db.session.commit()

        service = RateService()
        check = date_type(2026, 6, 13)

        # No material chosen yet → general rate is the safe default.
        self.assertEqual(service.find_applicable_rate(self.contractor_id, self.site_id, check_date=check).rate, 100)
        # Matching material chosen → the more specific rate wins.
        self.assertEqual(service.find_applicable_rate(self.contractor_id, self.site_id, material_id=self.material_id, check_date=check).rate, 130)

    def test_owner_scoped_rate_beats_route_wide_rate(self):
        from datetime import date as date_type

        route_wide = ContractorRate(contractor_id=self.contractor_id, site_id=self.site_id, unit="cft", rate=100, vehicle_rate=80, effective_from=date_type(2026, 1, 1))
        owner_scoped = ContractorRate(contractor_id=self.contractor_id, site_id=self.site_id, vehicle_owner_id=self.owner_id, unit="cft", rate=100, vehicle_rate=90, effective_from=date_type(2026, 1, 1))
        db.session.add_all([route_wide, owner_scoped])
        db.session.commit()

        service = RateService()
        check = date_type(2026, 6, 13)

        # No vehicle picked yet → the route-wide rate is the safe default.
        self.assertEqual(service.find_applicable_rate(self.contractor_id, self.site_id, check_date=check).id, route_wide.id)
        # Matching owner → the owner-specific rate wins.
        self.assertEqual(service.find_applicable_rate(self.contractor_id, self.site_id, vehicle_owner_id=self.owner_id, check_date=check).id, owner_scoped.id)
        # A different owner conflicts with the scoped rate → falls back to route-wide.
        self.assertEqual(service.find_applicable_rate(self.contractor_id, self.site_id, vehicle_owner_id=999999, check_date=check).id, route_wide.id)

    def test_save_rate_from_order_end_dates_overlapping_same_scope_rate(self):
        from datetime import date as date_type

        old = ContractorRate(contractor_id=self.contractor_id, site_id=self.site_id, unit="cft", rate=100, effective_from=date_type(2026, 1, 1))
        scoped = ContractorRate(contractor_id=self.contractor_id, site_id=self.site_id, material_id=self.material_id, unit="cft", rate=130, effective_from=date_type(2026, 1, 1))
        db.session.add_all([old, scoped])
        db.session.commit()

        service = RateService()
        new = service.save_rate_from_order({
            "contractor_id": self.contractor_id,
            "site_id": self.site_id,
            "from_site_id": None,
            "material_id": None,
            "unit": "cft",
            "rate": 120,
            "vehicle_rate": 95,
            "effective_from": date_type(2026, 7, 1),
            "effective_to": None,
            "notes": "Saved from order entry",
        })

        # The overlapping same-scope rate is closed the day before the new one starts.
        self.assertEqual(old.effective_to, date_type(2026, 6, 30))
        # A rate with a different scope (material-specific) is left untouched.
        self.assertIsNone(scoped.effective_to)

        # Old window still answers before the switch; the new rate answers after it.
        self.assertEqual(service.find_applicable_rate(self.contractor_id, self.site_id, check_date=date_type(2026, 5, 1)).rate, 100)
        self.assertEqual(service.find_applicable_rate(self.contractor_id, self.site_id, check_date=date_type(2026, 7, 2)).id, new.id)

    def test_performance_report_builds_trend_and_rankings(self):
        order = Order(
            vehicle_id=self.vehicle_one_id,
            contractor_id=self.contractor_id,
            site_id=self.site_id,
            driver_name="Driver",
            material_id=self.material_id,
            material_type="Sand",
            quantity=100,
            delivered_quantity=100,
            contractor_rate=500,
            vehicle_rate=400,
            status="Completed",
        )
        db.session.add(order)
        db.session.commit()

        context = ReportService().workspace_context({"report_type": "performance"})

        self.assertEqual(context["report_type"], "performance")
        self.assertEqual(len(context["monthly_rows"]), 1)
        self.assertEqual(context["monthly_rows"][0]["trips"], 1)
        self.assertEqual(context["contractor_rows"][0]["name"], "Contractor One")
        self.assertEqual(context["contractor_rows"][0]["profit"], 10000)
        self.assertEqual(context["vehicle_rows"][0]["vehicle_number"], "ABC-001")

    def test_vehicle_owner_bill_snapshot_includes_trip_detail_rows(self):
        owner = db.session.get(VehicleOwner, self.owner_id)
        owner.balance = 500
        order = Order(
            vehicle_id=self.vehicle_one_id,
            contractor_id=self.contractor_id,
            site_id=self.site_id,
            driver_name="Driver",
            material_id=self.material_id,
            material_type="Sand",
            quantity=100,
            delivered_quantity=90,
            advance_amount=100,
            diesel_amount=80,
            contractor_rate=15,
            vehicle_rate=10,
            plant_id=self.plant_id,
            plant_amount=50,
            receipt_number="DEL-001",
            status="Completed",
        )
        order.delivery_receipt_image = "/static/uploads/deliveries/sample.jpg"
        order.diesel_entries = [OrderDieselEntry(amount=80)]
        db.session.add(order)
        db.session.commit()

        billing_service = BillingService()
        bill, _ = billing_service.create_bill(
            "vehicle_owner", owner.id, order_ids=[order.id], notes="Owner bill",
            start_date=datetime(2026, 4, 1), end_date=datetime(2026, 4, 30),
        )
        snapshot = billing_service.bill_snapshot(bill.id)

        self.assertEqual(len(snapshot["vehicle_owner_activity_rows"]), 1)
        self.assertEqual(snapshot["vehicle_owner_activity_rows"][0].receipt_number, "DEL-001")
        self.assertEqual(snapshot["vehicle_owner_activity_rows"][0].vehicle.vehicle_number, "ABC-001")

    def _order_input(self, **overrides):
        values = {
            "order_date": date(2026, 5, 1),
            "vehicle_id": self.vehicle_one_id,
            "driver_name": "Driver",
            "contractor_id": self.contractor_id,
            "site_id": self.site_id,
            "from_site_id": None,
            "material_id": self.material_id,
            "load_quantity": 100,
            "unit": "cft",
            "advance_amount": 0,
            "builty_number": None,
            "receipt_number": None,
            "delivered_quantity": 100,
            "vehicle_rate": 10,
            "contractor_rate": 15,
            "plant_id": None,
            "plant_amount": 0,
            "commission": 0,
            "loading_image": None,
            "delivery_receipt_image": None,
            "remarks": None,
        }
        values.update(overrides)
        return OrderInput(**values)

    def test_billed_order_cannot_be_edited_or_deleted(self):
        service = OrderService()
        order = service.create_order(self._order_input())
        service.approve_order(order.id, contractor_rate=15, vehicle_rate=10)
        bill, _ = create_contractor_bill(
            self.contractor_id, [order.id],
            start_date=datetime(2026, 4, 1), end_date=datetime(2026, 6, 30),
        )
        db.session.commit()

        with self.assertRaises(ValidationError):
            service.update_order(order.id, self._order_input(delivered_quantity=50))
        with self.assertRaises(ValidationError):
            service.delete_order(order.id)

        db.session.refresh(order)
        self.assertEqual(order.delivered_quantity, 100)
        self.assertEqual(bill.total_amount, 1500)

    def test_receipt_delete_restores_contractor_balance(self):
        service = OrderService()
        order = service.create_order(self._order_input())
        service.approve_order(order.id, contractor_rate=15, vehicle_rate=10)
        db.session.commit()

        transaction_service = TransactionService()
        receipt = transaction_service.create_transaction(
            TransactionInput(
                type="contractor_receipt",
                amount=600,
                contractor_id=self.contractor_id,
                entity_type="contractor",
                entity_id=self.contractor_id,
                reference="PAY-9",
            )
        )
        db.session.commit()
        contractor = db.session.get(Contractor, self.contractor_id)
        self.assertEqual(contractor.balance, 900)  # 1500 receivable - 600 received

        transaction_service.delete_transaction(receipt.id)
        db.session.refresh(contractor)
        self.assertEqual(contractor.balance, 1500)

    def test_dashboard_profit_matches_order_profit(self):
        order = Order(
            vehicle_id=self.vehicle_one_id,
            contractor_id=self.contractor_id,
            site_id=self.site_id,
            driver_name="Driver",
            material_id=self.material_id,
            material_type="Sand",
            quantity=100,
            delivered_quantity=100,
            advance_amount=200,
            diesel_amount=50,
            contractor_rate=15,
            vehicle_rate=10,
            plant_id=self.plant_id,
            plant_amount=100,
            commission=30,
            status="Completed",
        )
        order.diesel_entries = [OrderDieselEntry(amount=50)]
        db.session.add(order)
        db.session.commit()

        metrics = get_dashboard_metrics()

        # profit = 1500 revenue − (1000 − 30 commission) net vehicle − 100 plant
        self.assertEqual(metrics["today_profit"], order.profit_amount())
        self.assertEqual(metrics["today_profit"], 430)
        self.assertEqual(metrics["today_expenses"], metrics["today_revenue"] - metrics["today_profit"])

    def test_reconciliation_detects_and_repairs_drift(self):
        service = OrderService()
        order = service.create_order(self._order_input(advance_amount=200))
        service.approve_order(order.id, contractor_rate=15, vehicle_rate=10)

        contractor = db.session.get(Contractor, self.contractor_id)
        self.assertEqual(contractor.balance, 1500)

        # Simulate drift (e.g. a legacy bug or manual DB edit).
        contractor.balance = 999
        db.session.commit()

        reconciliation = ReconciliationService()
        report = reconciliation.build_report()
        row = next(r for r in report["rows"] if r["entity_type"] == "contractor" and r["entity_id"] == self.contractor_id)
        self.assertTrue(row["has_drift"])
        self.assertEqual(row["expected_balance"], 1500)

        company_row = next(r for r in report["rows"] if r["entity_type"] == "company")
        self.assertEqual(company_row["expected_balance"], -200)
        self.assertFalse(company_row["has_drift"])

        reconciliation.repair("contractor", self.contractor_id)
        db.session.refresh(contractor)
        self.assertEqual(contractor.balance, 1500)

        with self.assertRaises(ValidationError):
            reconciliation.repair("contractor", self.contractor_id)

    def test_pending_order_is_hidden_until_approved_then_posts(self):
        service = OrderService()
        order = service.create_order(self._order_input())

        # Pending: no rates, no financials, hidden from register / summary / P&L.
        self.assertEqual(order.approval_status, "pending")
        self.assertEqual(len(service.list_orders_filtered({})), 0)
        self.assertEqual(service.orders_summary({})["total_trips"], 0)
        self.assertEqual(service.orders_pnl({})["revenue"], 0)
        self.assertEqual(service.pending_count(), 1)
        self.assertEqual(db.session.get(Contractor, self.contractor_id).balance, 0)

        groups = service.list_pending_approvals()
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["rows"]), 1)

        # Approving posts the financials and makes it visible.
        service.approve_order(order.id, contractor_rate=15, vehicle_rate=10)
        self.assertEqual(len(service.list_orders_filtered({})), 1)
        self.assertEqual(service.orders_pnl({})["revenue"], 1500)
        self.assertEqual(service.pending_count(), 0)
        self.assertEqual(db.session.get(Contractor, self.contractor_id).balance, 1500)

    def test_reject_pending_order_removes_it(self):
        service = OrderService()
        order = service.create_order(self._order_input())
        order_id = order.id
        service.reject_order(order_id)
        self.assertIsNone(db.session.get(Order, order_id))
        self.assertEqual(service.pending_count(), 0)

    def test_diesel_entry_pending_until_approved(self):
        diesel = DieselService()
        pump = db.session.get(PetrolPump, self.pump_id)
        pump_start = float(pump.balance or 0)

        entry = diesel.create_entry({
            "vehicle_id": self.vehicle_one_id,
            "petrol_pump_id": self.pump_id,
            "date": date(2026, 4, 11),
            "amount": 500,
        })

        # Pending: no balances applied, hidden from the fuel log.
        self.assertEqual(entry.approval_status, "pending")
        self.assertFalse(entry.balance_applied)
        db.session.refresh(pump)
        self.assertEqual(float(pump.balance or 0), pump_start)
        self.assertEqual(len(diesel.list_entries()), 0)
        self.assertEqual(diesel.pending_count(), 1)

        # Approving applies the pump and vehicle balances.
        diesel.approve_entry(entry.id)
        db.session.refresh(pump)
        self.assertEqual(float(pump.balance or 0), pump_start + 500)
        self.assertTrue(entry.balance_applied)
        self.assertTrue(entry.vehicle_balance_applied)
        self.assertEqual(len(diesel.list_entries()), 1)
        self.assertEqual(diesel.pending_count(), 0)

    def test_generic_transaction_directions_move_balances_both_ways(self):
        service = TransactionService()
        company = Company.query.first()

        # Payment to a contractor (inverse of receipt): money out, receivable up.
        service.create_transaction(TransactionInput(
            type="contractor_payment", amount=100, entity_type="contractor",
            entity_id=self.contractor_id, contractor_id=self.contractor_id,
        ))
        self.assertEqual(db.session.get(Contractor, self.contractor_id).balance, 100)
        self.assertEqual(Company.query.first().balance, -100)

        # Receipt from a plant (inverse of payment): money in, plant payable up.
        service.create_transaction(TransactionInput(
            type="plant_receipt", amount=50, entity_type="plant",
            entity_id=self.plant_id, plant_id=self.plant_id,
        ))
        self.assertEqual(db.session.get(Plant, self.plant_id).balance, 50)

        # Receipt from a petrol pump: money in, pump payable up.
        service.create_transaction(TransactionInput(
            type="petrol_pump_receipt", amount=30, entity_type="petrol_pump",
            entity_id=self.pump_id, petrol_pump_id=self.pump_id,
        ))
        self.assertEqual(db.session.get(PetrolPump, self.pump_id).balance, 30)
        # Company: -100 (paid contractor) + 50 (plant) + 30 (pump) = -20.
        self.assertEqual(Company.query.first().balance, -20)

    def test_manual_transaction_pending_until_approved(self):
        service = TransactionService()
        start = float(db.session.get(Contractor, self.contractor_id).balance or 0)

        tx = service.create_transaction(TransactionInput(
            type="contractor_receipt", amount=300, entity_type="contractor",
            entity_id=self.contractor_id, contractor_id=self.contractor_id,
        ), approval_status="pending")

        # Pending: no balance effect, hidden from the ledger list.
        self.assertEqual(tx.approval_status, "pending")
        self.assertEqual(float(db.session.get(Contractor, self.contractor_id).balance or 0), start)
        self.assertEqual(len(service.list_transactions()), 0)
        self.assertEqual(service.pending_count(), 1)

        # Approving posts the effect and reveals it.
        service.approve_transaction(tx.id)
        self.assertEqual(float(db.session.get(Contractor, self.contractor_id).balance or 0), start - 300)
        self.assertEqual(len(service.list_transactions()), 1)
        self.assertEqual(service.pending_count(), 0)

    def test_editing_approved_order_sends_it_back_to_pending(self):
        service = OrderService()
        order = service.create_order(self._order_input())
        service.approve_order(order.id, contractor_rate=15, vehicle_rate=10)
        contractor_after_approve = db.session.get(Contractor, self.contractor_id).balance
        self.assertEqual(contractor_after_approve, 1500)

        # Editing a live order reverses its financials and holds it for re-approval.
        service.update_order(order.id, self._order_input(delivered_quantity=50))
        db.session.refresh(order)
        self.assertEqual(order.approval_status, "pending")
        self.assertEqual(order.status, "Pending Approval")
        self.assertEqual(db.session.get(Contractor, self.contractor_id).balance, 0)
        self.assertEqual(len(service.list_orders_filtered({})), 0)

    def test_financial_entity_transactions_move_company_balance(self):
        fe_service = FinancialEntityService()
        entity = fe_service.create_entity("Brother Account")

        tx = fe_service.post_transaction(entity.id, "loan_given", 300)
        company = Company.query.first()
        self.assertEqual(company.balance, -300)
        self.assertEqual(entity.balance, 300)

        fe_service.delete_transaction(tx.id)
        company = Company.query.first()
        self.assertEqual(company.balance, 0)
        self.assertEqual(entity.balance, 0)


if __name__ == "__main__":
    unittest.main()
