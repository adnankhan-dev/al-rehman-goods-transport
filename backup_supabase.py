r"""Download a full JSON backup of the live (Supabase/Postgres) database.

READ-ONLY: it only runs SELECT statements — it never creates, alters, or
deletes anything. It reflects whatever tables exist and dumps their rows.

Usage (PowerShell), using the connection string from
Supabase -> Project Settings -> Database (URI):

    $env:DATABASE_URL = "postgresql://postgres.<ref>:<PASSWORD>@<host>:5432/postgres?sslmode=require"
    .\.venv\Scripts\python.exe backup_supabase.py

Or pass it as an argument:

    .\.venv\Scripts\python.exe backup_supabase.py "postgresql://...."

It writes a file like  alrehman_supabase_backup_YYYYMMDD_HHMMSS.json  in this folder.
"""

import base64
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import MetaData, create_engine


def _normalize(url: str) -> str:
    # The app uses the SQLAlchemy "+psycopg" scheme; a plain Supabase URI works too.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _encode(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__b64__": base64.b64encode(bytes(value)).decode("ascii")}
    return str(value)


def main():
    raw_url = os.environ.get("DATABASE_URL") or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not raw_url:
        print("ERROR: set DATABASE_URL (or pass the connection string as an argument).")
        sys.exit(1)

    engine = create_engine(_normalize(raw_url))
    metadata = MetaData()
    metadata.reflect(bind=engine)

    tables = {}
    total_rows = 0
    with engine.connect() as connection:
        for table in metadata.sorted_tables:
            rows = [dict(row._mapping) for row in connection.execute(table.select())]
            tables[table.name] = rows
            total_rows += len(rows)
            print(f"  {table.name:24} {len(rows)} rows")

    payload = {
        "meta": {
            "app": "al_rehman_goods_transport",
            "format": "json-snapshot-v1",
            "dialect": engine.dialect.name,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        },
        "tables": tables,
    }
    filename = f"alrehman_supabase_backup_{datetime.now():%Y%m%d_%H%M%S}.json"
    with open(filename, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, default=_encode, ensure_ascii=False)

    print(f"\nDone. {len(tables)} tables, {total_rows} rows -> {filename}")


if __name__ == "__main__":
    main()
