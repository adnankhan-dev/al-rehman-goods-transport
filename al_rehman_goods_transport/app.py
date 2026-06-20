import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

if __package__ in {None, ""}:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    from al_rehman_goods_transport.api import api_router
    from al_rehman_goods_transport.core.auth import AnonymousUser
    from al_rehman_goods_transport.core.config import settings
    from al_rehman_goods_transport.core.database import SessionLocal, init_db
    from al_rehman_goods_transport.core.paths import STATIC_DIR
    from al_rehman_goods_transport.models import *  # noqa: F401,F403
    from al_rehman_goods_transport.services.backups import run_automatic_backup
else:
    from .api import api_router
    from .core.auth import AnonymousUser
    from .core.config import settings
    from .core.database import SessionLocal, init_db
    from .core.paths import STATIC_DIR
    from .models import *  # noqa: F401,F403
    from .services.backups import run_automatic_backup


async def _daily_backup_loop():
    while True:
        await asyncio.sleep(24 * 60 * 60)
        try:
            run_automatic_backup()
        except Exception:
            pass


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    try:
        run_automatic_backup()
    except Exception:
        pass
    backup_task = asyncio.create_task(_daily_backup_loop())
    try:
        yield
    finally:
        backup_task.cancel()


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=_lifespan)
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, session_cookie=settings.session_cookie)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.middleware("http")
    async def load_current_user(request, call_next):
        request.state.current_user = AnonymousUser()
        try:
            return await call_next(request)
        finally:
            SessionLocal.remove()

    @app.get("/health", name="health")
    def health_check():
        return {"status": "OK"}

    @app.get("/__diag/routes", name="diag_routes")
    def diag_routes():
        import starlette
        import fastapi

        from .core.templating import iter_routes

        bills = []
        for route in iter_routes(app.router.routes):
            name = getattr(route, "name", None)
            if name and "bill" in name:
                bills.append({"name": name, "path": getattr(route, "path", None)})
        return {
            "fastapi": fastapi.__version__,
            "starlette": starlette.__version__,
            "total_routes_found": sum(1 for _ in iter_routes(app.router.routes)),
            "bill_routes": bills,
        }

    init_db()
    app.include_router(api_router)
    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port, reload=False)
