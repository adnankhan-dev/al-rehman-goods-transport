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

from al_rehman_goods_transport.utils.helpers import format_currency, format_quantity
from al_rehman_goods_transport.utils.validators import validate_email, validate_phone_number


class UtilsTests(unittest.TestCase):
    def test_currency_formatting(self):
        self.assertEqual(format_currency(12500), "RS12,500.00")

    def test_quantity_formatting(self):
        self.assertEqual(format_quantity(42), "42.00 CFT")

    def test_phone_validation(self):
        self.assertTrue(validate_phone_number("03001234567"))
        self.assertFalse(validate_phone_number("abc"))

    def test_email_validation(self):
        self.assertTrue(validate_email("ops@example.com"))
        self.assertFalse(validate_email("invalid-email"))


if __name__ == "__main__":
    unittest.main()
