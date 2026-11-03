"""Basket optimiser: split a restock list across suppliers so the TOTAL landed
cost (goods + delivery fees) is as low as possible, while every supplier order
meets that supplier's minimum and urgent (out-of-stock) items arrive fast.

Small baskets (the normal case) are solved exactly by enumeration; large
ones fall back to a supplier-subset heuristic. Deterministic: same input,
same answer, which keeps the agent auditable."""
from __future__ import annotations

import itertools
from datetime import date, timedelta

from .catalogue import PRODUCTS, SUPPLIERS

EXACT_LIMIT = 200_000
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def next_delivery(supplier: dict, today: date) -> tuple[int, date]:
    """Earliest delivery if ordered today (same-day not offered)."""
    for d in range(1, 8):
        if (today.weekday() + d) % 7 in supplier["delivery_days"]:
            return d, today + timedelta(days=d)
    return 99, today + timedelta(days=99)


def _r(x: float) -> float:
    return round(x + 1e-9, 2)


def _cost(assign: dict[str, str], qty: dict[str, int], suppliers: dict) -> tuple[float, dict[str, float]]:
    subtotals: dict[str, float] = {}
    for pid, sid in assign.items():
        subtotals[sid] = subtotals.get(sid, 0.0) + suppliers[sid]["prices"][pid] * qty[pid]
    total = sum(subtotals.values()) + sum(suppliers[s]["delivery_fee"] for s in subtotals)
    return total, subtotals


def _valid(subtotals: dict[str, float], suppliers: dict) -> bool:
    return all(v + 1e-9 >= suppliers[s]["min_order"] for s, v in subtotals.items())


def optimise(items: list[dict], today: date | None = None, suppliers: dict | None = None,
             urgent_max_days: int = 1, usual: str | None = None) -> dict:
    suppliers = suppliers or SUPPLIERS
    today = today or date.today()
    qty = {i["product_id"]: int(i["qty"]) for i in items if int(i.get("qty", 0)) > 0}
    urgent = {i["product_id"] for i in items if i.get("urgent")}
    eta = {sid: next_delivery(s, today) for sid, s in suppliers.items()}

    options: dict[str, list[str]] = {}
    unavailable, late_urgent = [], []
    for pid in qty:
        stocking = [sid for sid, s in suppliers.items() if pid in s["prices"]]
        if not stocking:
            unavailable.append(pid)
            continue
        if pid in urgent:
            fast = [sid for sid in stocking if eta[sid][0] <= urgent_max_days]
            if fast:
                stocking = fast
            else:
                late_urgent.append(pid)
        options[pid] = sorted(stocking, key=lambda s: suppliers[s]["prices"][pid])

    pids = list(options)
    best = None  # (total, n_suppliers, assign, subtotals)
    space = 1
    for p in pids:
        space *= len(options[p])

    def consider(assign: dict[str, str]) -> None:
        nonlocal best
        total, subs = _cost(assign, qty, suppliers)
        if not _valid(subs, suppliers):
            return
        key = (_r(total), len(subs))
        if best is None or key < (best[0], best[1]):
            best = (_r(total), len(subs), dict(assign), subs)

    method = "exact"
    if pids and space <= EXACT_LIMIT:
        for combo in itertools.product(*(options[p] for p in pids)):
            consider(dict(zip(pids, combo)))
    elif pids:
        method = "heuristic"
        sids = list(suppliers)
        for r in range(1, len(sids) + 1):
            for subset in itertools.combinations(sids, r):
                assign = {}
                for p in pids:
                    allowed = [s for s in options[p] if s in subset]
                    if not allowed:
                        break
                    assign[p] = allowed[0]
                else:
                    consider(assign)

    feasible = best is not None or not pids
    if best is None and pids:
        # No assignment meets every minimum: return the cheapest-per-item split
        # and let the guardrails block it with a clear shortfall message.
        assign = {p: options[p][0] for p in pids}
        total, subs = _cost(assign, qty, suppliers)
        best = (_r(total), len(subs), assign, subs)

    orders = []
    if best:
        _, _, assign, subs = best
        for sid in sorted(subs, key=lambda s: -subs[s]):
            s = suppliers[sid]
            lines = []
            for pid, a in assign.items():
                if a != sid:
                    continue
                unit_price = s["prices"][pid]
                lines.append({
                    "product_id": pid, "name": PRODUCTS.get(pid, {}).get("name", pid), "unit": PRODUCTS.get(pid, {}).get("unit", "unit"),
                    "qty": qty[pid], "unit_price": unit_price, "line_total": _r(unit_price * qty[pid]),
                    "urgent": pid in urgent,
                })
            subtotal = _r(sum(l["line_total"] for l in lines))
            days, ddate = eta[sid]
            orders.append({
                "supplier_id": sid, "supplier_name": s["name"], "lines": lines,
                "subtotal": subtotal, "delivery_fee": s["delivery_fee"],
                "total": _r(subtotal + s["delivery_fee"]),
                "min_order": s["min_order"], "below_min_by": _r(max(0.0, s["min_order"] - subtotal)),
                "delivery_date": ddate.isoformat(), "delivery_label": f"{DAY_NAMES[ddate.weekday()]} {ddate.day} {ddate.strftime('%b')}",
                "delivery_in_days": days,
            })

    # What would it cost to buy everything from one supplier? (for the savings story)
    single = {}
    for sid, s in suppliers.items():
        if all(p in s["prices"] for p in pids) and pids:
            sub = sum(s["prices"][p] * qty[p] for p in pids)
            single[sid] = {"supplier_name": s["name"], "total": _r(sub + s["delivery_fee"]),
                           "meets_min": sub >= s["min_order"]}
    optimal_total = _r(sum(o["total"] for o in orders))
    benchmark, benchmark_label = None, None
    if single:
        sid_b = min(single, key=lambda k: single[k]["total"])
        benchmark, benchmark_label = single[sid_b]["total"], f"buying everything at {single[sid_b]['supplier_name']}"
    elif pids:
        # Nobody stocks everything: compare with the owner's habit of using the
        # local shop first and topping up the rest wherever it is cheapest.
        usual_id = usual or next(iter(suppliers))
        habit = {p: (usual_id if usual_id in options[p] else options[p][0]) for p in pids}
        benchmark = _r(_cost(habit, qty, suppliers)[0])
        benchmark_label = f"your usual run ({suppliers[usual_id]['name']} first, top-up elsewhere)"
    savings = _r(max(0.0, (benchmark or optimal_total) - optimal_total))

    return {
        "orders": orders,
        "feasible": feasible,
        "method": method,
        "unavailable": [{"product_id": p, "name": PRODUCTS.get(p, {}).get("name", p)} for p in unavailable],
        "late_urgent": late_urgent,
        "single_supplier_totals": single,
        "optimal_total": optimal_total,
        "benchmark_total": benchmark,
        "benchmark_label": benchmark_label,
        "savings": savings,
    }
