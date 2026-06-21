"""Test package bootstrap.

Importing the application package builds the SQLAlchemy engine against the
default (live) DATABASE_URL before any individual test module runs. This
package __init__ executes first when any test under it is imported, so we point
the database at a throwaway file and rebind the engine exactly once here. This
guarantees the suite can never read or wipe the live data/app.db, and avoids
each test module rebinding its own separate engine to the same file (which
caused SQLite lock errors when modules ran together).
"""

import os
from pathlib import Path

TEST_DB_PATH = Path(__file__).resolve().parent / "_test_app.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"
os.environ.setdefault("SECRET_KEY", "test-secret")

from ..core.database import configure_engine  # noqa: E402

configure_engine(os.environ["DATABASE_URL"])
