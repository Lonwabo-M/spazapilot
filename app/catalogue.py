"""Seeded demo catalogue: 10 staple products and 3 fictional wholesalers around
Paarl / Mbekweni (Western Cape). Prices are illustrative ZAR, not real quotes."""
from __future__ import annotations

PRODUCTS = {
    "sugar":       {"name": "White sugar 2.5kg",          "unit": "bag"},
    "maize_meal":  {"name": "Super maize meal 10kg",      "unit": "bag"},
    "bread":       {"name": "Brown bread 700g",           "unit": "loaf"},
    "maas":        {"name": "Amasi (maas) 2L",            "unit": "bottle"},
    "milk":        {"name": "Long-life milk 1L",          "unit": "carton"},
    "eggs":        {"name": "Large eggs (tray of 30)",    "unit": "tray"},
    "cooking_oil": {"name": "Sunflower oil 2L",           "unit": "bottle"},
    "rice":        {"name": "Long-grain rice 2kg",        "unit": "bag"},
    "paraffin":    {"name": "Paraffin 1L",                "unit": "bottle"},
    "airtime":     {"name": "Prepaid airtime voucher",    "unit": "rand"},  # priced per R1 of face value
    "candles":     {"name": "Household candles (6 pack)", "unit": "pack"},
    "soap":        {"name": "Bar soap (5 pack)",          "unit": "pack"},
}

# delivery_days: 0=Mon ... 6=Sun ; prices: product_id -> ZAR per unit
SUPPLIERS = {
    "mbekweni_cc": {
        "name": "Mbekweni Cash & Carry",
        "area": "Mbekweni",
        "min_order": 250.0,
        "delivery_fee": 0.0,
        "delivery_days": [1, 4],  # Tue, Fri
        "payee_email": "mbekweni-cc@spazapilot.example",
        "verified": True,
        "prices": {
            "sugar": 52.99, "maize_meal": 99.99, "bread": 17.49, "maas": 38.99,
            "milk": 17.99, "cooking_oil": 62.99, "candles": 24.99, "soap": 34.99,
            "paraffin": 26.99,
        },
    },
    "boland_wholesale": {
        "name": "Boland Wholesale (Paarl)",
        "area": "Paarl",
        "min_order": 500.0,
        "delivery_fee": 60.0,
        "delivery_days": [0, 2, 4],  # Mon, Wed, Fri
        "payee_email": "boland-wholesale@spazapilot.example",
        "verified": True,
        "prices": {
            "sugar": 47.99, "maize_meal": 92.49, "bread": 16.99, "maas": 41.49,
            "milk": 16.49, "eggs": 74.99, "cooking_oil": 57.99, "rice": 36.99,
            "paraffin": 24.99, "candles": 21.99, "soap": 31.49,
        },
    },
    "kasi_direct": {
        "name": "Kasi Direct Distributors",
        "area": "Drakenstein",
        "min_order": 120.0,
        "delivery_fee": 35.0,
        "delivery_days": [0, 1, 2, 3, 4, 5],  # Mon-Sat, next day
        "payee_email": "kasi-direct@spazapilot.example",
        "verified": True,
        "prices": {
            "sugar": 55.49, "bread": 18.99, "maas": 36.49, "milk": 18.49,
            "eggs": 79.99, "cooking_oil": 64.99, "rice": 39.99, "airtime": 0.97,
            "candles": 26.49, "soap": 36.99,
        },
    },
}


def product_name(pid: str) -> str:
    return PRODUCTS.get(pid, {}).get("name", pid)
