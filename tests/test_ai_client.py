import json

import httpx

from app import ai
from app.config import settings


def test_openai_compatible_request_shape(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "openai")
    monkeypatch.setattr(settings, "ai_api_key", "sk-test")
    monkeypatch.setattr(settings, "ai_model", "gpt-4o-mini")
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"items": [], "unrecognised": []}'}}]})

    out = ai.chat_json("sys", "hi", image_b64="AAAA", transport=httpx.MockTransport(handler))
    assert out == {"items": [], "unrecognised": []}
    assert seen["url"].endswith("/chat/completions") and seen["auth"] == "Bearer sk-test"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_gemini_request_shape_and_fenced_json(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "gemini")
    monkeypatch.setattr(settings, "ai_api_key", "g-test")
    monkeypatch.setattr(settings, "ai_model", "")
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["key"] = req.headers["x-goog-api-key"]
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "```json\n{\"receipt\": \"ok\"}\n```"}]}}]})

    out = ai.chat_json("sys", "hi", transport=httpx.MockTransport(handler))
    assert out == {"receipt": "ok"}
    assert "gemini-2.5-flash:generateContent" in seen["url"] and seen["key"] == "g-test"


def test_provider_error_raises(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "openai")
    monkeypatch.setattr(settings, "ai_api_key", "sk-test")
    t = httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "bad key"}))
    try:
        ai.chat_json("s", "u", transport=t)
    except ai.AIError as e:
        assert "401" in str(e)
    else:
        raise AssertionError("expected AIError")
