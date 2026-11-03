"""FastAPI app: JSON API + the single-page mobile UI in /static."""
from __future__ import annotations

import base64
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agent, db, parser
from .catalogue import PRODUCTS, SUPPLIERS
from .config import settings
from .optimizer import next_delivery

STATIC = Path(__file__).resolve().parent.parent / "static"
app = FastAPI(title="SpazaPilot", version="0.1.0",
              description="AI restocking and payment agent for spaza shops, paying suppliers with PayPal.")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class NoteIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


class Item(BaseModel):
    product_id: str
    qty: int = Field(..., ge=1, le=1000)
    urgent: bool = False
    name: str | None = None
    unit: str | None = None
    source_text: str | None = None


class RunIn(BaseModel):
    items: list[Item] = Field(..., min_length=1, max_length=40)
    note: str = ""
    source: str = "text"
    engine: str = ""


class PolicyIn(BaseModel):
    weekly_budget: float | None = Field(None, ge=0, le=1_000_000)
    auto_approve_limit: float | None = Field(None, ge=0, le=1_000_000)
    agent_enabled: bool | None = None
    max_qty_per_line: int | None = Field(None, ge=1, le=10_000)
    urgent_max_days: int | None = Field(None, ge=1, le=7)
    shop_name: str | None = Field(None, max_length=60)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/config")
def config():
    return {
        "ai_mode": "mock" if settings.ai_mock else f"{settings.ai_provider}:{settings.default_model()}",
        "paypal_mode": "mock" if settings.paypal_mock else settings.paypal_env,
        "wallet_linked": bool(agent.vault_id()) or settings.paypal_mock,
        "currency": settings.paypal_currency, "zar_per_usd": settings.zar_per_usd,
        "policy": db.get_policy(), "week": agent.week_summary(),
        "products": PRODUCTS,
        "suppliers": [{"id": sid, "name": s["name"], "area": s["area"], "min_order": s["min_order"],
                       "delivery_fee": s["delivery_fee"], "products": len(s["prices"]),
                       "next_delivery": next_delivery(s, agent.today_sast())[1].isoformat()}
                      for sid, s in SUPPLIERS.items()],
    }


@app.put("/api/policy")
def update_policy(p: PolicyIn):
    pol = db.set_policy({k: v for k, v in p.model_dump().items() if v is not None})
    db.log_event("owner", f"Owner updated guardrails: budget R{pol['weekly_budget']:.0f}/week, "
                 f"autopay up to R{pol['auto_approve_limit']:.0f}, autopay {'on' if pol['agent_enabled'] else 'off'}.")
    return pol


@app.post("/api/parse")
def parse(note: NoteIn):
    return parser.parse_note(note.text)


@app.post("/api/parse-photo")
async def parse_photo(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > 6_000_000:
        raise HTTPException(413, "Photo too large (max 6 MB).")
    try:
        return parser.parse_photo(base64.b64encode(data).decode(), file.content_type or "image/jpeg")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Could not read the photo: {e}") from e


@app.post("/api/agent/run")
def run(body: RunIn):
    items = [i.model_dump() for i in body.items if i.product_id in PRODUCTS]
    if not items:
        raise HTTPException(400, "No known products in the list.")
    return agent.run_agent(items, note=body.note, source=body.source, engine=body.engine)


@app.get("/api/runs/{run_id}")
def get_run(run_id: int):
    try:
        return agent.get_run(run_id)
    except IndexError:
        raise HTTPException(404, "Run not found")


@app.get("/api/ledger")
def ledger():
    return {"orders": db.orders(), "week": agent.week_summary()}


@app.get("/api/approvals")
def approvals():
    return {"orders": db.orders("status IN ('awaiting_owner','awaiting_paypal')")}


@app.post("/api/orders/{order_id}/approve")
def approve(order_id: int):
    try:
        return agent.approve_order(order_id)
    except KeyError:
        raise HTTPException(404, "Order not found")
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/api/orders/{order_id}/reject")
def reject(order_id: int):
    try:
        return agent.reject_order(order_id)
    except KeyError:
        raise HTTPException(404, "Order not found")
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.get("/api/events")
def events(limit: int = 50):
    return {"events": db.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (min(limit, 500),))}


@app.get("/api/insights")
def insights():
    return agent.insights()


# --- PayPal redirects ----------------------------------------------------------
@app.get("/api/paypal/return")
def paypal_return(token: str):
    """PayPal sends the owner back here with ?token=<order id> after approval."""
    o = agent.complete_paypal_return(token)
    return RedirectResponse(f"/?paid={o['id'] if o else ''}#ledger", status_code=302)


@app.get("/api/paypal/cancel")
def paypal_cancel(token: str = ""):
    return RedirectResponse("/#approvals", status_code=302)


@app.post("/api/paypal/link-wallet")
def link_wallet():
    """Optional one-time step: the owner approves a PayPal billing agreement so
    the agent can pay later without a redirect (Payment Method Tokens v3)."""
    try:
        st = agent.paypal().create_setup_token(
            return_url=f"{settings.public_base_url}/api/paypal/wallet-return",
            cancel_url=f"{settings.public_base_url}/#settings", request_id=f"spz-setup-{uuid.uuid4().hex[:12]}")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"PayPal could not start wallet linking: {e}") from e
    db.set_setting("pending_setup_token", st["id"])
    link = next((l["href"] for l in st.get("links", []) if l.get("rel") == "approve"), None)
    return {"setup_token": st["id"], "approve_url": link}


@app.get("/api/paypal/wallet-return")
def wallet_return(approval_token_id: str | None = None, approval_session_id: str | None = None, token: str | None = None):
    setup_id = approval_token_id or approval_session_id or token or db.get_setting("pending_setup_token")
    if not setup_id:
        raise HTTPException(400, "No setup token to confirm.")
    pt = agent.paypal().create_payment_token(setup_id, request_id=f"spz-pt-{setup_id}")
    db.set_setting("paypal_vault_id", pt["id"])
    db.log_event("owner", "Owner linked a PayPal wallet: the agent can now autopay within the guardrails.")
    return RedirectResponse("/?wallet=linked#settings", status_code=302)


@app.post("/api/paypal/unlink-wallet")
def unlink_wallet():
    db.set_setting("paypal_vault_id", "")
    db.log_event("owner", "Owner unlinked the PayPal wallet: payments now need confirmation in PayPal.")
    return {"wallet_linked": False}


@app.post("/api/demo/reset")
def demo_reset():
    if not (settings.paypal_mock or os.getenv("SPAZAPILOT_ALLOW_RESET") == "1"):
        raise HTTPException(403, "Reset is only available in mock mode or with SPAZAPILOT_ALLOW_RESET=1.")
    db.reset_all()
    return {"ok": True}
