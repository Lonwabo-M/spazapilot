"""Runtime configuration. Everything comes from environment variables so the
same code runs in MOCK mode (no keys) and REAL mode (keys present)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader (no extra dependency). Existing env vars win."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value[:1] in {'"', "'"}:
            value = value[1:value.find(value[0], 1)] if value.find(value[0], 1) > 0 else value[1:]
        elif " #" in value or value.startswith("#"):
            value = value.split(" #", 1)[0] if not value.startswith("#") else ""
        os.environ.setdefault(key.strip(), value.strip())


ROOT = Path(__file__).resolve().parent.parent
_load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # --- AI -----------------------------------------------------------------
    ai_provider: str = field(default_factory=lambda: os.getenv("AI_PROVIDER", "").strip().lower())
    ai_api_key: str = field(default_factory=lambda: os.getenv("AI_API_KEY", "").strip())
    ai_model: str = field(default_factory=lambda: os.getenv("AI_MODEL", "").strip())
    # Any OpenAI-compatible endpoint (OpenAI, OpenRouter, Groq, Together, local vLLM ...)
    ai_base_url: str = field(default_factory=lambda: os.getenv("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/"))

    # --- PayPal -------------------------------------------------------------
    paypal_client_id: str = field(default_factory=lambda: os.getenv("PAYPAL_CLIENT_ID", "").strip())
    paypal_client_secret: str = field(default_factory=lambda: os.getenv("PAYPAL_CLIENT_SECRET", "").strip())
    paypal_env: str = field(default_factory=lambda: os.getenv("PAYPAL_ENV", "sandbox").strip().lower())
    # A vault (payment token) id for the shop owner's PayPal wallet. When set,
    # the agent can pay without a redirect (merchant-initiated, pre-authorised).
    paypal_vault_id: str = field(default_factory=lambda: os.getenv("PAYPAL_VAULT_ID", "").strip())
    # Fallback payee for every supplier (a sandbox BUSINESS account email).
    paypal_default_payee: str = field(default_factory=lambda: os.getenv("PAYPAL_DEFAULT_PAYEE_EMAIL", "").strip())
    # Optional JSON map {"supplier_id": "sandbox-business@example.com"}
    supplier_payees: dict = field(default_factory=lambda: json.loads(os.getenv("SUPPLIER_PAYEE_EMAILS", "{}") or "{}"))

    # PayPal does not settle in ZAR, so orders are charged in USD and shown in both.
    zar_per_usd: float = field(default_factory=lambda: float(os.getenv("ZAR_PER_USD", "18.00")))
    paypal_currency: str = field(default_factory=lambda: os.getenv("PAYPAL_CURRENCY", "USD"))

    public_base_url: str = field(default_factory=lambda: os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/"))
    db_path: str = field(default_factory=lambda: os.getenv("SPAZAPILOT_DB", str(ROOT / "spazapilot.db")))
    force_mock: bool = field(default_factory=lambda: _bool("SPAZAPILOT_FORCE_MOCK"))

    @property
    def ai_mock(self) -> bool:
        return self.force_mock or not (self.ai_provider and self.ai_api_key)

    @property
    def paypal_mock(self) -> bool:
        return self.force_mock or not (self.paypal_client_id and self.paypal_client_secret)

    @property
    def paypal_base_url(self) -> str:
        return "https://api-m.paypal.com" if self.paypal_env == "live" else "https://api-m.sandbox.paypal.com"

    def default_model(self) -> str:
        if self.ai_model:
            return self.ai_model
        return "gemini-2.5-flash" if self.ai_provider == "gemini" else "gpt-4o-mini"


settings = Settings()


def reload_settings() -> Settings:
    global settings
    settings = Settings()
    return settings
