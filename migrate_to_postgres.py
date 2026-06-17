"""One-time data migration: SQLite (data/app.db) -> Supabase Postgres.

Usage (run locally, where data/app.db lives):

    pip install -r requirements.txt
    python migrate_to_postgres.py "postgresql+psycopg://postgres.<ref>:<password>@<host>:5432/postgres?sslmode=require"

The target must use the SQLAlchemy + psycopg scheme (postgresql+psycopg://).
Use the Supabase "Session pooler" connection string (IPv4-compatible).

It creates the full schema on the target, copies every table in
foreign-key-safe order, then fixes the Postgres id sequences. Re-running it
against a non-empty target will fail on duplicate keys — only run it once,
into a fresh Supabase database.
"""

import sys
from pathlib import Path

from sqlalchemy import create_engine, text

# Import models so all tables are registered on Base.metadata.
from al_rehman_goods_transport.core.database import Base
from al_rehman_goods_transport.models import *  # noqa: F401,F403

SOURCE_URL = "sqlite:///" + (Path(__file__).resolve().parent / "data" / "app.db").as_posix()


def main():
    if len(sys.argv) < 2:
        print("ERROR: pass the target Postgres URL as the first argument.")
        print('Example: python migrate_to_postgres.py "postgresql+psycopg://postgres.<ref>:<pwd>@<host>:5432/postgres?sslmode=require"')
        raise SystemExit(1)

    target_url = sys.argv[1]
    if not target_url.startswith("postgresql+psycopg://"):
        print("ERROR: target URL must start with postgresql+psycopg://")
        print("Take the Supabase 'Session pooler' URI and replace 'postgresql://' with 'postgresql+psycopg://'.")
        raise SystemExit(1)

    source_engine = create_engine(SOURCE_URL)
    target_engine = create_engine(target_url)

    print("Creating schema on the target database ...")
    Base.metadata.create_all(bind=target_engine)

    tables = Base.metadata.sorted_tables  # parents before children (FK-safe)
    preparer = target_engine.dialect.identifier_preparer

    with source_engine.connect() as source, target_engine.begin() as target:
        for table in tables:
            rows = [dict(row._mapping) for row in source.execute(table.select())]
            if rows:
                target.execute(table.insert(), rows)
            print(f"  {table.name:32} {len(rows)} rows")

        # Advance id sequences so new inserts don't collide with copied ids.
        print("Fixing id sequences ...")
        for table in tables:
            if "id" in table.c:
                quoted = preparer.quote(table.name)
                target.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence('{quoted}', 'id'), "
                        f"GREATEST(COALESCE((SELECT MAX(id) FROM {quoted}), 1), 1))"
                    )
                )

    print("\nMigration complete. Verify a few rows in Supabase, then point the app's DATABASE_URL at it.")


if __name__ == "__main__":
    main()
