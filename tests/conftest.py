"""Test fixtures. Uses PostgreSQL when TEST_DATABASE_URL is set (CI, local dev server), else SQLite."""

from __future__ import annotations

import os
import re
import tempfile

# Settings are read once at import, so configure the environment before importing the app.
_TMP = tempfile.mkdtemp(prefix="sh-test-")
os.environ.update(
    {
        "APP_ENV": "test",
        "SECRET_KEY": "test-secret-key-0123456789abcdef",
        "BASE_URL": "http://testserver",
        "DATABASE_URL": os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_TMP}/test.sqlite3"),
        "ALLOWED_EMAIL_DOMAINS": "amentum.com,global.amentum.com,amentumcms.com,us.amentum.com,*.amentum.com",
        "BOOTSTRAP_ADMIN_EMAIL": "admin@amentum.com",
        "EMAIL_BACKEND": "memory",
        "STORAGE_BACKEND": "local",
        "LOCAL_STORAGE_PATH": f"{_TMP}/uploads",
        "SCHEDULER_ENABLED": "false",
        "LOGIN_RATE_PER_EMAIL_PER_HOUR": "50",
        "LOGIN_RATE_PER_IP_PER_HOUR": "500",
        "VERIFY_FAILURES_PER_IP_PER_HOUR": "500",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import models  # noqa: E402,F401
from app.db import Base, get_engine, get_sessionmaker  # noqa: E402
from app.enums import Role  # noqa: E402
from app.models import User, UserRole  # noqa: E402
from app.seed import seed_reference_data  # noqa: E402
from app.services.email import MemoryEmailBackend, get_email_backend, set_email_backend_for_tests  # noqa: E402


@pytest.fixture(scope="session")
def engine():
    eng = get_engine()
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    with get_sessionmaker()() as db:
        seed_reference_data(db)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def db(engine):
    s = get_sessionmaker()()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def clean_tables(engine):
    """Truncate mutable tables between tests; keep reference data."""
    yield
    keep = {"business_groups", "capability_areas", "capabilities", "publish_destinations", "alembic_version"}
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            names = [t.name for t in reversed(Base.metadata.sorted_tables) if t.name not in keep]
            conn.execute(text("TRUNCATE " + ", ".join(f'"{n}"' for n in names) + " RESTART IDENTITY CASCADE"))
        else:
            for t in reversed(Base.metadata.sorted_tables):
                if t.name not in keep:
                    conn.execute(t.delete())


@pytest.fixture()
def outbox():
    backend = MemoryEmailBackend()
    set_email_backend_for_tests(backend)
    yield backend.outbox
    set_email_backend_for_tests(None)


@pytest.fixture()
def client(engine, outbox):
    from app.main import app

    with TestClient(app, follow_redirects=False) as c:
        yield c


def grant(db, email: str, role: Role, business_group_id: int | None = None) -> None:
    email = email.lower()
    if db.get(User, email) is None:
        db.add(User(email=email, domain=email.split("@")[1]))
        db.flush()
    db.add(UserRole(email=email, role=role.value, business_group_id=business_group_id, granted_by_email="test"))
    db.commit()


def csrf_from(html: str) -> str:
    m = re.search(r'name="csrf-token" content="([^"]+)"', html)
    assert m, "csrf meta tag missing"
    return m.group(1)


def login(client: TestClient, outbox: list, email: str) -> str:
    """Drive the magic-link flow through the HTTP layer. Returns the CSRF token for the signed-in session."""
    client.cookies.clear()
    page = client.get("/login")
    assert page.status_code == 200
    token = csrf_from(page.text)
    before = len(outbox)
    r = client.post("/login", data={"email": email, "csrf_token": token, "next": "/"})
    assert r.status_code == 200, r.text
    assert len(outbox) == before + 1, "expected one sign-in email"
    link = re.search(r"(http://testserver/auth/verify\?t=[^\s]+)", outbox[-1]["text"]).group(1)
    verify_path = link.replace("http://testserver", "")
    r = client.get(verify_path)
    assert r.status_code == 200, r.text
    verify_csrf = csrf_from(r.text)
    raw_token = re.search(r"t=([^&\s]+)", verify_path).group(1)
    r = client.post("/auth/verify", data={"csrf_token": verify_csrf, "t": raw_token})
    assert r.status_code == 303, r.text
    home = client.get("/")
    assert home.status_code == 200
    return csrf_from(home.text)


__all__ = ["grant", "login", "csrf_from", "get_email_backend"]
