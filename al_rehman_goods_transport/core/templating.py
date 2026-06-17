import re
from urllib.parse import urlencode

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.routing import Mount, NoMatchFound

from .auth import get_optional_user
from .config import settings
from .flash import pop_flashes
from .paths import TEMPLATES_DIR


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Matches {id}, {id:int}, {path:path} etc. in a route path. Parsing route.path is
# stable across Starlette versions (route.param_convertors was not).
_PATH_PARAM_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?::[^}]+)?\}")


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

    path_param_names = _route_path_param_names(request, route_name)
    path_params = {key: value for key, value in params.items() if key in path_param_names}
    query_params = {key: value for key, value in params.items() if key not in path_param_names}

    try:
        url = str(request.url_for(route_name, **path_params))
    except NoMatchFound:
        url = str(request.url_for(route_name))

    if query_params:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urlencode(query_params, doseq=True)}"

    return url


def render_template(request: Request, template_name: str, status_code: int = 200, **context):
    base_context = {
        "request": request,
        "config": {"APP_NAME": settings.app_name},
        "current_user": get_optional_user(request),
        "flashes": pop_flashes(request),
        "url_for": lambda route_name, **params: template_url_for(request, route_name, **params),
    }
    base_context.update(context)
    return templates.TemplateResponse(request, template_name, base_context, status_code=status_code)
