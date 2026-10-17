"""SQLite storage: customers, pending actions, audit log, settings, notifications and simulator invoices.

All SQL lives in this class. JSON columns are encoded on write and decoded on read, so callers only
ever see Python dicts and lists.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DEFAULT_POLICY, SEED_CUSTOMERS

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL UNIQUE,
    email    TEXT NOT NULL,
    approved INTEGER NOT NULL DEFAULT 1,
    phone    TEXT
);
CREATE TABLE IF NOT EXISTS pending_actions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    action     TEXT NOT NULL,
    params     TEXT NOT NULL,                    -- json
    preview    TEXT NOT NULL,                    -- json
    rationale  TEXT,
    warnings   TEXT NOT NULL DEFAULT '[]',       -- json
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending | rejected | approved | executed | failed
    decided_at TEXT,
    result     TEXT,                             -- json
    source     TEXT                              -- chat | ui | mcp | api:<key name>
);
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    actor     TEXT NOT NULL,                     -- user | agent | policy | executor | webhook | system
    event     TEXT NOT NULL,
    detail    TEXT,                              -- json
    action_id INTEGER
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL                          -- json
);
CREATE TABLE IF NOT EXISTS mock_invoices (
    id   TEXT PRIMARY KEY,
    data TEXT NOT NULL                           -- json
);
CREATE TABLE IF NOT EXISTS notifications (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    channel   TEXT NOT NULL,                     -- email | whatsapp | slack
    adapter   TEXT NOT NULL,                     -- smtp | twilio | slack-webhook | simulator ...
    recipient TEXT,
    subject   TEXT,
    body      TEXT,
    status    TEXT NOT NULL,                     -- sent | simulated | failed
    detail    TEXT                               -- json
);
CREATE TABLE IF NOT EXISTS events (
    id      TEXT PRIMARY KEY,                    -- evt_...
    ts      TEXT NOT NULL,
    type    TEXT NOT NULL,
    data    TEXT NOT NULL                        -- json
);
CREATE TABLE IF NOT EXISTS webhook_subscriptions (
    id          TEXT PRIMARY KEY,                -- sub_...
    created_at  TEXT NOT NULL,
    url         TEXT NOT NULL,
    events      TEXT NOT NULL,                   -- json list of event types, or ["*"]
    description TEXT,
    secret      TEXT NOT NULL,                   -- whsec_...; signs deliveries; never listed
    active      INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    event_id        TEXT NOT NULL,
    subscription_id TEXT NOT NULL,
    attempt         INTEGER NOT NULL,
    ok              INTEGER NOT NULL,
    status_code     INTEGER,
    error           TEXT
);
CREATE TABLE IF NOT EXISTS webhook_receipts (
    provider    TEXT NOT NULL,
    event_id    TEXT NOT NULL,
    received_at TEXT NOT NULL,
    PRIMARY KEY (provider, event_id)
);
CREATE TABLE IF NOT EXISTS api_keys (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    name       TEXT NOT NULL UNIQUE,
    prefix     TEXT NOT NULL,                    -- first characters, for display
    key_hash   TEXT NOT NULL UNIQUE,             -- the key itself is never stored
    scopes     TEXT NOT NULL DEFAULT '["read", "propose"]',  -- json
    last_used  TEXT,
    revoked    INTEGER NOT NULL DEFAULT 0
);
"""

# Columns added after the first release: (table, column, DDL). Applied to older databases on open.
MIGRATIONS = [("customers", "phone", "ALTER TABLE customers ADD COLUMN phone TEXT"),
              ("pending_actions", "source", "ALTER TABLE pending_actions ADD COLUMN source TEXT"),
              ("api_keys", "scopes", "ALTER TABLE api_keys ADD COLUMN scopes TEXT NOT NULL DEFAULT '[\"read\", \"propose\"]'")]

_ACTION_JSON = ("params", "preview", "warnings", "result")


def now_iso() -> str:
    """Server-local time with its UTC offset, so the date part is the server's 'today' (brief, Conventions)."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


class Database:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            if path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()
        self._seed()

    # ------------------------------------------------------------------ helpers
    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur

    def _all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def _migrate(self) -> None:
        for table, column, ddl in MIGRATIONS:
            existing = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in existing:
                self._conn.execute(ddl)

    def _seed(self) -> None:
        with self._lock:
            if not self._all("SELECT id FROM customers LIMIT 1"):
                for name, email, approved, phone in SEED_CUSTOMERS:
                    self.upsert_customer(name, email, approved, phone)
            if self.get_setting("policy") is None:
                self.set_setting("policy", DEFAULT_POLICY)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------------------------------------------------------- settings
    def get_setting(self, key: str, default: Any = None) -> Any:
        rows = self._all("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_setting(self, key: str, value: Any) -> None:
        self._exec("INSERT INTO settings(key, value) VALUES (?, ?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, _dumps(value)))

    def get_policy(self) -> dict:
        """The stored policy merged over DEFAULT_POLICY, so new default keys apply to old databases."""
        return {**json.loads(json.dumps(DEFAULT_POLICY)), **(self.get_setting("policy") or {})}

    # --------------------------------------------------------------- customers
    @staticmethod
    def _customer(row: dict) -> dict:
        return {**row, "approved": bool(row["approved"])}

    def customers(self) -> list[dict]:
        return [self._customer(r) for r in self._all("SELECT * FROM customers ORDER BY name")]

    def find_customer(self, name_or_email: str | None) -> dict | None:
        """Exact name or email (case-insensitive), else an unambiguous first-name match ("Rahul")."""
        q = " ".join((name_or_email or "").split()).lower()
        if not q:
            return None
        rows = self.customers()
        for r in rows:
            if q in (r["name"].lower(), r["email"].lower()):
                return r
        first = q.split()[0]
        matches = [r for r in rows if r["name"].lower().split()[0] == first]
        return matches[0] if len(matches) == 1 else None

    def upsert_customer(self, name: str, email: str, approved: bool = True, phone: str | None = None) -> dict:
        name, email, phone = name.strip(), email.strip(), (phone or "").strip() or None
        self._exec("INSERT INTO customers(name, email, approved, phone) VALUES (?, ?, ?, ?) "
                   "ON CONFLICT(name) DO UPDATE SET email=excluded.email, approved=excluded.approved, "
                   "phone=excluded.phone", (name, email, int(bool(approved)), phone))
        return self._customer(self._all("SELECT * FROM customers WHERE name=?", (name,))[0])

    # -------------------------------------------------------------------- audit
    def log(self, actor: str, event: str, detail: Any = None, action_id: int | None = None) -> None:
        self._exec("INSERT INTO audit_log(ts, actor, event, detail, action_id) VALUES (?, ?, ?, ?, ?)",
                   (now_iso(), actor, event, None if detail is None else _dumps(detail), action_id))

    def audit(self, limit: int = 500) -> list[dict]:
        """Newest first."""
        rows = self._all("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["detail"] = json.loads(r["detail"]) if r["detail"] else None
        return rows

    # ---------------------------------------------------------- pending actions
    def add_pending(self, action: str, params: dict, preview: dict, rationale: str = "",
                    warnings: list[str] | None = None, source: str = "chat") -> int:
        cur = self._exec(
            "INSERT INTO pending_actions(created_at, action, params, preview, rationale, warnings, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now_iso(), action, _dumps(params), _dumps(preview), rationale, _dumps(warnings or []), source))
        return int(cur.lastrowid)

    @staticmethod
    def _action(row: dict) -> dict:
        for k in _ACTION_JSON:
            row[k] = json.loads(row[k]) if row[k] else None
        row["warnings"] = row["warnings"] or []
        return row

    def get_action(self, action_id: int) -> dict | None:
        rows = self._all("SELECT * FROM pending_actions WHERE id=?", (action_id,))
        return self._action(rows[0]) if rows else None

    def actions(self, status: str | None = None) -> list[dict]:
        """Newest first, optionally filtered by status."""
        if status:
            rows = self._all("SELECT * FROM pending_actions WHERE status=? ORDER BY id DESC", (status,))
        else:
            rows = self._all("SELECT * FROM pending_actions ORDER BY id DESC")
        return [self._action(r) for r in rows]

    def set_action_status(self, action_id: int, status: str, result: Any = None) -> None:
        self._exec("UPDATE pending_actions SET status=?, decided_at=?, result=? WHERE id=?",
                   (status, now_iso(), None if result is None else _dumps(result), action_id))

    # --------------------------------------------------------- mock invoices
    def mock_put(self, invoice: dict) -> None:
        self._exec("INSERT INTO mock_invoices(id, data) VALUES (?, ?) "
                   "ON CONFLICT(id) DO UPDATE SET data=excluded.data", (invoice["id"], _dumps(invoice)))

    def mock_all(self) -> list[dict]:
        return [json.loads(r["data"]) for r in self._all("SELECT data FROM mock_invoices ORDER BY rowid")]

    def mock_get(self, invoice_id: str) -> dict | None:
        rows = self._all("SELECT data FROM mock_invoices WHERE id=?", (invoice_id,))
        return json.loads(rows[0]["data"]) if rows else None

    # --------------------------------------------------------- notifications
    def add_notification(self, channel: str, adapter: str, recipient: str | None, subject: str | None,
                         body: str | None, status: str, detail: Any = None) -> int:
        cur = self._exec(
            "INSERT INTO notifications(ts, channel, adapter, recipient, subject, body, status, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now_iso(), channel, adapter, recipient, subject, body, status,
             None if detail is None else _dumps(detail)))
        return int(cur.lastrowid)

    def notifications(self, limit: int = 200) -> list[dict]:
        """Newest first."""
        rows = self._all("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["detail"] = json.loads(r["detail"]) if r["detail"] else None
        return rows

    # ----------------------------------------------------------------- events
    def add_event(self, type: str, data: dict) -> dict:
        event = {"id": "evt_" + secrets.token_hex(12), "type": type, "created": now_iso(), "data": data}
        self._exec("INSERT INTO events(id, ts, type, data) VALUES (?, ?, ?, ?)",
                   (event["id"], event["created"], type, _dumps(data)))
        return event

    @staticmethod
    def _event(row: dict) -> dict:
        return {"id": row["id"], "type": row["type"], "created": row["ts"], "data": json.loads(row["data"])}

    def get_event(self, event_id: str) -> dict | None:
        rows = self._all("SELECT * FROM events WHERE id=?", (event_id,))
        return self._event(rows[0]) if rows else None

    def events(self, limit: int = 200, type: str | None = None) -> list[dict]:
        """Newest first."""
        if type:
            rows = self._all("SELECT * FROM events WHERE type=? ORDER BY rowid DESC LIMIT ?", (type, limit))
        else:
            rows = self._all("SELECT * FROM events ORDER BY rowid DESC LIMIT ?", (limit,))
        return [self._event(r) for r in rows]

    # ---------------------------------------------------------- subscriptions
    def add_subscription(self, url: str, events: list[str], description: str, secret: str) -> str:
        sub_id = "sub_" + secrets.token_hex(8)
        self._exec("INSERT INTO webhook_subscriptions(id, created_at, url, events, description, secret) "
                   "VALUES (?, ?, ?, ?, ?, ?)", (sub_id, now_iso(), url, _dumps(events), description, secret))
        return sub_id

    @staticmethod
    def _subscription(row: dict, with_secret: bool = False) -> dict:
        out = {**row, "events": json.loads(row["events"]), "active": bool(row["active"])}
        if not with_secret:
            out.pop("secret", None)
        return out

    def subscriptions(self, active_only: bool = False) -> list[dict]:
        """Without secrets."""
        sql = "SELECT * FROM webhook_subscriptions" + (" WHERE active=1" if active_only else "") + " ORDER BY rowid"
        return [self._subscription(r) for r in self._all(sql)]

    def get_subscription(self, sub_id: str, with_secret: bool = False) -> dict | None:
        rows = self._all("SELECT * FROM webhook_subscriptions WHERE id=?", (sub_id,))
        return self._subscription(rows[0], with_secret) if rows else None

    def subscriptions_for(self, event_type: str) -> list[dict]:
        """Active subscriptions matching an event type, WITH secrets (only for signing deliveries)."""
        rows = self._all("SELECT * FROM webhook_subscriptions WHERE active=1 ORDER BY rowid")
        subs = [self._subscription(r, with_secret=True) for r in rows]
        return [s for s in subs if event_type in s["events"] or "*" in s["events"]]

    def deactivate_subscription(self, sub_id: str) -> bool:
        return self._exec("UPDATE webhook_subscriptions SET active=0 WHERE id=?", (sub_id,)).rowcount > 0

    # ------------------------------------------------------------- deliveries
    def add_delivery(self, event_id: str, subscription_id: str, attempt: int, ok: bool,
                     status_code: int | None = None, error: str | None = None) -> int:
        cur = self._exec(
            "INSERT INTO webhook_deliveries(ts, event_id, subscription_id, attempt, ok, status_code, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now_iso(), event_id, subscription_id, attempt, int(bool(ok)), status_code, error))
        return int(cur.lastrowid)

    def deliveries(self, event_id: str | None = None, subscription_id: str | None = None,
                   limit: int = 500) -> list[dict]:
        """Oldest first."""
        where, args = [], []
        if event_id:
            where.append("event_id=?")
            args.append(event_id)
        if subscription_id:
            where.append("subscription_id=?")
            args.append(subscription_id)
        sql = ("SELECT * FROM webhook_deliveries" + (" WHERE " + " AND ".join(where) if where else "")
               + " ORDER BY id LIMIT ?")
        return [{**r, "ok": bool(r["ok"])} for r in self._all(sql, (*args, limit))]

    def failed_deliveries(self, max_attempts: int) -> list[dict]:
        """(event, subscription) pairs that never succeeded and have had fewer than max_attempts attempts."""
        return self._all(
            "SELECT d.event_id, d.subscription_id, MAX(d.attempt) AS attempts FROM webhook_deliveries d "
            "JOIN webhook_subscriptions s ON s.id = d.subscription_id AND s.active = 1 "
            "GROUP BY d.event_id, d.subscription_id HAVING SUM(d.ok) = 0 AND MAX(d.attempt) < ? "
            "ORDER BY MIN(d.id)", (max_attempts,))

    # --------------------------------------------------------------- receipts
    def claim_receipt(self, provider: str, event_id: str) -> bool:
        """Atomically claim an inbound provider event. True the first time, False for a duplicate (replay)."""
        cur = self._exec("INSERT OR IGNORE INTO webhook_receipts(provider, event_id, received_at) VALUES (?, ?, ?)",
                         (provider, event_id, now_iso()))
        return cur.rowcount == 1

    record_receipt = claim_receipt

    def release_receipt(self, provider: str, event_id: str) -> None:
        """Undo a claim when processing failed, so the provider's retry is processed instead of skipped."""
        self._exec("DELETE FROM webhook_receipts WHERE provider=? AND event_id=?", (provider, event_id))

    # --------------------------------------------------------------- api keys
    def add_api_key(self, name: str, prefix: str, key_hash: str, scopes: list[str] | None = None) -> int:
        cur = self._exec("INSERT INTO api_keys(created_at, name, prefix, key_hash, scopes) VALUES (?, ?, ?, ?, ?)",
                         (now_iso(), name.strip(), prefix, key_hash, _dumps(scopes or ["read", "propose"])))
        return int(cur.lastrowid)

    @staticmethod
    def _api_key(row: dict) -> dict:
        return {**row, "scopes": json.loads(row["scopes"]) if row.get("scopes") else ["read", "propose"],
                "revoked": bool(row.get("revoked", 0))}

    def api_keys(self) -> list[dict]:
        """Without hashes."""
        rows = self._all("SELECT id, created_at, name, prefix, scopes, last_used, revoked FROM api_keys ORDER BY id")
        return [self._api_key(r) for r in rows]

    def find_api_key(self, key_hash: str) -> dict | None:
        """An active key by its hash; records when it was last used."""
        rows = self._all("SELECT id, name, prefix, scopes FROM api_keys WHERE key_hash=? AND revoked=0", (key_hash,))
        if not rows:
            return None
        self._exec("UPDATE api_keys SET last_used=? WHERE id=?", (now_iso(), rows[0]["id"]))
        return self._api_key(rows[0])

    def revoke_api_key(self, key_id: int) -> bool:
        return self._exec("UPDATE api_keys SET revoked=1 WHERE id=? AND revoked=0", (key_id,)).rowcount > 0
