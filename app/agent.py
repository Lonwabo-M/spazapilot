"""The SpazaPilot agent loop:

  restock list -> optimise basket -> guardrails per supplier order ->
  pay (PayPal Orders v2) or hold for owner -> receipt -> ledger

Each step writes to the event log so the owner (and judges) can see exactly
what the agent did and why."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db, narrate
from .catalogue import SUPPLIERS
from .config import settings
from .guardrails import AUTO_PAY, BLOCKED, NEEDS_APPROVAL, Policy, evaluate, order_signature
from .optimizer import optimise
from .paypal_client import MockPayPalClient, PayPalClient, PayPalError, approve_link, build_order_payload, capture_info

SAST = ZoneInfo("Africa/Johannesburg")
_client = None

# order statuses
PAID, AWAITING_OWNER, AWAITING_PAYPAL, BLOCKED_S, REJECTED, FAILED = (
    "paid", "awaiting_owner", "awaiting_paypal", "blocked", "rejected", "failed")

def _z(v: float) -> str:
    return f"R{v:,.2f}".replace(",", "\u00a0")


def _z0(v: float) -> str:
    return f"R{v:,.0f}".replace(",", "\u00a0")



def paypal():
    global _client
    if _client is None:
        if settings.paypal_mock:
            _client = MockPayPalClient()
        else:
            _client = PayPalClient(settings.paypal_client_id, settings.paypal_client_secret, settings.paypal_base_url)
    return _client


def set_paypal_client(client) -> None:  # used by tests
    global _client
    _client = client


def today_sast() -> date:
    return datetime.now(SAST).date()


def week_start_utc() -> str:
    now = datetime.now(SAST)
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday.astimezone(timezone.utc).isoformat(timespec="seconds")


def committed_this_week() -> float:
    """Money paid or already sent to PayPal for approval since Monday 00:00 SAST."""
    rows = db.query("SELECT COALESCE(SUM(total_zar),0) AS s FROM orders WHERE status IN (?,?) AND created_at >= ?",
                    (PAID, AWAITING_PAYPAL, week_start_utc()))
    return float(rows[0]["s"])


def recent_signatures() -> set[str]:
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    return {r["signature"] for r in db.query("SELECT signature FROM orders WHERE status=? AND paid_at >= ?", (PAID, since))}


def payee_for(supplier_id: str) -> str:
    if supplier_id in settings.supplier_payees:
        return settings.supplier_payees[supplier_id]
    if not settings.paypal_mock and settings.paypal_default_payee:
        return settings.paypal_default_payee
    return SUPPLIERS[supplier_id]["payee_email"]


def vault_id() -> str | None:
    return db.get_setting("paypal_vault_id") or settings.paypal_vault_id or None


def to_usd(zar: float) -> float:
    return round(zar / settings.zar_per_usd + 1e-9, 2)


# ---------------------------------------------------------------------------
def run_agent(items: list[dict], note: str = "", source: str = "text", engine: str = "") -> dict:
    policy_d = db.get_policy()
    policy = Policy.from_dict(policy_d)
    now = db.now_iso()
    run_id = db.execute("INSERT INTO runs(created_at, source, note, items_json, engine) VALUES(?,?,?,?,?)",
                        (now, source, note, json.dumps(items), engine)).lastrowid
    db.log_event("parse", f"Understood {len(items)} item(s) from the owner's {source}.", run_id,
                 data={"items": [f"{i['qty']} {i['product_id']}" + (" (urgent)" if i.get('urgent') else '') for i in items], "engine": engine})

    plan = optimise(items, today=today_sast(), urgent_max_days=int(policy_d.get("urgent_max_days", 1)))
    db.execute("UPDATE runs SET plan_json=?, savings=? WHERE id=?", (json.dumps(plan), plan["savings"], run_id))
    db.log_event("plan", f"Compared {len(SUPPLIERS)} suppliers ({plan['method']} search): {len(plan['orders'])} order(s), "
                 f"{_z(plan['optimal_total'])} total, saving {_z(plan['savings'])} vs {plan['benchmark_label'] or 'one-stop buying'}.", run_id,
                 data={"orders": [{o['supplier_name']: o['total']} for o in plan['orders']]})
    for u in plan["unavailable"]:
        db.log_event("plan", f"No supplier stocks {u['name']}; left off the order.", run_id)

    spent = committed_this_week()
    sigs = recent_signatures()
    # urgent orders first, then smallest first, so autopay covers the most stock
    ordered = sorted(plan["orders"], key=lambda o: (not any(l["urgent"] for l in o["lines"]), o["total"]))
    created = []
    for o in ordered:
        supplier = SUPPLIERS[o["supplier_id"]]
        d = evaluate(o, policy, spent, supplier, sigs)
        status = {AUTO_PAY: "pending", NEEDS_APPROVAL: AWAITING_OWNER, BLOCKED: BLOCKED_S}[d.decision]
        oid = db.execute(
            """INSERT INTO orders(run_id, supplier_id, supplier_name, lines_json, subtotal, delivery_fee, total_zar,
               total_usd, currency, delivery_date, delivery_label, decision, reasons_json, status, payee_email,
               signature, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, o["supplier_id"], o["supplier_name"], json.dumps(o["lines"]), o["subtotal"], o["delivery_fee"],
             o["total"], to_usd(o["total"]), settings.paypal_currency, o["delivery_date"], o["delivery_label"],
             d.decision, json.dumps(d.reasons), status, payee_for(o["supplier_id"]), order_signature(o), now, now)).lastrowid
        db.log_event("guardrail", f"{o['supplier_name']} {_z(o['total'])}: {d.decision.replace('_', ' ').lower()}. "
                     + " ".join(d.reasons), run_id, oid)
        if d.decision == AUTO_PAY:
            res = pay_order(oid, how=f"Autopaid: under your {_z0(policy.auto_approve_limit)} limit.")
            if res["status"] in (PAID, AWAITING_PAYPAL):
                spent += o["total"]
        created.append(oid)

    return get_run(run_id)


def pay_order(order_id: int, how: str) -> dict:
    o = db.order_row(order_id)
    pp = paypal()
    lines = [{"name": (f"R{l['qty']} airtime voucher" if l["product_id"] == "airtime" else f"{l['qty']} x {l['name']}"),
              "sku": l["product_id"], "amount": to_usd(l["line_total"])} for l in o["lines"]]
    shipping = to_usd(o["delivery_fee"])
    usd_total = round(sum(l["amount"] for l in lines) + shipping, 2)
    vid = vault_id()
    payload = build_order_payload(
        reference_id=f"SPZ-{order_id}", payee_email=o["payee_email"], currency=settings.paypal_currency,
        lines=lines, shipping=shipping, description=f"Restock from {o['supplier_name']} via SpazaPilot",
        invoice_id=f"SPZ-{order_id}-{o['created_at'][:19].replace(':', '').replace('-', '')}", custom_id=str(order_id),
        vault_id=vid if not settings.paypal_mock else None,
        return_url=f"{settings.public_base_url}/api/paypal/return", cancel_url=f"{settings.public_base_url}/api/paypal/cancel")
    db.execute("UPDATE orders SET total_usd=?, updated_at=? WHERE id=?", (usd_total, db.now_iso(), order_id))
    try:
        created = pp.create_order(payload, request_id=f"spz-{order_id}-create")
        db.execute("UPDATE orders SET paypal_order_id=? WHERE id=?", (created["id"], order_id))
        db.log_event("paypal", f"Created PayPal order {created['id']} ({created['status']}) for "
                     f"{settings.paypal_currency} {usd_total:.2f}, payee {o['payee_email']}.", o["run_id"], order_id)
        if created["status"] == "COMPLETED" and capture_info(created):
            return _mark_paid(order_id, created, how)
        if created["status"] == "APPROVED":
            return _capture(order_id, created["id"], how)
        link = approve_link(created)
        db.execute("UPDATE orders SET status=?, approve_url=?, updated_at=? WHERE id=?",
                   (AWAITING_PAYPAL, link, db.now_iso(), order_id))
        db.log_event("paypal", "Waiting for the owner to confirm in PayPal (no linked wallet yet).", o["run_id"], order_id)
        return db.order_row(order_id)
    except PayPalError as e:
        return _mark_failed(order_id, e)


def _capture(order_id: int, paypal_order_id: str, how: str) -> dict:
    try:
        cap = paypal().capture_order(paypal_order_id, request_id=f"spz-{order_id}-capture")
    except PayPalError as e:
        return _mark_failed(order_id, e)
    if cap.get("status") != "COMPLETED":
        return _mark_failed(order_id, PayPalError(200, "NOT_COMPLETED", f"Capture status {cap.get('status')}"))
    return _mark_paid(order_id, cap, how)


def _mark_paid(order_id: int, pp_order: dict, how: str) -> dict:
    info = capture_info(pp_order) or {}
    db.execute("UPDATE orders SET status=?, paypal_capture_id=?, paid_at=?, updated_at=?, error=NULL WHERE id=?",
               (PAID, info.get("id"), db.now_iso(), db.now_iso(), order_id))
    o = db.order_row(order_id)
    db.log_event("paypal", f"Captured {info.get('currency')} {info.get('amount')} (capture {info.get('id')}). Paid.",
                 o["run_id"], order_id)
    receipt = narrate.write_receipt(o, how)
    db.execute("UPDATE orders SET receipt=? WHERE id=?", (receipt, order_id))
    db.log_event("receipt", receipt, o["run_id"], order_id)
    return db.order_row(order_id)


def _mark_failed(order_id: int, e: PayPalError) -> dict:
    msg = f"{e.name}: {e.message}" + (f" (debug_id {e.debug_id})" if e.debug_id else "")
    db.execute("UPDATE orders SET status=?, error=?, updated_at=? WHERE id=?", (FAILED, msg, db.now_iso(), order_id))
    o = db.order_row(order_id)
    db.log_event("paypal", f"Payment failed, nothing was charged: {msg}", o["run_id"], order_id)
    return o


def approve_order(order_id: int) -> dict:
    o = db.order_row(order_id)
    if not o:
        raise KeyError(order_id)
    if o["status"] not in (AWAITING_OWNER, FAILED):
        raise ValueError(f"Order is {o['status']}, not waiting for approval.")
    # Owner approval releases soft holds, but hard limits are re-checked now.
    policy = Policy.from_dict(db.get_policy())
    order = {"supplier_id": o["supplier_id"], "total": o["total_zar"], "lines": o["lines"],
             "below_min_by": 0}
    d = evaluate(order, policy, committed_this_week(), SUPPLIERS[o["supplier_id"]])
    if d.decision == BLOCKED:
        db.execute("UPDATE orders SET status=?, decision=?, reasons_json=?, updated_at=? WHERE id=?",
                   (BLOCKED_S, BLOCKED, json.dumps(d.reasons), db.now_iso(), order_id))
        db.log_event("guardrail", "Owner approved, but a hard limit still applies: " + " ".join(d.reasons), o["run_id"], order_id)
        return db.order_row(order_id)
    db.log_event("owner", f"Owner approved {_z(o['total_zar'])} to {o['supplier_name']}.", o["run_id"], order_id)
    return pay_order(order_id, how="Paid after you approved it.")


def reject_order(order_id: int) -> dict:
    o = db.order_row(order_id)
    if not o:
        raise KeyError(order_id)
    if o["status"] not in (AWAITING_OWNER, AWAITING_PAYPAL, BLOCKED_S, FAILED):
        raise ValueError(f"Order is {o['status']} and can't be rejected.")
    db.execute("UPDATE orders SET status=?, updated_at=? WHERE id=?", (REJECTED, db.now_iso(), order_id))
    db.log_event("owner", f"Owner rejected the {o['supplier_name']} order.", o["run_id"], order_id)
    return db.order_row(order_id)


def complete_paypal_return(paypal_order_id: str) -> dict | None:
    rows = db.query("SELECT id FROM orders WHERE paypal_order_id=?", (paypal_order_id,))
    if not rows:
        return None
    oid = rows[0]["id"]
    o = db.order_row(oid)
    if o["status"] == PAID:
        return o
    return _capture(oid, paypal_order_id, "Paid after you confirmed it in PayPal.")


def get_run(run_id: int) -> dict:
    run = db.query("SELECT * FROM runs WHERE id=?", (run_id,))[0]
    plan = json.loads(run["plan_json"] or "{}")
    return {
        "run_id": run_id, "created_at": run["created_at"], "engine": run["engine"],
        "orders": list(reversed(db.orders("run_id=?", (run_id,)))),
        "events": db.query("SELECT * FROM events WHERE run_id=? ORDER BY id", (run_id,)),
        "savings": run["savings"], "optimal_total": plan.get("optimal_total"),
        "benchmark_total": plan.get("benchmark_total"), "benchmark_label": plan.get("benchmark_label"), "unavailable": plan.get("unavailable", []),
        "single_supplier_totals": plan.get("single_supplier_totals", {}),
        "week": week_summary(),
    }


def week_summary() -> dict:
    pol = db.get_policy()
    spent = committed_this_week()
    return {"budget": pol["weekly_budget"], "committed": round(spent, 2),
            "left": round(pol["weekly_budget"] - spent, 2), "auto_approve_limit": pol["auto_approve_limit"]}


def insights() -> dict:
    pol = db.get_policy()
    since = week_start_utc()
    paid = db.orders("status=? AND created_at >= ?", (PAID, since))
    sav = db.query("SELECT COALESCE(SUM(savings),0) s FROM runs WHERE created_at >= ? AND id IN "
                   "(SELECT run_id FROM orders WHERE status=?)", (since, PAID))[0]["s"]
    stats = narrate.weekly_stats(paid, pol["weekly_budget"], float(sav))
    text, engine = narrate.weekly_insight(stats)
    return {"stats": stats, "insights": text, "engine": engine}
