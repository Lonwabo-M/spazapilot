from datetime import date

from app.optimizer import next_delivery, optimise

TUESDAY = date(2026, 11, 3)

SUP = {
    "a": {"name": "A", "min_order": 100, "delivery_fee": 0, "delivery_days": [4], "prices": {"x": 10, "y": 50}},
    "b": {"name": "B", "min_order": 0, "delivery_fee": 20, "delivery_days": [2], "prices": {"x": 12, "y": 40, "z": 5}},
}


def items(**q):
    return [{"product_id": k, "qty": v} for k, v in q.items()]


def test_next_delivery_skips_today():
    d, when = next_delivery({"delivery_days": [1]}, TUESDAY)  # Tue -> next Tue
    assert d == 7 and when == date(2026, 11, 10)
    d, when = next_delivery({"delivery_days": [2]}, TUESDAY)
    assert d == 1


def test_picks_cheapest_total_including_fees():
    # x*10 from A = 100 (meets min, free delivery); y*1 from B = 40+20 = 60 vs A 50 -> A cheaper overall
    plan = optimise(items(x=10, y=1), today=TUESDAY, suppliers=SUP)
    assert plan["feasible"]
    assert [o["supplier_id"] for o in plan["orders"]] == ["a"]
    assert plan["optimal_total"] == 150


def test_respects_supplier_minimum():
    # 2 x at A = 20 < min 100, so x must come from B
    plan = optimise(items(x=2), today=TUESDAY, suppliers=SUP)
    assert [o["supplier_id"] for o in plan["orders"]] == ["b"]
    assert plan["optimal_total"] == 44


def test_unavailable_items_reported():
    plan = optimise(items(x=10, q=3), today=TUESDAY, suppliers=SUP)
    assert [u["product_id"] for u in plan["unavailable"]] == ["q"]


def test_urgent_items_go_to_fast_supplier():
    # A delivers Fri (3 days away), B delivers Wed (1 day). Urgent x must come from B despite A being cheaper.
    plan = optimise([{"product_id": "x", "qty": 10, "urgent": True}], today=TUESDAY, suppliers=SUP)
    assert plan["orders"][0]["supplier_id"] == "b"
    assert plan["orders"][0]["delivery_in_days"] == 1


def test_infeasible_minimum_is_flagged_not_hidden():
    sup = {"a": {**SUP["a"], "min_order": 1000}}
    plan = optimise(items(x=1), today=TUESDAY, suppliers=sup)
    assert plan["feasible"] is False
    assert plan["orders"][0]["below_min_by"] == 990


def test_savings_vs_single_supplier():
    plan = optimise(items(x=10, y=5), today=TUESDAY, suppliers=SUP)
    # A alone: 100+250=350 ; B alone: 120+200+20=340 ; split x->A (100) y->B (200+20) = 320
    assert plan["optimal_total"] == 320
    assert plan["benchmark_total"] == 340
    assert plan["savings"] == 20


def test_seeded_catalogue_demo_note_is_feasible():
    plan = optimise(items(sugar=2, maas=6, bread=3, airtime=50), today=TUESDAY)
    assert plan["feasible"] and plan["orders"]
    for o in plan["orders"]:
        assert o["below_min_by"] == 0
