"""SQLite persistence: settings, agent runs, supplier orders (the ledger) and an
append-only event log that doubles as the agent's audit trail."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

from .config import settings

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_conn_path: str | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, source TEXT, note TEXT,
  items_json TEXT, plan_json TEXT, savings REAL DEFAULT 0, engine TEXT);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, supplier_id TEXT, supplier_name TEXT,
  lines_json TEXT, subtotal REAL, delivery_fee REAL, total_zar REAL, total_usd REAL, currency TEXT,
  delivery_date TEXT, delivery_label TEXT, decision TEXT, reasons_json TEXT, status TEXT,
  paypal_order_id TEXT, paypal_capture_id TEXT, approve_url TEXT, payee_email TEXT,
  receipt TEXT, error TEXT, signature TEXT, created_at TEXT, updated_at TEXT, paid_at TEXT);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, run_id INTEGER, order_id INTEGER,
  kind TEXT, message TEXT, data_json TEXT);
"""

DEFAULT_POLICY = {
    "weekly_budget": 2500.0, "auto_approve_limit": 500.0, "agent_enabled": True,
    "max_qty_per_line": 60, "max_airtime_per_order": 500.0, "urgent_max_days": 1,
    "shop_name": "Nomsa's Spaza", "language": "en",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    global _conn, _conn_path
    with _lock:
        if _conn is None or _conn_path != settings.db_path:
            _conn = sqlite3.connect(settings.db_path, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.executescript(SCHEMA)
            _conn_path = settings.db_path
        return _conn


def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        c = conn()
        cur = c.execute(sql, params)
        c.commit()
        return cur


def query(sql: str, params: tuple = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, params).fetchall()]


def get_policy() -> dict:
    rows = query("SELECT value FROM settings WHERE key='policy'")
    pol = dict(DEFAULT_POLICY)
    if rows:
        pol.update(json.loads(rows[0]["value"]))
    return pol


def set_policy(update: dict) -> dict:
    pol = get_policy()
    pol.update({k: v for k, v in update.items() if k in DEFAULT_POLICY})
    execute("INSERT OR REPLACE INTO settings(key, value) VALUES('policy', ?)", (json.dumps(pol),))
    return pol


def get_setting(key: str, default: str | None = None) -> str | None:
    rows = query("SELECT value FROM settings WHERE key=?", (key,))
    return rows[0]["value"] if rows else default


def set_setting(key: str, value: str) -> None:
    execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)", (key, value))


def log_event(kind: str, message: str, run_id: int | None = None, order_id: int | None = None,
              data: dict | None = None) -> None:
    execute("INSERT INTO events(ts, run_id, order_id, kind, message, data_json) VALUES(?,?,?,?,?,?)",
            (now_iso(), run_id, order_id, kind, message, json.dumps(data or {})))


def order_row(order_id: int) -> dict | None:
    rows = query("SELECT * FROM orders WHERE id=?", (order_id,))
    return _decode(rows[0]) if rows else None


def _decode(r: dict) -> dict:
    r = dict(r)
    r["lines"] = json.loads(r.pop("lines_json") or "[]")
    r["reasons"] = json.loads(r.pop("reasons_json") or "[]")
    return r


def orders(where: str = "1=1", params: tuple = ()) -> list[dict]:
    return [_decode(r) for r in query(f"SELECT * FROM orders WHERE {where} ORDER BY id DESC", params)]


def reset_all() -> None:
    with _lock:
        c = conn()
        c.executescript("DELETE FROM orders; DELETE FROM runs; DELETE FROM events; DELETE FROM settings;"
                        "DELETE FROM sqlite_sequence;")
        c.commit()
