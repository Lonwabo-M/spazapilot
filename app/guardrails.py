"""Spending guardrails. These are plain code, evaluated BEFORE any payment call.
The language model never decides whether money moves; it only explains."""
from __future__ import annotations

from dataclasses import dataclass, field


def _z(v: float) -> str:
    return f"R{v:,.2f}".replace(",", "\u00a0")


def _z0(v: float) -> str:
    return f"R{v:,.0f}".replace(",", "\u00a0")


AUTO_PAY = "AUTO_PAY"
NEEDS_APPROVAL = "NEEDS_APPROVAL"
BLOCKED = "BLOCKED"


@dataclass
class Policy:
    weekly_budget: float = 2500.0
    auto_approve_limit: float = 500.0
    agent_enabled: bool = True
    max_qty_per_line: int = 60
    max_airtime_per_order: float = 500.0
    allowed_suppliers: list[str] | None = None  # None = all verified suppliers

    @classmethod
    def from_dict(cls, d: dict) -> "Policy":
        return cls(
            weekly_budget=float(d.get("weekly_budget", 2500)),
            auto_approve_limit=float(d.get("auto_approve_limit", 500)),
            agent_enabled=bool(d.get("agent_enabled", True)),
            max_qty_per_line=int(d.get("max_qty_per_line", 60)),
            max_airtime_per_order=float(d.get("max_airtime_per_order", 500)),
            allowed_suppliers=d.get("allowed_suppliers"),
        )


@dataclass
class Decision:
    decision: str
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"decision": self.decision, "reasons": self.reasons}


def evaluate(order: dict, policy: Policy, spent_this_week: float, supplier: dict,
             recent_signatures: set[str] | None = None) -> Decision:
    """Decide AUTO_PAY / NEEDS_APPROVAL / BLOCKED for one supplier order.

    spent_this_week must include money already paid AND money already committed
    by earlier orders in the same run, so two small orders can't sneak past the budget.
    """
    total = float(order["total"])
    sid = order["supplier_id"]

    # Hard blocks: the owner must change something before this can ever pay.
    if not supplier.get("verified"):
        return Decision(BLOCKED, [f"{supplier.get('name', sid)} is not a verified supplier."])
    if policy.allowed_suppliers is not None and sid not in policy.allowed_suppliers:
        return Decision(BLOCKED, [f"{supplier['name']} is not on your approved supplier list."])
    if order.get("below_min_by", 0) > 0:
        return Decision(BLOCKED, [f"Below {supplier['name']}'s {_z0(supplier['min_order'])} minimum by {_z(order['below_min_by'])}. Add items or wait for the next list."])
    remaining = policy.weekly_budget - spent_this_week
    if total > remaining + 1e-9:
        return Decision(BLOCKED, [f"Would exceed your weekly budget: {_z(total)} needed, {_z(max(remaining, 0))} left of {_z0(policy.weekly_budget)}."])

    # Soft holds: one tap from the owner releases them.
    reasons: list[str] = []
    if not policy.agent_enabled:
        reasons.append("Autopay is switched off, so every order waits for you.")
    if total > policy.auto_approve_limit + 1e-9:
        reasons.append(f"{_z(total)} is above your {_z0(policy.auto_approve_limit)} autopay limit.")
    for line in order["lines"]:
        if line["product_id"] == "airtime":
            if line["qty"] > policy.max_airtime_per_order:
                reasons.append(f"Airtime R{line['qty']} is more than the usual {_z0(policy.max_airtime_per_order)}.")
        elif line["qty"] > policy.max_qty_per_line:
            reasons.append(f"Unusual quantity: {line['qty']} x {line['name']}.")
    if recent_signatures and order_signature(order) in recent_signatures:
        reasons.append("Looks like a repeat of an order paid in the last 24 hours.")

    if reasons:
        return Decision(NEEDS_APPROVAL, reasons)
    return Decision(AUTO_PAY, [f"Within your {_z0(policy.auto_approve_limit)} autopay limit and weekly budget ({_z(remaining - total)} left after this)."])


def order_signature(order: dict) -> str:
    lines = sorted((l["product_id"], int(l["qty"])) for l in order["lines"])
    return f"{order['supplier_id']}|" + ",".join(f"{p}:{q}" for p, q in lines)
