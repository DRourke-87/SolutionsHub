from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import auth as authsvc
from app.config import get_settings
from app.db import get_db
from app.routers.common import csrf_protect
from app.security import client_ip
from app.services import notifications as notify
from app.templating import redirect, render

router = APIRouter(tags=["auth"])


@router.get("/login")
def login_form(request: Request, next: str = "/", db: Session = Depends(get_db)):
    session, user = authsvc.load_session(db, request)
    if user:
        return RedirectResponse(authsvc.safe_next_path(next), status_code=303)
    request.state.user = None
    return render(request, "login.html", next=authsvc.safe_next_path(next), error=None)


@router.post("/login", dependencies=[Depends(csrf_protect)])
def login_submit(
    request: Request,
    email: str = Form(...),
    next: str = Form("/"),
    remember_me: bool = Form(False),
    db: Session = Depends(get_db),
):
    request.state.user = None
    settings = get_settings()
    ip = client_ip(request)
    try:
        raw, link = authsvc.request_magic_link(db, email, ip, next, remember_me)
    except authsvc.RateLimited as exc:
        db.commit()
        return render(request, "login.html", status_code=429, next=next, error=str(exc), email=email)
    dev_link = None
    if link:
        notify.queue(db, "magic_link", [email], None, link=link, minutes=settings.magic_link_ttl_minutes)
        db.commit()
        notify.send_pending(db)
        if settings.app_env.lower() == "dev" and settings.email_backend.lower() in ("console", "memory"):
            dev_link = link
    else:
        db.commit()
    return render(request, "check_email.html", email=email.strip(), dev_link=dev_link)


@router.get("/auth/verify")
def verify(request: Request, t: str = "", db: Session = Depends(get_db)):
    request.state.user = None
    try:
        session, next_path = authsvc.verify_magic_link(db, t, client_ip(request), request.headers.get("user-agent"))
    except authsvc.LoginError as exc:
        db.commit()
        return render(request, "login.html", status_code=400, next="/", error=str(exc))
    db.commit()
    resp = redirect(next_path, "You are signed in.")
    authsvc.set_session_cookie(resp, session)
    return resp


@router.post("/logout", dependencies=[Depends(csrf_protect)])
def logout(request: Request, db: Session = Depends(get_db)):
    from app.security import SESSION_COOKIE, unsign_value

    authsvc.revoke_session(db, unsign_value(request.cookies.get(SESSION_COOKIE)))
    db.commit()
    resp = redirect("/login", "You have signed out.")
    authsvc.clear_session_cookie(resp)
    return resp
