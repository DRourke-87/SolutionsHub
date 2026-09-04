from __future__ import annotations

import re
from datetime import timedelta

from app.db import utcnow
from app.models import MagicLinkToken, User, UserRole
from tests.conftest import csrf_from, login


def test_unauthenticated_redirects_to_login(client):
    r = client.get("/")
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")


def test_login_flow_creates_user_and_bootstrap_admin(client, outbox, db):
    login(client, outbox, "admin@amentum.com")
    user = db.get(User, "admin@amentum.com")
    assert user is not None
    assert any(r.role == "admin" for r in db.query(UserRole).filter_by(email="admin@amentum.com"))
    assert "sign-in link" in outbox[0]["subject"]


def test_disallowed_domain_gets_identical_response_and_no_email(client, outbox):
    page = client.get("/login")
    token = csrf_from(page.text)
    r = client.post("/login", data={"email": "someone@gmail.com", "csrf_token": token})
    assert r.status_code == 200
    assert "Check your email" in r.text
    assert outbox == []


def test_all_three_domains_allowed(client, outbox):
    for domain in (
        "amentum.com",
        "global.amentum.com",
        "amentumcms.com",
        "us.amentum.com",
        "eu.us.amentum.com",
    ):
        login(client, outbox, f"user@{domain}")
    assert len(outbox) == 5


def test_magic_link_is_single_use(client, outbox):
    page = client.get("/login")
    token = csrf_from(page.text)
    client.post("/login", data={"email": "once@amentum.com", "csrf_token": token})
    link = re.search(r"(/auth/verify\?t=[^\s]+)", outbox[-1]["text"]).group(1)
    verify = client.get(link)
    assert verify.status_code == 200
    verify_csrf = csrf_from(verify.text)
    raw_token = re.search(r"t=([^&\s]+)", link).group(1)
    assert client.post("/auth/verify", data={"csrf_token": verify_csrf, "t": raw_token}).status_code == 303
    client.cookies.clear()
    again = client.get(link)
    assert again.status_code == 400
    assert "invalid or has expired" in again.text


def test_expired_token_rejected(client, outbox, db):
    page = client.get("/login")
    token = csrf_from(page.text)
    client.post("/login", data={"email": "late@amentum.com", "csrf_token": token})
    for t in db.query(MagicLinkToken).filter_by(email="late@amentum.com"):
        t.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    link = re.search(r"(/auth/verify\?t=[^\s]+)", outbox[-1]["text"]).group(1)
    assert client.get(link).status_code == 400


def test_csrf_required_on_post(client):
    r = client.post("/login", data={"email": "x@amentum.com"})
    assert r.status_code == 403


def test_logout_revokes_session(client, outbox):
    token = login(client, outbox, "bye@amentum.com")
    r = client.post("/logout", data={"csrf_token": token})
    assert r.status_code == 303
    assert client.get("/").status_code == 303


def test_security_headers_present(client):
    r = client.get("/login")
    assert "Content-Security-Policy" in r.headers
    assert r.headers["X-Frame-Options"] == "DENY"
