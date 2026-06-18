import re
from urllib.parse import urlencode

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.routing import Mount, NoMatchFound

from .auth import get_optional_user
from .config import settings
from .flash import pop_flashes
from .paths import STATIC_DIR, TEMPLATES_DIR


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Matches {id}, {id:int}, {path:path} etc. in a route path. Parsing route.path is
# stable across Starlette versions (route.param_convertors was not).
_PATH_PARAM_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?::[^}]+)?\}")


def _static_asset_version(filename: str) -> str | None:
    # Stamp static URLs with the file's mtime so browsers re-fetch CSS/JS after an
    # edit instead of serving a stale cached copy (StaticFiles sends no Cache-Control).
    try:
        return str(int((STATIC_DIR / filename).stat().st_mtime))
    except (OSError, ValueError):
        return None


def _route_path_param_names(request: Request, route_name: str) -> set[str]:
    for route in request.app.router.routes:
        if getattr(route, "name", None) == route_name:
            # Mounts (e.g. the StaticFiles "static" mount) always take a "path" param.
            if isinstance(route, Mount):
                return {"path"}
            return set(_PATH_PARAM_RE.findall(getattr(route, "path", "") or ""))
    return set()


def template_url_for(request: Request, route_name: str, **params) -> str:
    if route_name == "static" and "filename" in params:
        params["path"] = params.pop("filename")
        version = _static_asset_version(params["path"])
        if version is not None:
            params.setdefault("v", version)

    path_param_names = _route_path_param_names(request, route_name)
    path_params = {key: value for key, value in params.items() if key in path_param_names}
    query_params = {key: value for key, value in params.items() if key not in path_param_names}

    url = None
    # 1) Use our introspected path/query split.
    try:
        url = str(request.url_for(route_name, **path_params))
    except NoMatchFound:
        url = None
    # 2) Route introspection differs across Starlette versions; if the split was
    #    wrong, retry treating every supplied param as a path param.
    if url is None and params:
        try:
            url = str(request.url_for(route_name, **params))
            query_params = {}
        except NoMatchFound:
            url = None
    # 3) Last resort: the route takes no path params.
    if url is None:
        url = str(request.url_for(route_name))

    if query_params:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urlencode(query_params, doseq=True)}"

    return url


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
