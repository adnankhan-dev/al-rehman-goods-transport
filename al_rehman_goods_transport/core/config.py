import os
from dataclasses import dataclass

from .paths import DATA_DIR

DATA_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Settings:
    app_name: str = os.environ.get("APP_NAME", "Al Rehman Goods Transport")
    secret_key: str = os.environ.get("SECRET_KEY", "change-me-in-production")
    csrf_secret_key: str = os.environ.get("CSRF_SECRET_KEY", "change-me-too")
    database_url: str = os.environ.get("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'app.db').as_posix()}")
    debug: bool = os.environ.get("DEBUG", "true").lower() in {"1", "true", "yes", "on"}
    host: str = os.environ.get("HOST", "127.0.0.1")
    port: int = int(os.environ.get("PORT", "8000"))
    session_cookie: str = os.environ.get("SESSION_COOKIE_NAME", "al_rehman_transport_session")


settings = Settings()
