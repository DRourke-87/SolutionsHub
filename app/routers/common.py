from __future__ import annotations

from fastapi import HTTPException, Request, status

from app.security import CSRF_FIELD, CSRF_HEADER, validate_csrf


async def csrf_protect(request: Request) -> None:
    """Dependency for state-changing routes. Accepts the form field or the HTMX header."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    token = request.headers.get(CSRF_HEADER)
    if not token:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form = await request.form()
            token = form.get(CSRF_FIELD)
    if not validate_csrf(request, token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing CSRF token. Please reload the page and try again.",
        )
