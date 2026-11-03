"""Thin AI client. Supports any OpenAI-compatible chat endpoint and Google
Gemini. Every call asks for JSON and is validated by the caller, so the model
never gets to decide anything that touches money: it only reads and writes text."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import settings


class AIError(RuntimeError):
    pass


def _extract_json(text: str) -> Any:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise AIError(f"Model did not return JSON: {text[:200]}")


def chat_json(system: str, user: str, image_b64: str | None = None, image_mime: str = "image/jpeg",
              transport: httpx.BaseTransport | None = None, timeout: float = 45.0) -> Any:
    """Send one prompt (optionally with an image) and return parsed JSON."""
    provider = settings.ai_provider
    model = settings.default_model()
    client = httpx.Client(timeout=timeout, transport=transport)
    try:
        if provider == "gemini":
            parts: list[dict] = [{"text": user}]
            if image_b64:
                parts.append({"inline_data": {"mime_type": image_mime, "data": image_b64}})
            r = client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": settings.ai_api_key},
                json={
                    "systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": parts}],
                    "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
                },
            )
            if r.status_code >= 400:
                raise AIError(f"Gemini error {r.status_code}: {r.text[:300]}")
            data = r.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        else:  # openai-compatible
            content: Any = user
            if image_b64:
                content = [
                    {"type": "text", "text": user},
                    {"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{image_b64}"}},
                ]
            r = client.post(
                f"{settings.ai_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {settings.ai_api_key}"},
                json={
                    "model": model,
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
                },
            )
            if r.status_code >= 400:
                raise AIError(f"AI provider error {r.status_code}: {r.text[:300]}")
            text = r.json()["choices"][0]["message"]["content"]
        return _extract_json(text)
    except (httpx.HTTPError, KeyError, IndexError) as exc:
        raise AIError(str(exc)) from exc
    finally:
        client.close()
