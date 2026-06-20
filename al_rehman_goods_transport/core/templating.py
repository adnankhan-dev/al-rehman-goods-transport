import re
from urllib.parse import urlencode

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.routing import Mount

from .auth import get_optional_user
from .config import settings
from .flash import pop_flashes
from .paths import STATIC_DIR, TEMPLATES_DIR


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Matches {id}, {id:int}, {path:path} etc. in a route path. Parsing route.path is
# stable across Starlette versions (route.param_convertors and request.url_for
# name resolution were not).
_PATH_PARAM_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?::[^}]+)?\}")


def _static_asset_version(filename: str) -> str | None:
    # Stamp static URLs with the file's mtime so browsers re-fetch CSS/JS after an
    # edit instead of serving a stale cached copy (StaticFiles sends no Cache-Control).
    try:
        return str(int((STATIC_DIR / filename).stat().st_mtime))
    except (OSError, ValueError):
        return None


def iter_routes(routes, _seen=None):
    """Yield every route, recursing into nested routers and mounts.

    Starlette 1.x (which Render installs) stops flattening included routers into
    app.router.routes and instead nests them inside a single _IncludedRouter,
    so a top-level scan misses every APIRouter route. Walking the tree finds
    them regardless of Starlette version.
    """
    if _seen is None:
        _seen = set()
    for route in routes:
        if id(route) in _seen:
            continue
        _seen.add(id(route))
        yield route
        # Recurse into nested route collections (mounts, sub-apps, included routers).
        for holder in (route, getattr(route, "app", None), getattr(route, "router", None)):
            if holder is None:
                continue
            sub = getattr(holder, "routes", None)
            if sub is not None and sub is not routes:
                yield from iter_routes(sub, _seen)


def build_route_index(*route_collections) -> dict:
    """Map every named route to its route object.

    Built once at startup from the flat FastAPI APIRouter (whose .routes list
    is reliable across Starlette versions) plus the app's own top-level routes
    (static mount, health). This avoids depending on how Starlette nests
    included routers in app.router.routes, which changed in 1.x.
    """
    index: dict = {}
    for routes in route_collections:
        for route in iter_routes(routes):
            name = getattr(route, "name", None)
            if name and name not in index:
                index[name] = route
    return index


def _find_route(request: Request, route_name: str):
    index = getattr(request.app.state, "route_index", None)
    if index:
        route = index.get(route_name)
        if route is not None:
            return route
    # Fallback: walk the live route tree (covers any route not in the prebuilt index).
    for route in iter_routes(request.app.router.routes):
        if getattr(route, "name", None) == route_name:
            return route
    return None


def template_url_for(request: Request, route_name: str, **params) -> str:
    """Resolve a named route to a root-relative URL.

    We build the path directly from the route table instead of calling
    request.url_for(). Starlette's named-route resolution has proven unreliable
    across versions on the deployed server (raising NoMatchFound for APIRouter
    routes that plainly exist), and that previously 500'd whole pages. Building
    the path ourselves is version-independent, proxy-safe (root-relative URLs
    avoid http/https mismatches behind Render's proxy), and never raises.
    """
    if route_name == "static" and "filename" in params:
        params["path"] = params.pop("filename")
        version = _static_asset_version(params["path"])
        if version is not None:
            params.setdefault("v", version)

    route = _find_route(request, route_name)
    if route is None:
        # Unknown route name: fail soft so a single bad link never 500s the page.
        return "#"

    if isinstance(route, Mount):
        # e.g. the StaticFiles "static" mount — the remaining "path" is the asset.
        base = getattr(route, "path", "") or ""
        sub = str(params.pop("path", "")).lstrip("/")
        url = f"{base}/{sub}" if sub else base
    else:
        def _substitute(match: "re.Match[str]") -> str:
            key = match.group(1)
            return str(params.pop(key)) if key in params else match.group(0)

        url = _PATH_PARAM_RE.sub(_substitute, getattr(route, "path", "") or "")

    # Anything left over becomes the query string.
    if params:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urlencode(params, doseq=True)}"

    return url or "/"


def _base_context(request: Request, **context):
    base_context = {
        "request": request,
        "config": {"APP_NAME": settings.app_name},
        "current_user": get_optional_user(request),
        "flashes": pop_flashes(request),
        "url_for": lambda route_name, **params: template_url_for(request, route_name, **params),
    }
    base_context.update(context)
    return base_context


def render_template(request: Request, template_name: str, status_code: int = 200, **context):
    return templates.TemplateResponse(request, template_name, _base_context(request, **context), status_code=status_code)


def render_template_string(request: Request, template_name: str, **context) -> str:
    return templates.get_template(template_name).render(_base_context(request, **context))
