from __future__ import annotations

import hmac
import secrets
from fastapi import HTTPException, Request


def verify_admin(username: str, password: str, configured_username: str, configured_password: str) -> bool:
    if not configured_password:
        return False
    return hmac.compare_digest(username.encode(), configured_username.encode()) and hmac.compare_digest(password.encode(), configured_password.encode())


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return str(token)


def check_csrf(request: Request, submitted: str) -> None:
    expected = request.session.get("csrf")
    if not expected or not hmac.compare_digest(str(expected), str(submitted)):
        raise HTTPException(403, "Form expired. Reload the page and try again.")


def require_admin(request: Request) -> str:
    username = request.session.get("admin")
    if not username:
        raise HTTPException(status_code=303, detail="Login required", headers={"Location": "/login"})
    return str(username)
