"""End-to-end agent loop through the HTTP API in mock mode."""

NOTE = "iswekile 5, isonka 10, amasi iphelile, mealie meal 4, oil 6, amaqanda 2, airtime R50"


def run(client, text=NOTE):
    items = client.post("/api/parse", json={"text": text}).json()["items"]
    r = client.post("/api/agent/run", json={"items": items, "note": text})
    assert r.status_code == 200, r.text
    return r.json()


def test_config_reports_mock_mode(client):
    c = client.get("/api/config").json()
    assert c["ai_mode"] == "mock" and c["paypal_mode"] == "mock"
    assert len(c["suppliers"]) == 3


def test_full_loop_autopay_and_approval(client):
    r = run(client)
    statuses = {o["supplier_name"]: o["status"] for o in r["orders"]}
    assert "paid" in statuses.values()             # small order autopaid
    assert "awaiting_owner" in statuses.values()   # big order held for the owner
    paid = [o for o in r["orders"] if o["status"] == "paid"][0]
    assert paid["paypal_order_id"].startswith("MOCK") and paid["paypal_capture_id"].startswith("CAP")
    assert paid["total_zar"] <= 500 and "Paid R" in paid["receipt"]
    kinds = [e["kind"] for e in r["events"]]
    assert kinds[:2] == ["parse", "plan"] and "guardrail" in kinds and "paypal" in kinds and "receipt" in kinds

    held = [o for o in r["orders"] if o["status"] == "awaiting_owner"][0]
    assert client.get("/api/approvals").json()["orders"][0]["id"] == held["id"]
    approved = client.post(f"/api/orders/{held['id']}/approve").json()
    assert approved["status"] == "paid" and approved["receipt"]
    assert client.get("/api/approvals").json()["orders"] == []
    # approving twice is refused
    assert client.post(f"/api/orders/{held['id']}/approve").status_code == 409

    ledger = client.get("/api/ledger").json()
    assert all(o["status"] == "paid" for o in ledger["orders"])
    ins = client.get("/api/insights").json()
    assert ins["stats"]["orders"] == 2 and ins["insights"]


def test_budget_blocks_and_reject(client):
    client.put("/api/policy", json={"weekly_budget": 500, "auto_approve_limit": 400})
    r = run(client)
    assert any(o["status"] == "blocked" for o in r["orders"])
    blocked = [o for o in r["orders"] if o["status"] == "blocked"][0]
    assert "budget" in blocked["reasons"][0]
    assert client.post(f"/api/orders/{blocked['id']}/approve").status_code == 409
    assert client.post(f"/api/orders/{blocked['id']}/reject").json()["status"] == "rejected"
    assert client.get("/api/config").json()["week"]["committed"] <= 500


def test_autopay_off_holds_everything(client):
    client.put("/api/policy", json={"agent_enabled": False})
    r = run(client, "sugar 2, maas iphelile, need 3 bread, airtime R50")
    assert r["orders"] and all(o["status"] == "awaiting_owner" for o in r["orders"])


def test_owner_approval_rechecks_budget(client):
    r = run(client)
    held = [o for o in r["orders"] if o["status"] == "awaiting_owner"][0]
    client.put("/api/policy", json={"weekly_budget": 400})  # owner tightens budget before approving
    out = client.post(f"/api/orders/{held['id']}/approve").json()
    assert out["status"] == "blocked"


def test_repeat_order_within_24h_is_held(client):
    first = run(client, "sugar 2, maas iphelile, need 3 bread, airtime R50")
    assert all(o["status"] == "paid" for o in first["orders"])
    second = run(client, "sugar 2, maas iphelile, need 3 bread, airtime R50")
    assert all(o["status"] == "awaiting_owner" for o in second["orders"])
    assert any("repeat" in " ".join(o["reasons"]) for o in second["orders"])


def test_mock_wallet_link_roundtrip(client):
    r = client.post("/api/paypal/link-wallet").json()
    assert "approval_token_id=" in r["approve_url"]
    path = r["approve_url"].split("localhost:8000")[1]
    resp = client.get(path, follow_redirects=False)
    assert resp.status_code == 302


def test_photo_upload_mock(client):
    res = client.post("/api/parse-photo", files={"file": ("shelf.jpg", b"\xff\xd8fakejpeg", "image/jpeg")}).json()
    assert {i["product_id"] for i in res["items"]} == {"maize_meal", "cooking_oil", "candles"}


def test_rejects_unknown_products(client):
    r = client.post("/api/agent/run", json={"items": [{"product_id": "cigarettes", "qty": 2}]})
    assert r.status_code == 400
