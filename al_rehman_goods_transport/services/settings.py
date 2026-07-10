import os
import sqlite3
from datetime import datetime
from pathlib import Path

from ..core.config import settings
from ..core.database import SessionLocal, engine, init_db
from ..extensions import db
from ..repositories import SettingsRepository
from .exceptions import ValidationError


class SettingsService:
    DIESEL_RATE_KEY = "diesel_rate"

    # Editable letterhead shown on every printed document. Keys map to
    # (settings key, default) — defaults preserve the original hardcoded text.
    LETTERHEAD_FIELDS = {
        "name": ("letterhead_name", "Al Rehman Goods Transport"),
        "address": ("letterhead_address", "Bahtr Mor Wah Cantt"),
        "contact1": ("letterhead_contact1", "Contact No. Ahsan Niazi 0307-2342827"),
        "contact2": ("letterhead_contact2", "Inam Khan - 0301-5749086"),
    }

    def __init__(self, session=None):
        self.session = session or db.session
        self.settings = SettingsRepository(self.session)

    def get_letterhead(self):
        result = {}
        for field, (key, default) in self.LETTERHEAD_FIELDS.items():
            setting = self.settings.get_by_key(key)
            value = setting.value if setting and setting.value not in (None, "") else default
            result[field] = value
        return result

    def update_letterhead(self, name=None, address=None, contact1=None, contact2=None):
        values = {"name": name, "address": address, "contact1": contact1, "contact2": contact2}
        try:
            for field, (key, _default) in self.LETTERHEAD_FIELDS.items():
                self.settings.set_value(key, (values.get(field) or "").strip())
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return self.get_letterhead()

    def get_diesel_rate(self):
        setting = self.settings.get_by_key(self.DIESEL_RATE_KEY)
        if setting is None or setting.value in (None, ""):
            return None
        try:
            return float(setting.value)
        except (TypeError, ValueError):
            return None

    def update_diesel_rate(self, diesel_rate):
        if diesel_rate in (None, ""):
            raise ValidationError("Diesel rate is required.")

        rate = float(diesel_rate)
        if rate <= 0:
            raise ValidationError("Diesel rate must be greater than zero.")

        try:
            self.settings.set_value(self.DIESEL_RATE_KEY, f"{rate:.4f}")
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return rate

    def diesel_rate_context(self):
        rate = self.get_diesel_rate()
        return {
            "diesel_rate": rate,
            "diesel_rate_display": f"{rate:.2f}" if rate is not None else "Not set",
        }

    def build_manual_entry_form_html(self, contractor_name, year, month, rows_per_day=20):
        """Printable blank monthly form for manual (handwritten) trip entries.

        One dated section per day of the chosen month, each with `rows_per_day`
        empty rows ready to fill in by hand and enter into the system later."""
        import calendar
        from datetime import date
        from html import escape
        from io import StringIO

        letterhead = self.get_letterhead()

        # (label, width). From/To kept wide; rates and quantities narrowed with
        # abbreviations (Del. Qty = delivered, Load. Qty = loading).
        columns = [
            ("#", "3%"),
            ("Vehicle No.", "12%"),
            ("From", "19%"),
            ("To", "19%"),
            ("Del. Qty", "7%"),
            ("Veh. Rate", "7%"),
            ("Con. Rate", "7%"),
            ("Plant", "11%"),
            ("Plant Receipt", "8%"),
            ("Load. Qty", "7%"),
        ]
        colgroup = "<colgroup>" + "".join(f"<col style='width:{w}'>" for _, w in columns) + "</colgroup>"
        header_cells = "".join(f"<th>{escape(label)}</th>" for label, _ in columns)
        days_in_month = calendar.monthrange(year, month)[1]
        month_label = date(year, month, 1).strftime("%B %Y")

        empty_row_template = (
            "<tr>" + "".join(
                (f"<td class='rownum'>{{n}}</td>" if i == 0 else "<td></td>")
                for i in range(len(columns))
            ) + "</tr>"
        )

        day_blocks = StringIO()
        for day in range(1, days_in_month + 1):
            current = date(year, month, day)
            rows = "".join(empty_row_template.format(n=n) for n in range(1, rows_per_day + 1))
            day_blocks.write(
                f"""
                <div class="day-block">
                    <div class="day-head">{current.strftime('%A, %d %b %Y')}</div>
                    <table class="entry-table">
                        {colgroup}
                        <thead><tr>{header_cells}</tr></thead>
                        <tbody>{rows}</tbody>
                    </table>
                </div>
                """
            )

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>Manual Entry Form — {escape(contractor_name)} — {escape(month_label)}</title>
    <style>
        body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 0; padding: 22px; color: #0f172a; }}
        .toolbar {{ display: flex; justify-content: flex-end; margin-bottom: 12px; }}
        .button {{ border: none; border-radius: 999px; background: #0f172a; color: #fff; padding: 9px 16px; cursor: pointer; font: inherit; }}
        .letterhead {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; border-bottom: 2px solid #0b2742; padding-bottom: 12px; margin-bottom: 12px; }}
        .letterhead-name {{ font-size: 1.5rem; font-weight: 800; color: #0b2742; }}
        .letterhead-line {{ color: #475569; font-size: 0.82rem; line-height: 1.5; }}
        .meta {{ display: flex; gap: 28px; font-size: 0.95rem; margin-bottom: 14px; }}
        .meta strong {{ color: #0b2742; }}
        .day-block {{ margin-bottom: 16px; }}
        .day-head {{ font-weight: 800; color: #0b2742; background: #e8f0fb; border: 1px solid #cbd9ec; border-bottom: none; padding: 6px 10px; border-radius: 8px 8px 0 0; font-size: 0.92rem; }}
        table.entry-table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
        .entry-table th, .entry-table td {{ border: 1px solid #b9c7dc; padding: 0; text-align: left; font-size: 0.8rem; }}
        .entry-table th {{ background: #f1f6fc; padding: 5px 6px; text-align: center; }}
        .entry-table td {{ height: 26px; }}
        .entry-table td.rownum {{ text-align: center; color: #94a3b8; width: 26px; font-size: 0.72rem; }}
        @media print {{
            body {{ padding: 0; }}
            .toolbar {{ display: none; }}
            tr {{ break-inside: avoid; }}
            .day-head {{ break-after: avoid; }}
            .day-block {{ break-inside: avoid; }}
            thead {{ display: table-header-group; }}
        }}
    </style>
</head>
<body>
    <div class="toolbar"><button type="button" class="button" onclick="window.print()">Print / Save PDF</button></div>
    <header class="letterhead">
        <div>
            <div class="letterhead-name">{escape(letterhead['name'])}</div>
            <div class="letterhead-line">{escape(letterhead['address'])}</div>
            <div class="letterhead-line">{escape(letterhead['contact1'])}</div>
            <div class="letterhead-line">{escape(letterhead['contact2'])}</div>
        </div>
        <div style="text-align:right;">
            <div style="text-transform:uppercase;letter-spacing:0.14em;color:#d97706;font-size:0.74rem;font-weight:700;">Manual Entry Form</div>
            <div class="letterhead-line">Printed: {datetime.now().strftime('%d %b %Y')}</div>
        </div>
    </header>
    <div class="meta">
        <div><strong>Contractor:</strong> {escape(contractor_name)}</div>
        <div><strong>Month:</strong> {escape(month_label)}</div>
    </div>
    {day_blocks.getvalue()}
</body>
</html>
"""

    def backup_supported(self):
        return self._database_path() is not None

    def database_filename(self):
        database_path = self._database_path()
        return database_path.name if database_path else "External database"

    def create_backup(self):
        database_path = self._database_path(required=True)
        source_connection = sqlite3.connect(str(database_path))
        backup_connection = sqlite3.connect(":memory:")
        try:
            source_connection.backup(backup_connection)
            content = backup_connection.serialize()
        finally:
            backup_connection.close()
            source_connection.close()

        filename = f"al_rehman_goods_transport_backup_{datetime.now():%Y%m%d_%H%M%S}.db"
        return {
            "filename": filename,
            "content": content,
            "media_type": "application/x-sqlite3",
        }

    def create_data_snapshot(self):
        """Universal backup that works on ANY database backend (including
        Supabase/Postgres): every table's rows exported to one JSON file."""
        import base64
        import json
        from datetime import date, datetime
        from decimal import Decimal

        from ..core.database import Base

        def _encode(value):
            if isinstance(value, (datetime, date)):
                return value.isoformat()
            if isinstance(value, Decimal):
                return float(value)
            if isinstance(value, (bytes, bytearray, memoryview)):
                return {"__b64__": base64.b64encode(bytes(value)).decode("ascii")}
            return str(value)

        bind = self.session.get_bind()
        tables = {}
        for table in Base.metadata.sorted_tables:
            result = self.session.execute(table.select())
            tables[table.name] = [dict(row._mapping) for row in result]

        payload = {
            "meta": {
                "app": "al_rehman_goods_transport",
                "format": "json-snapshot-v1",
                "dialect": getattr(bind.dialect, "name", "unknown") if bind else "unknown",
                "created_at": datetime.now().isoformat(timespec="seconds"),
            },
            "tables": tables,
        }
        content = json.dumps(payload, default=_encode, ensure_ascii=False).encode("utf-8")
        filename = f"al_rehman_goods_transport_backup_{datetime.now():%Y%m%d_%H%M%S}.json"
        return {"filename": filename, "content": content, "media_type": "application/json"}

    def restore_data_snapshot(self, filename, content):
        """Restore a JSON snapshot produced by create_data_snapshot: replace all
        rows in every table, in foreign-key-safe order. Works on Postgres + SQLite."""
        import base64
        import json
        from datetime import date, datetime

        from sqlalchemy import Date, DateTime, text

        from ..core.database import Base

        if not content:
            raise ValidationError("Select a backup file to restore.")
        if (filename or "").strip() and not filename.lower().endswith(".json"):
            raise ValidationError("Upload a .json data backup created by this ERP.")
        try:
            payload = json.loads(content.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValidationError("Backup file is not valid JSON.") from exc

        tables_data = payload.get("tables")
        if not isinstance(tables_data, dict):
            raise ValidationError("Backup file does not contain table data.")

        sorted_tables = list(Base.metadata.sorted_tables)
        bind = self.session.get_bind()
        is_postgres = getattr(bind.dialect, "name", "") == "postgresql"

        def _decode_row(table, row):
            out = {}
            for column in table.columns:
                if column.name not in row:
                    continue
                value = row[column.name]
                if value is not None:
                    if isinstance(value, str) and isinstance(column.type, DateTime):
                        value = datetime.fromisoformat(value)
                    elif isinstance(value, str) and isinstance(column.type, Date):
                        value = date.fromisoformat(value)
                    elif isinstance(value, dict) and "__b64__" in value:
                        value = base64.b64decode(value["__b64__"])
                out[column.name] = value
            return out

        try:
            self.session.rollback()
            for table in reversed(sorted_tables):
                self.session.execute(table.delete())
            for table in sorted_tables:
                rows = tables_data.get(table.name) or []
                cleaned = [_decode_row(table, row) for row in rows]
                if cleaned:
                    self.session.execute(table.insert(), cleaned)
            if is_postgres:
                # Re-sync auto-increment sequences to the restored max(id).
                for table in sorted_tables:
                    pk_columns = list(table.primary_key.columns)
                    if len(pk_columns) == 1 and pk_columns[0].name == "id":
                        self.session.execute(
                            text(
                                "SELECT setval(pg_get_serial_sequence(:tbl, 'id'), "
                                f'GREATEST((SELECT COALESCE(MAX(id), 0) FROM "{table.name}"), 1))'
                            ),
                            {"tbl": table.name},
                        )
            self.session.commit()
        except Exception as exc:
            self.session.rollback()
            raise ValidationError(f"Restore failed: {exc}") from exc

    def restore_backup(self, filename, content):
        # JSON snapshots are universal (and the only option on Postgres/Supabase).
        if (filename or "").strip().lower().endswith(".json"):
            return self.restore_data_snapshot(filename, content)

        database_path = self._database_path(required=True)
        uploaded_name = (filename or "").strip()

        if not content:
            raise ValidationError("Select a backup file to restore.")

        if uploaded_name and not uploaded_name.lower().endswith((".db", ".sqlite", ".sqlite3", ".backup", ".bak")):
            raise ValidationError("Upload a valid SQLite backup file.")

        self._validate_backup_content(content)

        try:
            self.session.rollback()
        except Exception:
            pass

        SessionLocal.remove()
        engine.dispose()

        temp_restore_path = database_path.with_suffix(f"{database_path.suffix}.restore")
        try:
            temp_restore_path.write_bytes(content)
            os.replace(str(temp_restore_path), str(database_path))
            init_db()
        except OSError as exc:
            temp_restore_path.unlink(missing_ok=True)
            raise ValidationError("Database restore could not be completed because the database file is currently in use.") from exc

    def _validate_backup_content(self, content: bytes):
        try:
            with sqlite3.connect(":memory:") as connection:
                connection.deserialize(content)
                integrity_result = connection.execute("PRAGMA integrity_check").fetchone()
                if not integrity_result or integrity_result[0] != "ok":
                    raise ValidationError("Backup file failed database integrity checks.")

                tables = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
                ).fetchone()
                if tables is None:
                    raise ValidationError("Backup file does not contain any application data tables.")
        except sqlite3.DatabaseError as exc:
            raise ValidationError("Uploaded file is not a valid SQLite backup.") from exc

    def _database_path(self, required=False):
        prefix = "sqlite:///"
        database_url = settings.database_url or ""
        if not database_url.startswith(prefix):
            if required:
                raise ValidationError("Backup and restore are only supported for SQLite databases.")
            return None

        database_path = Path(database_url[len(prefix):])
        if required and not database_path.exists():
            raise ValidationError("Database file could not be found for backup.")
        return database_path
