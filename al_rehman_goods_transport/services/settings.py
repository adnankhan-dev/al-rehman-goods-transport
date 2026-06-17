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
