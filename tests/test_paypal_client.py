import base64
import json

import httpx
import pytest

from app.paypal_client import (MockPayPalClient, PayPalClient, PayPalError, approve_link,
                               build_order_payload, capture_info)

BASE = "https://api-m.sandbox.paypal.com"


class FakePayPal:
    """Records requests and answers like the sandbox."""

    def __init__(self):
        self.calls = []
        self.token_calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if path == "/v1/oauth2/token":
            self.token_calls += 1
            assert request.headers["authorization"] == "Basic " + base64.b64encode(b"cid:secret").decode()
            assert b"grant_type=client_credentials" in request.content
            return httpx.Response(200, json={"access_token": "A21AA-test", "expires_in": 32400})
        assert request.headers["authorization"] == "Bearer A21AA-test"
        if path == "/v2/checkout/orders" and request.method == "POST":
            body = json.loads(request.content)
            if body["purchase_units"][0]["payee"]["email_address"] == "bad@example.com":
                return httpx.Response(422, json={"name": "UNPROCESSABLE_ENTITY", "message": "PAYEE_ACCOUNT_INVALID",
                                                 "debug_id": "dbg123"})
            if "vault_id" in body.get("payment_source", {}).get("paypal", {}):
                return httpx.Response(201, json={"id": "5O1", "status": "COMPLETED", "purchase_units": [
                    {"payments": {"captures": [{"id": "CAP1", "status": "COMPLETED",
                                                "amount": {"currency_code": "USD", "value": "16.79"}}]}}]})
            return httpx.Response(200, json={"id": "5O2", "status": "PAYER_ACTION_REQUIRED", "links": [
                {"rel": "self", "href": f"{BASE}/v2/checkout/orders/5O2"},
                {"rel": "payer-action", "href": "https://www.sandbox.paypal.com/checkoutnow?token=5O2"}]})
        if path == "/v2/checkout/orders/5O2/capture":
            return httpx.Response(201, json={"id": "5O2", "status": "COMPLETED", "purchase_units": [
                {"payments": {"captures": [{"id": "CAP2", "status": "COMPLETED",
                                            "amount": {"currency_code": "USD", "value": "74.32"}}]}}]})
        if path == "/v3/vault/setup-tokens":
            return httpx.Response(201, json={"id": "SETUP1", "status": "PAYER_ACTION_REQUIRED",
                                             "links": [{"rel": "approve", "href": "https://sandbox.paypal.com/agreements/approve?approval_session_id=SETUP1"}]})
        if path == "/v3/vault/payment-tokens":
            assert json.loads(request.content)["payment_source"]["token"] == {"id": "SETUP1", "type": "SETUP_TOKEN"}
            return httpx.Response(201, json={"id": "vault42", "customer": {"id": "cust1"}})
        return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND", "message": "nope"})


def make():
    fake = FakePayPal()
    return PayPalClient("cid", "secret", BASE, transport=httpx.MockTransport(fake)), fake


def payload(**kw):
    base = dict(reference_id="SPZ-1", payee_email="supplier@biz.example", currency="USD",
                lines=[{"name": "6 x Amasi 2L", "sku": "maas", "amount": 12.16},
                       {"name": "R50 airtime voucher", "sku": "airtime", "amount": 2.69}],
                shipping=1.94, description="Restock", invoice_id="SPZ-1-x", custom_id="1",
                return_url="http://localhost:8000/api/paypal/return", cancel_url="http://localhost:8000/api/paypal/cancel")
    base.update(kw)
    return build_order_payload(**base)


def test_payload_amounts_add_up_and_payee_is_supplier():
    p = payload()
    pu = p["purchase_units"][0]
    assert p["intent"] == "CAPTURE"
    assert pu["payee"] == {"email_address": "supplier@biz.example"}
    items_sum = sum(float(i["unit_amount"]["value"]) * int(i["quantity"]) for i in pu["items"])
    bd = pu["amount"]["breakdown"]
    assert f"{items_sum:.2f}" == bd["item_total"]["value"] == "14.85"
    assert bd["shipping"]["value"] == "1.94"
    assert pu["amount"]["value"] == "16.79"
    assert p["payment_source"]["paypal"]["experience_context"]["return_url"].endswith("/api/paypal/return")


def test_payload_with_vault_id_skips_redirect():
    p = payload(vault_id="vault42")
    assert p["payment_source"] == {"paypal": {"vault_id": "vault42"}}


def test_token_is_cached_and_idempotency_header_sent():
    c, fake = make()
    c.create_order(payload(), request_id="spz-1-create")
    c.create_order(payload(), request_id="spz-2-create")
    assert fake.token_calls == 1
    orders = [r for r in fake.calls if r.url.path == "/v2/checkout/orders"]
    assert [r.headers["paypal-request-id"] for r in orders] == ["spz-1-create", "spz-2-create"]


def test_redirect_flow_then_capture():
    c, _ = make()
    o = c.create_order(payload(), request_id="r1")
    assert o["status"] == "PAYER_ACTION_REQUIRED"
    assert approve_link(o) == "https://www.sandbox.paypal.com/checkoutnow?token=5O2"
    cap = c.capture_order(o["id"], request_id="r1-cap")
    assert capture_info(cap) == {"id": "CAP2", "status": "COMPLETED", "amount": "74.32", "currency": "USD"}


def test_vaulted_order_completes_on_create():
    c, _ = make()
    o = c.create_order(payload(vault_id="vault42"), request_id="r2")
    assert o["status"] == "COMPLETED" and capture_info(o)["id"] == "CAP1"


def test_errors_surface_name_and_debug_id():
    c, _ = make()
    with pytest.raises(PayPalError) as ei:
        c.create_order(payload(payee_email="bad@example.com"), request_id="r3")
    assert ei.value.status == 422 and ei.value.debug_id == "dbg123" and "PAYEE_ACCOUNT_INVALID" in str(ei.value)


def test_vault_setup_and_payment_token():
    c, _ = make()
    st = c.create_setup_token("http://x/return", "http://x/cancel", request_id="s1")
    assert st["id"] == "SETUP1"
    assert c.create_payment_token("SETUP1", request_id="s2")["id"] == "vault42"


def test_mock_client_is_idempotent_and_can_decline():
    m = MockPayPalClient()
    a = m.create_order(payload(), request_id="same")
    b = m.create_order(payload(), request_id="same")
    assert a["id"] == b["id"] and a["status"] == "APPROVED"
    done = m.capture_order(a["id"], "cap")
    assert done["status"] == "COMPLETED"
    with pytest.raises(PayPalError):
        m.capture_order(a["id"], "cap2")  # can't capture twice
    d = m.create_order(payload(payee_email="decline@x.example"), request_id="d")
    with pytest.raises(PayPalError):
        m.capture_order(d["id"], "dc")
