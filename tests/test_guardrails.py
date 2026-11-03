from app.guardrails import AUTO_PAY, BLOCKED, NEEDS_APPROVAL, Policy, evaluate, order_signature

SUP = {"name": "Kasi Direct", "verified": True, "min_order": 120}


def order(total, lines=None, sid="kasi_direct", below=0):
    return {"supplier_id": sid, "total": total, "below_min_by": below,
            "lines": lines or [{"product_id": "sugar", "name": "Sugar", "qty": 2}]}


def test_small_order_autopays():
    d = evaluate(order(150), Policy(auto_approve_limit=400, weekly_budget=2500), 0, SUP)
    assert d.decision == AUTO_PAY


def test_over_limit_needs_owner():
    d = evaluate(order(450), Policy(auto_approve_limit=400), 0, SUP)
    assert d.decision == NEEDS_APPROVAL and "autopay limit" in d.reasons[0]


def test_budget_is_a_hard_block_including_already_spent():
    d = evaluate(order(300), Policy(weekly_budget=1000, auto_approve_limit=400), 800, SUP)
    assert d.decision == BLOCKED and "weekly budget" in d.reasons[0]


def test_unverified_or_not_allowlisted_supplier_blocked():
    assert evaluate(order(50), Policy(), 0, {**SUP, "verified": False}).decision == BLOCKED
    assert evaluate(order(50), Policy(allowed_suppliers=["boland_wholesale"]), 0, SUP).decision == BLOCKED


def test_below_minimum_blocked():
    assert evaluate(order(50, below=70), Policy(), 0, SUP).decision == BLOCKED


def test_autopay_switched_off_holds_everything():
    d = evaluate(order(10), Policy(agent_enabled=False), 0, SUP)
    assert d.decision == NEEDS_APPROVAL


def test_unusual_quantities_held():
    big = [{"product_id": "bread", "name": "Bread", "qty": 500}]
    assert evaluate(order(100, big), Policy(max_qty_per_line=60), 0, SUP).decision == NEEDS_APPROVAL
    air = [{"product_id": "airtime", "name": "Airtime", "qty": 2000}]
    assert evaluate(order(100, air), Policy(auto_approve_limit=5000, max_airtime_per_order=500), 0, SUP).decision == NEEDS_APPROVAL


def test_duplicate_of_recent_paid_order_held():
    o = order(100)
    d = evaluate(o, Policy(), 0, SUP, recent_signatures={order_signature(o)})
    assert d.decision == NEEDS_APPROVAL and "repeat" in d.reasons[0]
