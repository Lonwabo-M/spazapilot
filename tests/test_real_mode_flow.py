"""Agent loop wired to the REAL PayPalClient, with PayPal's HTTP mocked.
Covers both sandbox paths: redirect approval and linked wallet (vault_id)."""
import json

import httpx

from app import agent, db
from app.config import settings
from app.paypal_client import PayPalClient
from tests.test_paypal_client import BASE, FakePayPal

QUICK = "sugar 2, maas iphelile, need 3 bread, airtime R50"


def real_mode(monkeypatch):
    monkeypatch.setattr(settings, "force_mock", False)
    monkeypatch.setattr(settings, "paypal_client_id", "cid")
    monkeypatch.setattr(settings, "paypal_client_secret", "secret")
    monkeypatch.setattr(settings, "paypal_default_payee", "supplier@biz.example")
    monkeypatch.setattr(settings, "paypal_vault_id", "")
    fake = FakePayPal()
    agent.set_paypal_client(PayPalClient("cid", "secret", BASE, transport=httpx.MockTransport(fake)))
    return fake


def test_redirect_path_then_return_captures(client, monkeypatch):
    fake = real_mode(monkeypatch)
    items = client.post("/api/parse", json={"text": QUICK}).json()["items"]
    run = client.post("/api/agent/run", json={"items": items}).json()
    o = run["orders"][0]
    assert o["status"] == "awaiting_paypal"
    assert o["approve_url"] == "https://www.sandbox.paypal.com/checkoutnow?token=5O2"
    sent = json.loads([r for r in fake.calls if r.url.path == "/v2/checkout/orders"][0].content)
    assert sent["purchase_units"][0]["payee"]["email_address"] == "supplier@biz.example"
    assert sent["payment_source"]["paypal"]["experience_context"]["return_url"].endswith("/api/paypal/return")
    # budget already counts the in-flight order
    assert client.get("/api/config").json()["week"]["committed"] == o["total_zar"]

    r = client.get("/api/paypal/return?token=5O2", follow_redirects=False)
    assert r.status_code == 302
    paid = db.order_row(o["id"])
    assert paid["status"] == "paid" and paid["paypal_capture_id"] == "CAP2" and paid["receipt"]


def test_linked_wallet_pays_without_redirect(client, monkeypatch):
    fake = real_mode(monkeypatch)
    monkeypatch.setattr(settings, "paypal_vault_id", "vault42")
    items = client.post("/api/parse", json={"text": QUICK}).json()["items"]
    o = client.post("/api/agent/run", json={"items": items}).json()["orders"][0]
    assert o["status"] == "paid" and o["paypal_capture_id"] == "CAP1"
    sent = json.loads([r for r in fake.calls if r.url.path == "/v2/checkout/orders"][0].content)
    assert sent["payment_source"] == {"paypal": {"vault_id": "vault42"}}
    assert not any(r.url.path.endswith("/capture") for r in fake.calls)


def test_paypal_error_marks_failed_and_charges_nothing(client, monkeypatch):
    real_mode(monkeypatch)
    monkeypatch.setattr(settings, "paypal_default_payee", "bad@example.com")
    items = client.post("/api/parse", json={"text": QUICK}).json()["items"]
    o = client.post("/api/agent/run", json={"items": items}).json()["orders"][0]
    assert o["status"] == "failed" and "PAYEE_ACCOUNT_INVALID" in o["error"] and "dbg123" in o["error"]
    assert client.get("/api/config").json()["week"]["committed"] == 0


def test_wallet_linking_flow(client, monkeypatch):
    real_mode(monkeypatch)
    r = client.post("/api/paypal/link-wallet").json()
    assert r["setup_token"] == "SETUP1" and "approval_session_id=SETUP1" in r["approve_url"]
    resp = client.get("/api/paypal/wallet-return", follow_redirects=False)
    assert resp.status_code == 302
    assert db.get_setting("paypal_vault_id") == "vault42"
    assert client.get("/api/config").json()["wallet_linked"] is True
