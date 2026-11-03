"""Turn a messy restock note (English / isiXhosa / mixed) or a shelf photo into a
structured restock list.

* MOCK mode: a deterministic lexicon parser (works offline, used in tests).
* REAL mode: the LLM parses, then the output is validated against the catalogue.
  If the model fails or returns junk, we fall back to the deterministic parser.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from . import ai
from .catalogue import PRODUCTS
from .config import settings

# Longest phrases first so "cooking oil" beats "oil".
LEXICON: dict[str, list[str]] = {
    "sugar": ["sugar", "iswekile", "swekile", "tsoekere"],
    "maize_meal": ["mealie meal", "mielie meal", "maize meal", "mealiemeal", "mieliemeal", "impuphu", "umgubo", "pap"],
    "bread": ["bread", "isonka", "sonka", "loaves", "loaf"],
    "maas": ["amasi", "maas", "amas"],
    "milk": ["milk", "ubisi", "lebese"],
    "eggs": ["amaqanda", "amaqhanda", "iqanda", "eggs", "egg"],
    "cooking_oil": ["cooking oil", "sunflower oil", "ioyile", "i-oyile", "oyile", "oil"],
    "rice": ["irayisi", "rayisi", "rice"],
    "paraffin": ["iparafini", "parafini", "paraffin"],
    "airtime": ["airtime", "air time", "i-airtime"],
    "candles": ["amakhandlela", "ikhandlela", "candles", "candle"],
    "soap": ["isepha", "sepha", "soap"],
}

# Default restock quantity when the note only says "finished" / "iphelile".
DEFAULT_QTY = {
    "sugar": 5, "maize_meal": 3, "bread": 10, "maas": 6, "milk": 12, "eggs": 2,
    "cooking_oil": 4, "rice": 4, "paraffin": 6, "airtime": 100, "candles": 5, "soap": 4,
}

OUT_OF_STOCK = [
    "iphelile", "iphelele", "ziphelile", "uphelile", "siphelile", "kuphelile", "akukho", "ayikho",
    "out of stock", "sold out", "finished", "run out", "ran out", "none left", "no more", "empty", "out",
]

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "dozen": 12, "a dozen": 12,
    # isiXhosa (common forms used with plural nouns)
    "inye": 1, "nye": 1, "mbini": 2, "zimbini": 2, "ezimbini": 2, "ntathu": 3, "zintathu": 3,
    "ezintathu": 3, "zine": 4, "ezine": 4, "ntlanu": 5, "zintlanu": 5, "ezintlanu": 5,
    "ntandathu": 6, "zintandathu": 6, "ezintandathu": 6,
}

SPLIT_RE = re.compile(r"[,;\n/+]|\band\b|\bna\b|&", re.I)


@dataclass
class ParsedItem:
    product_id: str
    name: str
    qty: int
    unit: str
    urgent: bool = False
    source_text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _find_product(seg: str) -> tuple[str | None, int]:
    best: tuple[str | None, int, int] = (None, -1, 0)  # pid, pos, length
    for pid, words in LEXICON.items():
        for w in words:
            m = re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", seg)
            if m and len(w) > best[2]:
                best = (pid, m.start(), len(w))
    return best[0], best[1]


def _find_qty(seg: str, pid: str) -> int | None:
    money = re.search(r"\br\s?(\d+(?:[.,]\d+)?)", seg)
    if pid == "airtime" and money:
        return int(float(money.group(1).replace(",", ".")))
    seg_no_money = re.sub(r"\br\s?\d+(?:[.,]\d+)?", " ", seg)
    seg_no_units = re.sub(r"\d+(?:\.\d+)?\s?(kg|g|l|ml|litre|liter)s?\b", " ", seg_no_money)
    nums = re.findall(r"(?<![\d.])(\d{1,3})(?:\s?x)?(?![\d.])", seg_no_units)
    if nums:
        return int(nums[0])
    for word, val in sorted(NUMBER_WORDS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", seg_no_units):
            return val
    return None


def parse_note_deterministic(text: str) -> dict:
    items: dict[str, ParsedItem] = {}
    unrecognised: list[str] = []
    for raw in SPLIT_RE.split(text or ""):
        seg = raw.strip().lower()
        if not seg or not re.search(r"[a-z]", seg):
            continue
        pid, _ = _find_product(seg)
        if not pid:
            unrecognised.append(raw.strip())
            continue
        urgent = any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", seg) for w in OUT_OF_STOCK)
        qty = _find_qty(seg, pid) or DEFAULT_QTY[pid]
        qty = max(1, min(qty, 1000))
        if pid in items:
            items[pid].qty += qty
            items[pid].urgent = items[pid].urgent or urgent
            items[pid].source_text += f"; {raw.strip()}"
        else:
            items[pid] = ParsedItem(pid, PRODUCTS[pid]["name"], qty, PRODUCTS[pid]["unit"], urgent, raw.strip())
    return {"items": [i.to_dict() for i in items.values()], "unrecognised": unrecognised, "engine": "deterministic"}


PARSE_SYSTEM = """You are the parsing step of SpazaPilot, a restocking agent for South African spaza shops.
Owners write short, messy notes in English, isiXhosa, or a mix (e.g. "iphelile" = it is finished / out of stock).
Map every request to EXACTLY one of these catalogue product_ids:
{catalogue}
Rules:
- qty is an integer count of the catalogue unit. For airtime, qty is the rand face value (e.g. "airtime R50" -> 50).
- If the note says an item is finished/out of stock without a number, use this default qty: {defaults} and set urgent=true.
- Anything you cannot map goes in "unrecognised" (verbatim). Never invent products.
Return JSON only: {{"items":[{{"product_id":str,"qty":int,"urgent":bool,"source_text":str}}],"unrecognised":[str]}}"""

PHOTO_SYSTEM = """You are the vision step of SpazaPilot, a restocking agent for a South African spaza shop.
Look at the shelf photo and identify staple products that look EMPTY or LOW. Only use these product_ids:
{catalogue}
Suggest a sensible restock qty (use these defaults if unsure: {defaults}). Mark empty shelves urgent=true.
Return JSON only: {{"items":[{{"product_id":str,"qty":int,"urgent":bool,"source_text":"what you saw"}}],"unrecognised":[str]}}"""


def _catalogue_text() -> str:
    return "\n".join(f"- {pid}: {p['name']} (unit: {p['unit']})" for pid, p in PRODUCTS.items())


def validate_ai_items(data: dict) -> dict:
    """Never trust model output: keep only known products with sane quantities."""
    items: dict[str, ParsedItem] = {}
    unrecognised = [str(u) for u in (data.get("unrecognised") or [])][:20]
    for it in data.get("items") or []:
        pid = str(it.get("product_id", "")).strip()
        try:
            qty = int(float(it.get("qty", 0)))
        except (TypeError, ValueError):
            qty = 0
        if pid not in PRODUCTS or qty <= 0:
            unrecognised.append(str(it.get("source_text") or pid))
            continue
        qty = min(qty, 1000)
        if pid in items:
            items[pid].qty += qty
        else:
            items[pid] = ParsedItem(pid, PRODUCTS[pid]["name"], qty, PRODUCTS[pid]["unit"],
                                    bool(it.get("urgent")), str(it.get("source_text", ""))[:120])
    return {"items": [i.to_dict() for i in items.values()], "unrecognised": unrecognised}


def parse_note(text: str) -> dict:
    if settings.ai_mock:
        return parse_note_deterministic(text)
    try:
        system = PARSE_SYSTEM.format(catalogue=_catalogue_text(), defaults=json.dumps(DEFAULT_QTY))
        out = validate_ai_items(ai.chat_json(system, f"Owner note:\n{text}"))
        out["engine"] = f"ai:{settings.ai_provider}"
        return out
    except Exception as exc:  # noqa: BLE001 - fall back, never block the owner
        out = parse_note_deterministic(text)
        out["engine"] = "deterministic (AI fallback)"
        out["warning"] = f"AI parse failed, used offline parser: {exc}"[:200]
        return out


MOCK_PHOTO_RESULT = {
    "items": [
        {"product_id": "maize_meal", "qty": 3, "urgent": True, "source_text": "maize meal shelf empty"},
        {"product_id": "cooking_oil", "qty": 4, "urgent": False, "source_text": "only 1 bottle of oil left"},
        {"product_id": "candles", "qty": 5, "urgent": False, "source_text": "candle packs running low"},
    ],
    "unrecognised": [],
}


def parse_photo(image_b64: str, mime: str = "image/jpeg") -> dict:
    if settings.ai_mock:
        out = validate_ai_items(MOCK_PHOTO_RESULT)
        out["engine"] = "mock-vision"
        out["warning"] = "Mock mode: photo not analysed, showing a sample detection."
        return out
    system = PHOTO_SYSTEM.format(catalogue=_catalogue_text(), defaults=json.dumps(DEFAULT_QTY))
    out = validate_ai_items(ai.chat_json(system, "Shelf photo attached.", image_b64=image_b64, image_mime=mime))
    out["engine"] = f"ai-vision:{settings.ai_provider}"
    return out
