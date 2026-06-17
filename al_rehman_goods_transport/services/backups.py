import sqlite3
from datetime import date, datetime
from pathlib import Path

from ..core.config import settings

AUTO_BACKUP_PREFIX = "auto_backup_"
DEFAULT_RETENTION = 14


def _database_path():
    prefix = "sqlite:///"
    database_url = settings.database_url or ""
    if not database_url.startswith(prefix):
        return None
    return Path(database_url[len(prefix):])


def backup_directory():
    database_path = _database_path()
    if database_path is None:
        return None
    return database_path.parent / "backups"


def run_automatic_backup(retention=DEFAULT_RETENTION):
    """Write one dated backup of the SQLite database per day and prune old ones.

    Uses the sqlite3 backup API so a consistent copy is taken even while the
    app holds open connections. No-op for non-SQLite databases or when
    today's backup already exists.
    """
    database_path = _database_path()
    if database_path is None or not database_path.exists():
        return None

    backup_dir = backup_directory()
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{AUTO_BACKUP_PREFIX}{date.today():%Y%m%d}.db"
    if target.exists():
        return target

    source_connection = sqlite3.connect(str(database_path))
    target_connection = sqlite3.connect(str(target))
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()

    _prune_old_backups(backup_dir, retention)
    return target


def _prune_old_backups(backup_dir, retention):
    backups = sorted(backup_dir.glob(f"{AUTO_BACKUP_PREFIX}*.db"))
    for old_backup in backups[:-retention] if retention > 0 else []:
        try:
            old_backup.unlink()
        except OSError:
            pass


def automatic_backup_status():
    backup_dir = backup_directory()
    if backup_dir is None or not backup_dir.exists():
        return {"enabled": _database_path() is not None, "count": 0, "latest": None, "directory": backup_dir}

    backups = sorted(backup_dir.glob(f"{AUTO_BACKUP_PREFIX}*.db"))
    latest = backups[-1] if backups else None
    return {
        "enabled": True,
        "count": len(backups),
        "latest": latest.name if latest else None,
        "latest_time": datetime.fromtimestamp(latest.stat().st_mtime) if latest else None,
        "directory": str(backup_dir),
    }
