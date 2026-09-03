from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app import policy
from app.config import get_settings
from app.enums import (
    CONTACT_ROLE_LABELS,
    DEPLOYMENT_LABELS,
    PIPELINE_STAGES,
    READINESS_LABELS,
    STATUS_LABELS,
    ContactRole,
    DeploymentStatus,
    ReadinessLevel,
    Role,
    Status,
)
from app.security import ANON_COOKIE, csrf_identity, csrf_token_for, sign_value, unsign_value
from app.workflow import waiting_on

FLASH_COOKIE = "sh_flash"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _fmt_dt(value: datetime | None, fmt: str = "%d %b %Y %H:%M") -> str:
    if not value:
        return ""
    return value.strftime(fmt) + " UTC"


def _fmt_date(value: datetime | None) -> str:
    return value.strftime("%d %b %Y") if value else ""


def _status_label(value: str | None) -> str:
    try:
        return STATUS_LABELS[Status(value)]
    except Exception:  # noqa: BLE001
        return str(value or "")


def _human_size(n: int | None) -> str:
    n = n or 0
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


templates.env.filters.update(dt=_fmt_dt, date=_fmt_date, status_label=_status_label, filesize=_human_size)
templates.env.globals.update(
    Status=Status,
    Role=Role,
    ContactRole=ContactRole,
    ReadinessLevel=ReadinessLevel,
    DeploymentStatus=DeploymentStatus,
    STATUS_LABELS=STATUS_LABELS,
    READINESS_LABELS=READINESS_LABELS,
    DEPLOYMENT_LABELS=DEPLOYMENT_LABELS,
    CONTACT_ROLE_LABELS=CONTACT_ROLE_LABELS,
    PIPELINE_STAGES=PIPELINE_STAGES,
    policy=policy,
    waiting_on=waiting_on,
)


def _encode_flash(messages: list[dict]) -> str:
    # base64url keeps the cookie value free of quotes, commas and spaces (browsers differ on quoted values)
    return base64.urlsafe_b64encode(json.dumps(messages).encode("utf-8")).decode("ascii").rstrip("=")


def pop_flash(request: Request) -> list[dict]:
    raw = unsign_value(request.cookies.get(FLASH_COOKIE))
    if not raw:
        return []
    try:
        padded = raw + "=" * (-len(raw) % 4)
        return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return []


def render(request: Request, name: str, status_code: int = 200, **ctx) -> Response:
    settings = get_settings()
    user = getattr(request.state, "user", None)
    flash = pop_flash(request)
    ctx.update(
        request=request,
        user=user,
        csrf_token=csrf_token_for(csrf_identity(request)),
        settings=settings,
        flash=flash,
        app_env=settings.app_env,
    )
    resp = templates.TemplateResponse(request, name, ctx, status_code=status_code)
    anon = getattr(request.state, "anon_id", None)
    if anon and not request.cookies.get(ANON_COOKIE):
        resp.set_cookie(
            ANON_COOKIE, sign_value(anon), httponly=True, samesite="lax", secure=settings.secure_cookies, path="/"
        )
    if flash:
        resp.delete_cookie(FLASH_COOKIE, path="/")
    return resp


def redirect(url: str, message: str | None = None, kind: str = "success", status_code: int = 303) -> RedirectResponse:
    resp = RedirectResponse(url, status_code=status_code)
    if message:
        resp.set_cookie(
            FLASH_COOKIE,
            sign_value(_encode_flash([{"kind": kind, "text": message}])),
            httponly=True,
            samesite="lax",
            secure=get_settings().secure_cookies,
            max_age=120,
            path="/",
        )
    return resp
