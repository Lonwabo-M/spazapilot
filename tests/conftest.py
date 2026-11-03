import os
import sys
from pathlib import Path

os.environ["SPAZAPILOT_FORCE_MOCK"] = "1"  # tests never touch real APIs
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app import agent, db  # noqa: E402
from app.config import settings  # noqa: E402
from app.paypal_client import MockPayPalClient  # noqa: E402


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    db._conn = None
    agent.set_paypal_client(MockPayPalClient())
    yield
    agent.set_paypal_client(None)
    db._conn = None


@pytest.fixture()
def client(fresh_db):
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)
