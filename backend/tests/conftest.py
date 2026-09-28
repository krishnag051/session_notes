"""Test-suite bootstrap. The env-var override at the very top MUST run
before any `app.*` module is imported anywhere in the test session — same
discipline as agent-making's own conftest.py, and the prior TP-review
project's before that. Once DATABASE_URL is pointed at a disposable sqlite
file, every app module that reads `settings.database_url` (app.db.base's
engine/SessionLocal) resolves against the test database for the rest of
the process.

No real Postgres server is available in this environment to test
against — this uses a disposable sqlite file instead (schema created via
Base.metadata.create_all, not by running the real Alembic migration
against it; the migration itself is authored and reviewed for correctness
in alembic/versions/, but only structurally, not exercised against a live
Postgres here). Every model column here uses portable, dialect-generic
SQLAlchemy types for exactly this reason — see app/db/models.py's own
docstring.
"""
import os
import sys
import tempfile
from pathlib import Path

_TEST_DB_PATH = Path(tempfile.gettempdir()) / "session_notes_backend_test.db"
if _TEST_DB_PATH.exists():
    _TEST_DB_PATH.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"
# Never let a test run write into the real backend/data/uploads/ directory
# — a disposable temp dir instead, same reasoning as the test database above.
os.environ["UPLOAD_STORAGE_DIR"] = str(Path(tempfile.gettempdir()) / "session_notes_backend_test_uploads")

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db import models  # noqa: E402, F401 — registers every model on Base.metadata
from app.db.base import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _recreated_test_database():
    """Drops and recreates every table, unconditionally, once per test
    session — every run starts from genuinely empty schema, not leftover
    state from a prior run."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def _blocked_review_person_document(*args, **kwargs):
    raise RuntimeError(
        "BLOCKED by backend/tests/conftest.py::_block_real_api_calls: a test attempted to call "
        "app.agent_client.review_person_document — the exact seam this backend imports agent-making's "
        "real review function through — which would make a real, billed call to the Anthropic API. "
        "This is blocked for every test in this suite by default. If a test is deliberately meant to "
        "exercise the real API, mark it explicitly with @pytest.mark.real_api — and only run it with "
        "the user's explicit, per-instance approval (exact command + call count + cost estimate), per "
        "CLAUDE.md's hard rule. Never add that marker to a test, or run one that already has it, "
        "without that approval already granted for this specific run."
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_api: this test deliberately calls the real Anthropic API. Requires the user's explicit, "
        "per-instance approval (CLAUDE.md's hard rule) before every run -- never add or run this "
        "marker on your own judgment.",
    )


@pytest.fixture(autouse=True)
def _block_real_api_calls(request, monkeypatch):
    """Structural guardrail — makes it impossible for ANY test in this
    suite to reach the real Anthropic API through this backend's own
    import of agent-making's review function, regardless of which test
    file runs. The one escape hatch, @pytest.mark.real_api, is never
    itself permission — see the module docstring above.
    """
    if request.node.get_closest_marker("real_api") is not None:
        yield
        return
    import app.routers.person_documents as person_documents_module

    monkeypatch.setattr(person_documents_module, "review_person_document", _blocked_review_person_document)
    yield


@pytest.fixture()
def db_session():
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = Session()
    yield session
    session.close()


@pytest.fixture()
def client():
    # raise_server_exceptions=False: several of this suite's own tests
    # (the real-API guardrail proof in particular) deliberately trigger an
    # unhandled exception inside a route and assert on the resulting 500
    # response — TestClient's default (True) would re-raise it into the
    # test process instead of returning a response to inspect.
    return TestClient(app, raise_server_exceptions=False)
