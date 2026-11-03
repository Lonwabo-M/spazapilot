"""Plain-language receipts and weekly insights. AI writes them in real mode;
templates write them in mock mode or if the AI call fails. Numbers always come
from the ledger, never from the model."""
from __future__ import annotations

import json

from . import ai
from .config import settings


def _fmt(v: float) -> str:
    return f"R{v:,.2f}".replace(",", "\u00a0")


def _lines_text(lines: list[dict]) -> str:
    parts = []
    for l in lines:
        if l["product_id"] == "airtime":
            parts.append(f"R{l['qty']} airtime")
        else:
            parts.append(f"{l['qty']} x {l['name']}")
    return ", ".join(parts)


def receipt_template(order: dict, how: str) -> str:
    return (f"Paid {_fmt(order['total_zar'])} to {order['supplier_name']} for {_lines_text(order['lines'])}. "
            f"Delivery {order.get('delivery_label') or order.get('delivery_date')}"
            f"{' (incl. ' + _fmt(order['delivery_fee']) + ' delivery)' if order['delivery_fee'] else ' (free delivery)'}. "
            f"{how} PayPal ref {order.get('paypal_capture_id') or order.get('paypal_order_id')} "
            f"({order['currency']} {order['total_usd']:.2f}).")


RECEIPT_SYSTEM = """You write SpazaPilot receipts for a busy South African spaza shop owner.
Write 2 short sentences in plain, friendly English a person reading on a cheap phone understands:
what was bought, from whom, total in rand, when it arrives, and why it was paid (autopay or owner approved).
Then add one short isiXhosa line only if you are confident it is correct; otherwise leave it out.
Use ONLY the numbers in the JSON. Return JSON: {"receipt": "..."}"""


def write_receipt(order: dict, how: str) -> str:
    if settings.ai_mock:
        return receipt_template(order, how)
    try:
        facts = {k: order.get(k) for k in ("supplier_name", "total_zar", "delivery_fee", "delivery_label",
                                           "paypal_capture_id", "currency", "total_usd")}
        facts["items"] = _lines_text(order["lines"])
        facts["why_paid"] = how
        out = ai.chat_json(RECEIPT_SYSTEM, json.dumps(facts))
        text = str(out.get("receipt", "")).strip()
        return text or receipt_template(order, how)
    except Exception:  # noqa: BLE001
        return receipt_template(order, how)


def weekly_stats(paid: list[dict], budget: float, savings: float) -> dict:
    spent = round(sum(o["total_zar"] for o in paid), 2)
    by_supplier: dict[str, float] = {}
    by_item: dict[str, float] = {}
    fees = 0.0
    for o in paid:
        by_supplier[o["supplier_name"]] = by_supplier.get(o["supplier_name"], 0) + o["total_zar"]
        fees += o["delivery_fee"]
        for l in o["lines"]:
            by_item[l["name"]] = by_item.get(l["name"], 0) + l["line_total"]
    top_item = max(by_item.items(), key=lambda kv: kv[1]) if by_item else None
    top_sup = max(by_supplier.items(), key=lambda kv: kv[1]) if by_supplier else None
    return {"spent": spent, "budget": budget, "left": round(budget - spent, 2), "orders": len(paid),
            "delivery_fees": round(fees, 2), "savings_vs_single_supplier": round(savings, 2),
            "top_item": top_item, "top_supplier": top_sup,
            "by_supplier": {k: round(v, 2) for k, v in by_supplier.items()}}


def insight_template(s: dict) -> list[str]:
    if not s["orders"]:
        return ["No restock payments yet this week. Paste a list and SpazaPilot will find the cheapest suppliers."]
    pct = 100 * s["spent"] / s["budget"] if s["budget"] else 0
    out = [f"You've spent {_fmt(s['spent'])} of your {_fmt(s['budget'])} budget ({pct:.0f}%) across {s['orders']} supplier orders."]
    if s["savings_vs_single_supplier"] > 0:
        out.append(f"Smart supplier splits saved you {_fmt(s['savings_vs_single_supplier'])} compared with your usual way of buying.")
    if s["top_item"]:
        out.append(f"Your biggest cost was {s['top_item'][0]} ({_fmt(s['top_item'][1])}). Check its sell price still covers this.")
    if s["delivery_fees"] > 0:
        out.append(f"Delivery fees came to {_fmt(s['delivery_fees'])}. Combining lists before a delivery day cuts these.")
    return out


INSIGHT_SYSTEM = """You are SpazaPilot's weekly money coach for a South African spaza shop owner.
Given this week's ledger stats (rand), write 3 short, practical insights (max 25 words each):
budget position, where savings came from, one concrete action for next week. Plain English, no jargon.
Only use numbers present in the JSON. Return JSON: {"insights": ["...", "...", "..."]}"""


def weekly_insight(stats: dict) -> tuple[list[str], str]:
    if settings.ai_mock or not stats["orders"]:
        return insight_template(stats), "template"
    try:
        out = ai.chat_json(INSIGHT_SYSTEM, json.dumps(stats))
        ins = [str(x) for x in out.get("insights", []) if str(x).strip()][:4]
        return (ins or insight_template(stats)), f"ai:{settings.ai_provider}"
    except Exception:  # noqa: BLE001
        return insight_template(stats), "template (AI fallback)"
