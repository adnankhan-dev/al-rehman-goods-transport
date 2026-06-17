from fastapi import Request


def flash(request: Request, message: str, category: str = "info"):
    messages = list(request.session.get("_flashes", []))
    messages.append({"category": category, "message": message})
    request.session["_flashes"] = messages


def pop_flashes(request: Request):
    return request.session.pop("_flashes", [])
