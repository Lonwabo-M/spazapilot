"""PayPal REST client (Orders v2 + Payment Method Tokens / Vault v3) and a
drop-in mock with the same interface and realistic response shapes.

Real endpoints used:
  POST /v1/oauth2/token                       client-credentials access token
  POST /v2/checkout/orders                    create order (supplier = payee)
  POST /v2/checkout/orders/{id}/capture       capture after approval
  GET  /v2/checkout/orders/{id}               reconcile status
  POST /v3/vault/setup-tokens                 owner links wallet once (optional)
  POST /v3/vault/payment-tokens               turn approved setup token into vault_id
"""
from __future__ import annotations

import base64
import time
import uuid
from typing import Any

import httpx


class PayPalError(RuntimeError):
    def __init__(self, status: int, name: str, message: str, debug_id: str | None = None, details: Any = None):
        super().__init__(f"PayPal {status} {name}: {message}")
        self.status, self.name, self.message, self.debug_id, self.details = status, name, message, debug_id, details


def money(v: float) -> str:
    return f"{round(v + 1e-9, 2):.2f}"


def build_order_payload(*, reference_id: str, payee_email: str, currency: str, lines: list[dict],
                        shipping: float, description: str, invoice_id: str, custom_id: str,
                        vault_id: str | None = None, return_url: str | None = None,
                        cancel_url: str | None = None, brand_name: str = "SpazaPilot") -> dict:
    """lines: [{"name", "sku", "amount"}] where amount is the LINE total in `currency`.
    Each line is sent as quantity 1 so tiny unit prices (airtime) never lose cents."""
    items = [{
        "name": l["name"][:127], "sku": l.get("sku", "")[:127], "quantity": "1",
        "unit_amount": {"currency_code": currency, "value": money(l["amount"])},
        "category": "PHYSICAL_GOODS",
    } for l in lines]
    item_total = sum(float(i["unit_amount"]["value"]) for i in items)
    total = item_total + round(shipping, 2)
    payload: dict[str, Any] = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "reference_id": reference_id[:256],
            "description": description[:127],
            "custom_id": custom_id[:127],
            "invoice_id": invoice_id[:127],
            "payee": {"email_address": payee_email},
            "amount": {
                "currency_code": currency, "value": money(total),
                "breakdown": {
                    "item_total": {"currency_code": currency, "value": money(item_total)},
                    "shipping": {"currency_code": currency, "value": money(shipping)},
                },
            },
            "items": items,
        }],
    }
    if vault_id:
        payload["payment_source"] = {"paypal": {"vault_id": vault_id}}
    elif return_url:
        payload["payment_source"] = {"paypal": {"experience_context": {
            "brand_name": brand_name, "user_action": "PAY_NOW", "shipping_preference": "NO_SHIPPING",
            "return_url": return_url, "cancel_url": cancel_url or return_url,
        }}}
    return payload


def approve_link(order: dict) -> str | None:
    for l in order.get("links", []):
        if l.get("rel") in ("approve", "payer-action"):
            return l.get("href")
    return None


def capture_info(order: dict) -> dict | None:
    try:
        cap = order["purchase_units"][0]["payments"]["captures"][0]
        return {"id": cap["id"], "status": cap["status"], "amount": cap["amount"]["value"],
                "currency": cap["amount"]["currency_code"]}
    except (KeyError, IndexError, TypeError):
        return None


class PayPalClient:
    mode = "sandbox"

    def __init__(self, client_id: str, client_secret: str, base_url: str,
                 transport: httpx.BaseTransport | None = None, timeout: float = 30.0):
        self.client_id, self.client_secret, self.base_url = client_id, client_secret, base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)
        self._token: str | None = None
        self._token_exp = 0.0

    # -- auth ---------------------------------------------------------------
    def access_token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        r = self._http.post("/v1/oauth2/token", data={"grant_type": "client_credentials"},
                            headers={"Authorization": f"Basic {basic}", "Accept": "application/json"})
        self._raise(r)
        body = r.json()
        self._token = body["access_token"]
        self._token_exp = time.time() + int(body.get("expires_in", 300))
        return self._token

    def _headers(self, request_id: str | None = None) -> dict:
        h = {"Authorization": f"Bearer {self.access_token()}", "Content-Type": "application/json",
             "Prefer": "return=representation"}
        if request_id:
            h["PayPal-Request-Id"] = request_id  # idempotency: a retry can never double-charge
        return h

    @staticmethod
    def _raise(r: httpx.Response) -> None:
        if r.status_code < 400:
            return
        try:
            b = r.json()
        except ValueError:
            b = {}
        raise PayPalError(r.status_code, b.get("name") or b.get("error", "ERROR"),
                          b.get("message") or b.get("error_description") or r.text[:200],
                          b.get("debug_id"), b.get("details"))

    # -- orders ---------------------------------------------------------------
    def create_order(self, payload: dict, request_id: str) -> dict:
        r = self._http.post("/v2/checkout/orders", json=payload, headers=self._headers(request_id))
        self._raise(r)
        return r.json()

    def capture_order(self, order_id: str, request_id: str) -> dict:
        r = self._http.post(f"/v2/checkout/orders/{order_id}/capture", json={}, headers=self._headers(request_id))
        self._raise(r)
        return r.json()

    def get_order(self, order_id: str) -> dict:
        r = self._http.get(f"/v2/checkout/orders/{order_id}", headers=self._headers())
        self._raise(r)
        return r.json()

    # -- vault (optional: lets the agent pay without a redirect) ---------------
    def create_setup_token(self, return_url: str, cancel_url: str, request_id: str) -> dict:
        payload = {"payment_source": {"paypal": {
            "description": "SpazaPilot restock payments to your approved suppliers",
            "permit_multiple_payment_tokens": False, "usage_pattern": "IMMEDIATE",
            "usage_type": "MERCHANT", "customer_type": "CONSUMER",
            "experience_context": {"brand_name": "SpazaPilot", "shipping_preference": "NO_SHIPPING",
                                   "return_url": return_url, "cancel_url": cancel_url},
        }}}
        r = self._http.post("/v3/vault/setup-tokens", json=payload, headers=self._headers(request_id))
        self._raise(r)
        return r.json()

    def create_payment_token(self, setup_token_id: str, request_id: str) -> dict:
        payload = {"payment_source": {"token": {"id": setup_token_id, "type": "SETUP_TOKEN"}}}
        r = self._http.post("/v3/vault/payment-tokens", json=payload, headers=self._headers(request_id))
        self._raise(r)
        return r.json()


class MockPayPalClient:
    """Simulates the sandbox so the full agent loop runs with no credentials.
    Behaves like the 'linked wallet' path: orders are pre-approved and capture
    immediately. A payee email containing 'decline' simulates a failed capture."""
    mode = "mock"

    def __init__(self) -> None:
        self.orders: dict[str, dict] = {}
        self.requests: dict[str, dict] = {}

    @staticmethod
    def _id(prefix: str = "") -> str:
        return (prefix + uuid.uuid4().hex.upper())[:17]

    def create_order(self, payload: dict, request_id: str) -> dict:
        if request_id in self.requests:  # idempotent like the real API
            return self.requests[request_id]
        pu = payload["purchase_units"][0]
        oid = self._id("MOCK")
        order = {
            "id": oid, "intent": "CAPTURE", "status": "APPROVED",
            "purchase_units": [{**pu, "payments": {}}],
            "links": [{"href": f"https://api-m.sandbox.paypal.com/v2/checkout/orders/{oid}", "rel": "self", "method": "GET"},
                      {"href": f"https://api-m.sandbox.paypal.com/v2/checkout/orders/{oid}/capture", "rel": "capture", "method": "POST"}],
        }
        self.orders[oid] = order
        self.requests[request_id] = order
        return order

    def capture_order(self, order_id: str, request_id: str) -> dict:
        order = self.orders.get(order_id)
        if not order:
            raise PayPalError(404, "RESOURCE_NOT_FOUND", "The specified resource does not exist.", "mockdebug")
        if order["status"] == "COMPLETED":
            raise PayPalError(422, "UNPROCESSABLE_ENTITY", "ORDER_ALREADY_CAPTURED", "mockdebug")
        pu = order["purchase_units"][0]
        if "decline" in pu["payee"]["email_address"]:
            raise PayPalError(422, "UNPROCESSABLE_ENTITY", "INSTRUMENT_DECLINED", "mockdebug")
        amt = pu["amount"]
        pu["payments"] = {"captures": [{"id": self._id("CAP"), "status": "COMPLETED",
                                        "amount": {"currency_code": amt["currency_code"], "value": amt["value"]},
                                        "final_capture": True}]}
        order["status"] = "COMPLETED"
        order["payer"] = {"email_address": "owner-sandbox@personal.example", "payer_id": "MOCKPAYER1"}
        return order

    def get_order(self, order_id: str) -> dict:
        if order_id not in self.orders:
            raise PayPalError(404, "RESOURCE_NOT_FOUND", "The specified resource does not exist.", "mockdebug")
        return self.orders[order_id]

    def create_setup_token(self, return_url: str, cancel_url: str, request_id: str) -> dict:
        tid = self._id("SETUP")
        return {"id": tid, "status": "PAYER_ACTION_REQUIRED",
                "links": [{"rel": "approve", "href": f"{return_url}{'&' if '?' in return_url else '?'}approval_token_id={tid}"}]}

    def create_payment_token(self, setup_token_id: str, request_id: str) -> dict:
        return {"id": "mockvault" + setup_token_id[-6:].lower(), "customer": {"id": "mockcustomer"}}
