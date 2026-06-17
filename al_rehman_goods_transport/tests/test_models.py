import os
import sys
import unittest
from pathlib import Path

PACKAGE_PARENT = Path(__file__).resolve().parents[2]
TEST_DB_PATH = Path(__file__).resolve().parent / "_test_app.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"
os.environ["SECRET_KEY"] = "test-secret"

if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

from al_rehman_goods_transport.models import Order, OrderDieselEntry, User
from al_rehman_goods_transport.core.permissions import ROLE_DEFINITIONS


class OrderModelTests(unittest.TestCase):
    def test_order_financial_helpers_use_net_vehicle_amount(self):
        order = Order(
            quantity=100,
            delivered_quantity=95,
            vehicle_rate=12,
            contractor_rate=15,
            plant_amount=120,
            commission=30,
            advance_amount=50,
            diesel_amount=40,
        )
        order.diesel_entries = [OrderDieselEntry(amount=40), OrderDieselEntry(amount=10)]

        self.assertEqual(order.total_contractor_amount(), 1425)
        self.assertEqual(order.gross_vehicle_amount(), 1140)
        self.assertEqual(order.total_vehicle_amount(), 1110)
        self.assertEqual(order.remaining_vehicle_payment(), 1010)
        self.assertEqual(order.profit_amount(), 195)
        self.assertEqual(order.total_advance_amount(), 50)
        self.assertEqual(order.total_diesel_amount(), 50)

    def test_legacy_manage_permission_still_implies_view_permission(self):
        user = User(username="viewer", email="viewer@example.com")
        user.set_role("viewer")
        user.set_permissions(["orders.manage"])

        self.assertTrue(user.can("orders.manage"))
        self.assertTrue(user.can("orders.view"))
        self.assertTrue(user.can("orders.edit"))
        self.assertFalse(user.can("ledger.view"))

    def test_data_entry_role_is_available(self):
        self.assertIn("data_entry", ROLE_DEFINITIONS)
        self.assertEqual(ROLE_DEFINITIONS["data_entry"]["label"], "Data Entry Operator")
        self.assertIn("orders.create", ROLE_DEFINITIONS["data_entry"]["permissions"])
        self.assertNotIn("orders.edit", ROLE_DEFINITIONS["data_entry"]["permissions"])
        self.assertNotIn("ledger.edit", ROLE_DEFINITIONS["data_entry"]["permissions"])


if __name__ == "__main__":
    unittest.main()
