from __future__ import annotations

from sqlalchemy import select

from app.models import UserRole
from tests.conftest import grant, login


def test_non_admin_forbidden(client, outbox):
    login(client, outbox, "plain@amentum.com")
    assert client.get("/admin").status_code == 403


def test_admin_can_grant_and_revoke_roles(client, outbox, db):
    token = login(client, outbox, "admin@amentum.com")  # bootstrap admin
    assert client.get("/admin").status_code == 200
    outbox.clear()
    r = client.post(
        "/admin/users/grant",
        data={"csrf_token": token, "email": "new.reviewer@amentum.com", "role": "reviewer", "business_group_id": ""},
    )
    assert r.status_code == 303
    role = db.execute(select(UserRole).where(UserRole.email == "new.reviewer@amentum.com")).scalar_one()
    assert role.role == "reviewer" and role.revoked_at is None
    assert any("reviewer role has been granted" in m["subject"] for m in outbox)

    r = client.post(f"/admin/users/new.reviewer@amentum.com/revoke/{role.id}", data={"csrf_token": token})
    assert r.status_code == 303
    db.expire_all()
    assert db.get(UserRole, role.id).revoked_at is not None


def test_admin_cannot_grant_to_disallowed_domain(client, outbox, db):
    token = login(client, outbox, "admin@amentum.com")
    client.post("/admin/users/grant", data={"csrf_token": token, "email": "x@evil.com", "role": "admin"})
    assert db.execute(select(UserRole).where(UserRole.email == "x@evil.com")).first() is None


def test_admin_pages_render(client, outbox, db):
    login(client, outbox, "admin@amentum.com")
    for path in ("/admin/users", "/admin/reference", "/admin/notifications", "/admin/audit", "/admin/settings"):
        assert client.get(path).status_code == 200, path


def test_disabled_user_cannot_sign_in(client, outbox, db):
    token = login(client, outbox, "admin@amentum.com")
    login(client, outbox, "victim@amentum.com")
    token = login(client, outbox, "admin@amentum.com")
    r = client.post("/admin/users/victim@amentum.com/toggle-disabled", data={"csrf_token": token})
    assert r.status_code == 303
    client.cookies.clear()
    page = client.get("/login")
    from tests.conftest import csrf_from

    outbox.clear()
    client.post("/login", data={"email": "victim@amentum.com", "csrf_token": csrf_from(page.text)})
    assert outbox == []  # no link issued for a disabled account
    grant  # noqa: B018 - keep import used for readability of helper availability
