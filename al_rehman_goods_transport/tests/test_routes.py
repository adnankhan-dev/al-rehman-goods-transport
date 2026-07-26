import os
import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

PACKAGE_PARENT = Path(__file__).resolve().parents[2]
TEST_DB_PATH = Path(__file__).resolve().parent / "_test_app.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"
os.environ["SECRET_KEY"] = "test-secret"

if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

from al_rehman_goods_transport import create_app, db
from al_rehman_goods_transport.core.database import Base, engine  # engine rebound to the test DB in tests/__init__.py
from al_rehman_goods_transport.core.permissions import role_permissions
from al_rehman_goods_transport.models import Contractor, Material, Order, OrderDieselEntry, Plant, Site, Transaction, User, Vehicle


class RouteTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self.client = TestClient(create_app())

        user = User(username="admin", email="admin@example.com")
        user.set_role("admin")
        user.set_permissions(None)
        user.set_password("admin123")
        db.session.add(user)
        db.session.commit()

    def _login(self, username="admin", password="admin123"):
        return self.client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=True,
        )

    def tearDown(self):
        self.client.close()
        db.session.remove()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        if TEST_DB_PATH.exists():
            TEST_DB_PATH.unlink()

    def test_dashboard_requires_login(self):
        response = self.client.get("/dashboard", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn("/login", response.headers["location"])

    def test_login_allows_dashboard_access(self):
        response = self._login()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Dispatch, accounts, and reports — all in one workspace.", response.text)

    def test_bill_creation_marks_order_as_billed_and_shows_bill_page(self):
        contractor = Contractor(name="ABC Contractors", contact_person="John Doe")
        material = Material(name="Sand")
        vehicle = Vehicle(vehicle_number="ABC-001")
        plant = Plant(name="Plant One")
        db.session.add_all([contractor, material, vehicle, plant])
        db.session.flush()

        site = Site(name="Site One", contractor_id=contractor.id)
        db.session.add(site)
        db.session.flush()

        order = Order(
            vehicle_id=vehicle.id,
            contractor_id=contractor.id,
            site_id=site.id,
            driver_name="Driver One",
            material_id=material.id,
            material_type="Sand",
            quantity=100,
            delivered_quantity=100,
            contractor_rate=16,
            vehicle_rate=10,
            plant_id=plant.id,
            plant_amount=120,
            receipt_number="RCPT-001",
            status="Completed",
        )
        db.session.add(order)
        db.session.commit()

        self._login()
        response = self.client.post(
            "/bills/create",
            data={
                "entity_type": "contractor",
                "contractor_id": str(contractor.id),
                "site_ids": [str(site.id)],
                "material_type": "Sand",
                "order_ids": [str(order.id)],
                "start_date": "2026-04-01",
                "end_date": "2026-04-30",
            },
            follow_redirects=True,
        )

        db.session.refresh(order)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Bill BILL-ABC-001", response.text)
        self.assertTrue(order.is_billed)

        contractor_page = self.client.get(f"/contractors/{contractor.id}")
        self.assertEqual(contractor_page.status_code, 200)
        self.assertIn("Billed", contractor_page.text)

    def test_legacy_finance_lists_redirect_to_erp_ledger(self):
        self._login()

        bills_response = self.client.get("/bills", follow_redirects=False)
        transactions_response = self.client.get("/transactions", follow_redirects=False)
        create_bill_response = self.client.get("/bills/create", follow_redirects=False)
        create_transaction_response = self.client.get("/transactions/create", follow_redirects=False)

        self.assertEqual(bills_response.status_code, 303)
        self.assertEqual(transactions_response.status_code, 303)
        self.assertEqual(create_bill_response.status_code, 303)
        self.assertEqual(create_transaction_response.status_code, 303)
        self.assertTrue(bills_response.headers["location"].endswith("/ledger"))
        self.assertTrue(transactions_response.headers["location"].endswith("/ledger"))
        self.assertIn("/ledger/create?mode=bill", create_bill_response.headers["location"])
        self.assertIn("/ledger/create?mode=transaction", create_transaction_response.headers["location"])

    def test_ledger_transaction_mode_posts_transaction(self):
        self._login()

        response = self.client.post(
            "/ledger/create",
            data={
                "mode": "transaction",
                "type": "other_expense",
                "amount": "250",
                "description": "Office fuel slip",
                "payment_method": "cash",
                "reference": "EXP-001",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        # New transactions are created pending and post nothing until approved.
        self.assertEqual(db.session.query(Transaction).count(), 1)
        transaction = db.session.query(Transaction).first()
        self.assertEqual(transaction.type, "other_expense")
        self.assertEqual(transaction.reference, "EXP-001")
        self.assertEqual(transaction.approval_status, "pending")

    def test_bill_print_and_excel_export_routes_work(self):
        contractor = Contractor(name="ABC Contractors", contact_person="John Doe")
        material = Material(name="Sand")
        vehicle = Vehicle(vehicle_number="ABC-001")
        plant = Plant(name="Plant One")
        db.session.add_all([contractor, material, vehicle, plant])
        db.session.flush()

        site = Site(name="Site One", contractor_id=contractor.id)
        db.session.add(site)
        db.session.flush()

        order = Order(
            vehicle_id=vehicle.id,
            contractor_id=contractor.id,
            site_id=site.id,
            driver_name="Driver One",
            material_id=material.id,
            material_type="Sand",
            quantity=100,
            delivered_quantity=100,
            contractor_rate=16,
            vehicle_rate=10,
            plant_id=plant.id,
            plant_amount=120,
            receipt_number="RCPT-001",
            status="Completed",
        )
        db.session.add(order)
        db.session.commit()

        self._login()
        bill_response = self.client.post(
            "/bills/create",
            data={
                "entity_type": "contractor",
                "contractor_id": str(contractor.id),
                "site_ids": [str(site.id)],
                "material_type": "Sand",
                "order_ids": [str(order.id)],
                "start_date": "2026-04-01",
                "end_date": "2026-04-30",
            },
            follow_redirects=False,
        )
        bill_location = bill_response.headers["location"]
        bill_id = int(bill_location.rstrip("/").split("/")[-1])

        print_response = self.client.get(f"/bills/{bill_id}/print", follow_redirects=True)
        export_response = self.client.get(f"/bills/{bill_id}/export/excel", follow_redirects=True)
        pdf_response = self.client.get(f"/bills/{bill_id}/export/pdf", follow_redirects=True)

        self.assertEqual(print_response.status_code, 200)
        self.assertIn("Download PDF", print_response.text)
        self.assertEqual(export_response.status_code, 200)
        self.assertIn("application/vnd.ms-excel", export_response.headers["content-type"])
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response.headers["content-type"], "application/pdf")
        self.assertTrue(pdf_response.content.startswith(b"%PDF"))
        self.assertGreater(len(pdf_response.content), 1000)

    def test_business_site_can_be_created_without_contractor(self):
        self._login()

        response = self.client.post(
            "/sites/create",
            data={
                "name": "Yard Alpha",
                "contact_person": "Store Incharge",
                "phone": "03001234567",
                "contractor_id": "0",
                "is_business_site": "y",
                "address": "Main yard",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        site = db.session.query(Site).filter_by(name="Yard Alpha").first()
        self.assertIsNotNone(site)
        self.assertTrue(site.is_business_site)
        self.assertIsNone(site.contractor_id)

    def test_orders_print_route_supports_filters(self):
        contractor = Contractor(name="ABC Contractors", contact_person="John Doe")
        material = Material(name="Sand")
        vehicle = Vehicle(vehicle_number="ABC-001")
        plant = Plant(name="Plant One")
        db.session.add_all([contractor, material, vehicle, plant])
        db.session.flush()

        site = Site(name="Site One", contractor_id=contractor.id)
        from_site = Site(name="Yard Alpha", is_business_site=True)
        db.session.add_all([site, from_site])
        db.session.flush()

        order = Order(
            vehicle_id=vehicle.id,
            contractor_id=contractor.id,
            site_id=site.id,
            from_site_id=from_site.id,
            driver_name="Driver One",
            material_id=material.id,
            material_type="Sand",
            quantity=100,
            delivered_quantity=100,
            contractor_rate=16,
            vehicle_rate=10,
            plant_id=plant.id,
            plant_amount=120,
            receipt_number="RCPT-001",
            status="Completed",
        )
        db.session.add(order)
        db.session.commit()

        self._login()
        response = self.client.get(f"/orders/print?from_site_id={from_site.id}", follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Al Rehman Goods Transport", response.text)
        self.assertIn("Yard Alpha", response.text)

    def test_pending_approvals_page_and_approve_flow(self):
        contractor = Contractor(name="ABC Contractors")
        material = Material(name="Sand")
        vehicle = Vehicle(vehicle_number="ABC-001")
        db.session.add_all([contractor, material, vehicle])
        db.session.flush()
        site = Site(name="Site One", contractor_id=contractor.id)
        db.session.add(site)
        db.session.flush()

        # A pending order (created directly to mimic the add-order flow output).
        order = Order(
            vehicle_id=vehicle.id,
            contractor_id=contractor.id,
            site_id=site.id,
            driver_name="Driver One",
            material_id=material.id,
            material_type="Sand",
            quantity=100,
            delivered_quantity=100,
            receipt_number="RCPT-APPROVE",
            status="Pending Approval",
            approval_status="pending",
        )
        db.session.add(order)
        db.session.commit()
        order_id = order.id

        self._login()
        # The pending order must NOT appear in the register…
        register = self.client.get("/orders", follow_redirects=True)
        self.assertNotIn("RCPT-APPROVE", register.text)
        # …but it must appear on the Pending Approvals page.
        pending = self.client.get("/orders/pending", follow_redirects=True)
        self.assertEqual(pending.status_code, 200)
        self.assertIn("ABC Contractors", pending.text)

        # Approve it with rates → it posts and becomes visible in the register.
        approve = self.client.post(
            "/orders/approve",
            data={"order_ids": str(order_id), f"contractor_rate_{order_id}": "16", f"vehicle_rate_{order_id}": "10"},
            follow_redirects=True,
        )
        self.assertEqual(approve.status_code, 200)
        db.session.expire_all()
        approved = db.session.get(Order, order_id)
        self.assertEqual(approved.approval_status, "approved")
        self.assertEqual(approved.status, "Completed")
        self.assertEqual(approved.contractor_rate, 16)
        self.assertGreater(db.session.get(Contractor, contractor.id).balance, 0)

    def test_pending_approvals_grouping_and_view_only_access(self):
        contractor = Contractor(name="ABC Contractors")
        material = Material(name="Sand")
        vehicle = Vehicle(vehicle_number="ABC-777")
        db.session.add_all([contractor, material, vehicle])
        db.session.flush()
        site = Site(name="Site One", contractor_id=contractor.id)
        db.session.add(site)
        db.session.flush()
        order = Order(
            vehicle_id=vehicle.id, contractor_id=contractor.id, site_id=site.id,
            driver_name="Driver", material_id=material.id, material_type="Sand",
            quantity=100, delivered_quantity=100, receipt_number="RCPT-GRP",
            status="Pending Approval", approval_status="pending",
        )
        db.session.add(order)
        db.session.commit()

        # A view-only user (orders.view, no approve/edit).
        viewer = User(username="viewer2", email="viewer2@example.com")
        viewer.set_role("viewer")
        viewer.set_permissions(["orders.view"])
        viewer.set_password("viewerpass1")
        db.session.add(viewer)
        db.session.commit()

        # Admin sees the page with the grouping applied and Approve controls.
        self._login()
        grouped = self.client.get("/orders/pending?group_by=vehicle", follow_redirects=True)
        self.assertEqual(grouped.status_code, 200)
        self.assertIn("ABC-777", grouped.text)
        self.assertIn("Approve", grouped.text)

        # Viewer can open it read-only: sees the order, but no approve controls.
        self.client.get("/logout", follow_redirects=True)
        self._login(username="viewer2", password="viewerpass1")
        view_only = self.client.get("/orders/pending", follow_redirects=True)
        self.assertEqual(view_only.status_code, 200)
        self.assertIn("RCPT-GRP", view_only.text)
        self.assertIn("view-only access", view_only.text)
        self.assertNotIn("approveRow", view_only.text)

    def test_print_column_settings_hide_and_reflect(self):
        from al_rehman_goods_transport.services import SettingsService

        self._login()
        # Checked = shown. Send all columns checked EXCEPT fuel_log/litres to hide it.
        data = {"action": "update_template_columns"}
        for doc, meta in SettingsService.TEMPLATE_COLUMNS.items():
            for key, _label in meta["columns"]:
                if not (doc == "fuel_log" and key == "litres"):
                    data[f"col_{doc}_{key}"] = "on"
        resp = self.client.post("/settings", data=data, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        # Saved: only litres hidden for the fuel log; other docs unchanged.
        prefs = SettingsService().get_template_columns()
        self.assertEqual(prefs["fuel_log"], ["litres"])
        self.assertEqual(prefs["bill"], [])

        # The fuel log print now carries the hide rule for that column.
        printed = self.client.get("/diesel/print", follow_redirects=True)
        self.assertEqual(printed.status_code, 200)
        self.assertIn(".col-litres { display: none", printed.text)

    def test_reports_workspace_supports_diesel_mode(self):
        contractor = Contractor(name="ABC Contractors", contact_person="John Doe")
        material = Material(name="Sand")
        vehicle = Vehicle(vehicle_number="ABC-001")
        plant = Plant(name="Plant One")
        db.session.add_all([contractor, material, vehicle, plant])
        db.session.flush()

        site = Site(name="Site One", contractor_id=contractor.id)
        db.session.add(site)
        db.session.flush()

        order = Order(
            vehicle_id=vehicle.id,
            contractor_id=contractor.id,
            site_id=site.id,
            driver_name="Driver One",
            material_id=material.id,
            material_type="Sand",
            quantity=100,
            delivered_quantity=100,
            contractor_rate=16,
            vehicle_rate=10,
            plant_id=plant.id,
            plant_amount=120,
            receipt_number="RCPT-001",
            diesel_amount=250,
            status="Completed",
        )
        db.session.add(order)
        db.session.flush()
        order.diesel_entries.append(OrderDieselEntry(amount=250, litres=1, receipt_number="DS-1"))
        db.session.commit()

        self._login()
        response = self.client.get("/reports?report_type=diesel", follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Diesel Report", response.text)
        self.assertIn("Diesel register", response.text)

    def test_legacy_financial_report_route_redirects_to_reports_workspace(self):
        self._login()

        response = self.client.get("/reports/financial", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertIn("/reports?report_type=profit_loss", response.headers["location"])

    def test_admin_can_create_user_from_settings(self):
        self._login()

        response = self.client.post(
            "/settings/users/create",
            data={
                "username": "viewer1",
                "email": "viewer1@example.com",
                "password": "viewerpass1",
                "confirm_password": "viewerpass1",
                "role": "viewer",
                "permission_codes": ["dashboard.view", "orders.view", "reports.view"],
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("User viewer1 created successfully.", response.text)
        created_user = db.session.query(User).filter_by(username="viewer1").first()
        self.assertIsNotNone(created_user)
        self.assertEqual(created_user.role, "viewer")
        self.assertIn("orders.view", created_user.permission_codes)

    def test_settings_backup_download_returns_sqlite_file(self):
        self._login()

        response = self.client.get("/settings/backup/download")

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment;", response.headers["content-disposition"])
        self.assertTrue(response.content.startswith(b"SQLite format 3"))

    def test_settings_backup_restore_reverts_database_state(self):
        self._login()
        backup_response = self.client.get("/settings/backup/download")
        self.assertEqual(backup_response.status_code, 200)

        extra_user = User(username="tempuser", email="tempuser@example.com")
        extra_user.set_role("viewer")
        extra_user.set_permissions(["dashboard.view"])
        extra_user.set_password("viewerpass1")
        db.session.add(extra_user)
        db.session.commit()
        self.assertEqual(db.session.query(User).count(), 2)
        db.session.remove()

        response = self.client.post(
            "/settings/backup/restore",
            files={"backup_file": ("backup.db", backup_response.content, "application/x-sqlite3")},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Backup restored successfully.", response.text)
        db.session.remove()
        self.assertEqual(db.session.query(User).count(), 1)

    def test_viewer_cannot_open_manage_only_route(self):
        viewer = User(username="viewer", email="viewer@example.com")
        viewer.set_role("viewer")
        viewer.set_permissions(["dashboard.view", "orders.view", "reports.view"])
        viewer.set_password("viewerpass1")
        db.session.add(viewer)
        db.session.commit()

        self._login(username="viewer", password="viewerpass1")
        response = self.client.get("/orders/create", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "http://testserver/")

    def test_data_entry_role_cannot_edit_orders_by_default(self):
        operator = User(username="operator", email="operator@example.com")
        operator.set_role("data_entry")
        operator.set_permissions(role_permissions("data_entry"))
        operator.set_password("operator123")
        db.session.add(operator)
        db.session.commit()

        contractor = Contractor(name="ABC Contractors", contact_person="John Doe")
        material = Material(name="Sand")
        vehicle = Vehicle(vehicle_number="ABC-001")
        plant = Plant(name="Plant One")
        db.session.add_all([contractor, material, vehicle, plant])
        db.session.flush()

        site = Site(name="Site One", contractor_id=contractor.id)
        db.session.add(site)
        db.session.flush()

        order = Order(
            vehicle_id=vehicle.id,
            contractor_id=contractor.id,
            site_id=site.id,
            driver_name="Driver One",
            material_id=material.id,
            material_type="Sand",
            quantity=100,
            delivered_quantity=100,
            contractor_rate=16,
            vehicle_rate=10,
            plant_id=plant.id,
            plant_amount=120,
            receipt_number="RCPT-001",
            status="Completed",
        )
        db.session.add(order)
        db.session.commit()

        self._login(username="operator", password="operator123")
        response = self.client.get(f"/orders/{order.id}/edit", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "http://testserver/")


if __name__ == "__main__":
    unittest.main()
