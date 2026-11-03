from app import parser
from app.parser import parse_note_deterministic as parse, validate_ai_items


def by_id(res):
    return {i["product_id"]: i for i in res["items"]}


def test_brief_example_mixed_english_isixhosa():
    res = by_id(parse("sugar 2, maas iphelile, need 3 bread, airtime R50"))
    assert res["sugar"]["qty"] == 2 and not res["sugar"]["urgent"]
    assert res["maas"]["urgent"] is True and res["maas"]["qty"] == parser.DEFAULT_QTY["maas"]
    assert res["bread"]["qty"] == 3
    assert res["airtime"]["qty"] == 50  # rand face value


def test_isixhosa_words_and_number_words():
    res = by_id(parse("iswekile ezimbini\nisonka 10 na amaqanda 2"))
    assert res["sugar"]["qty"] == 2
    assert res["bread"]["qty"] == 10
    assert res["eggs"]["qty"] == 2


def test_pack_sizes_are_not_quantities():
    res = by_id(parse("mealie meal 10kg 3 bags, oil 2L x4"))
    assert res["maize_meal"]["qty"] == 3
    assert res["cooking_oil"]["qty"] == 4


def test_longest_phrase_wins_and_unknowns_reported():
    out = parse("cooking oil 2, pampers 3")
    assert [i["product_id"] for i in out["items"]] == ["cooking_oil"]
    assert out["unrecognised"] == ["pampers 3"]


def test_duplicates_merge_and_urgency_sticks():
    res = by_id(parse("bread 2, isonka iphelile"))
    assert res["bread"]["qty"] == 2 + parser.DEFAULT_QTY["bread"]
    assert res["bread"]["urgent"] is True


def test_empty_and_noise():
    assert parse("")["items"] == []
    assert parse("!!! 123")["items"] == []


def test_ai_output_is_validated_against_catalogue():
    raw = {"items": [{"product_id": "sugar", "qty": "3", "urgent": False, "source_text": "sugar"},
                     {"product_id": "cigarettes", "qty": 10, "source_text": "smokes"},
                     {"product_id": "bread", "qty": -2, "source_text": "bread"},
                     {"product_id": "milk", "qty": 99999, "source_text": "milk"}],
           "unrecognised": ["nappies"]}
    out = validate_ai_items(raw)
    ids = {i["product_id"]: i["qty"] for i in out["items"]}
    assert ids == {"sugar": 3, "milk": 1000}
    assert "smokes" in out["unrecognised"] and "nappies" in out["unrecognised"]


def test_ai_failure_falls_back_to_offline_parser(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "force_mock", False)
    monkeypatch.setattr(settings, "ai_provider", "openai")
    monkeypatch.setattr(settings, "ai_api_key", "x")

    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(parser.ai, "chat_json", boom)
    out = parser.parse_note("sugar 2")
    assert out["engine"].startswith("deterministic")
    assert out["items"][0]["product_id"] == "sugar"
    assert "warning" in out


def test_ai_path_used_when_keys_present(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "force_mock", False)
    monkeypatch.setattr(settings, "ai_provider", "gemini")
    monkeypatch.setattr(settings, "ai_api_key", "x")
    monkeypatch.setattr(parser.ai, "chat_json", lambda *a, **k: {"items": [{"product_id": "rice", "qty": 4, "urgent": True, "source_text": "irayisi iphelile"}], "unrecognised": []})
    out = parser.parse_note("irayisi iphelile")
    assert out["engine"] == "ai:gemini"
    assert out["items"][0]["product_id"] == "rice" and out["items"][0]["urgent"]
