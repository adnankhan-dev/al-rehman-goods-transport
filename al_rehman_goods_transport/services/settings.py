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

    def __init__(self, session=None):
        self.session = session or db.session
        self.settings = SettingsRepository(self.session)

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
            <div class="letterhead-name">Al Rehman Goods Transport</div>
            <div class="letterhead-line">Bahtr Mor Wah Cantt</div>
            <div class="letterhead-line">Contact No. Ahsan Niazi 0307-2342827</div>
            <div class="letterhead-line">Inam Khan - 0301-5749086</div>
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

    def restore_backup(self, filename, content):
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
