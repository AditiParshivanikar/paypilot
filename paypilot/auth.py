"""Who is calling: API keys (for integrations) and the owner's login session (for the website).

* API keys look like ``pp_<8 hex>_<random>``. Only their SHA-256 hash is stored; the key is shown once.
  Scopes: read, propose, approve, admin (default read + propose, so an integration can draft but never approve).
* The owner logs in with PAYPILOT_OWNER_PASSWORD (compared in constant time) and gets an HMAC-signed
  ``<expiry>.<mac>`` session token in the ``pp_owner`` cookie, valid for 12 hours. The signing key is
  PAYPILOT_SESSION_SECRET, or else derived from the password (so changing the password ends all sessions).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import time

from .integrations.base import hmac_sha256_hex, safe_equal

SCOPES = ("read", "propose", "approve", "admin")
DEFAULT_SCOPES = ("read", "propose")
OWNER_COOKIE = "pp_owner"
SESSION_TTL = 12 * 60 * 60
KEY_RE = re.compile(r"^pp_[0-9a-f]{8}_[A-Za-z0-9_-]{20,}$")
KEY_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def effective_scopes(scopes) -> set[str]:
    """'admin' implies every scope."""
    scopes = set(scopes or ())
    return set(SCOPES) if "admin" in scopes else scopes


# --------------------------------------------------------------------- API keys
def create_api_key(db, name: str, scopes=None) -> dict:
    """Create a key. The returned ``key`` is the only time the full value is ever available."""
    name = (name or "").strip()
    if not KEY_NAME_RE.match(name):
        raise ValueError("Key name must be 1-64 characters: letters, digits, '.', '_' or '-'")
    scopes = list(dict.fromkeys(scopes or DEFAULT_SCOPES))
    if unknown := [s for s in scopes if s not in SCOPES]:
        raise ValueError(f"Unknown scope(s): {', '.join(unknown)} (allowed: {', '.join(SCOPES)})")
    prefix = f"pp_{secrets.token_hex(4)}"
    key = f"{prefix}_{secrets.token_urlsafe(24)}"
    try:
        key_id = db.add_api_key(name, prefix, hash_key(key), scopes)
    except sqlite3.IntegrityError:
        raise ValueError(f"A key named {name!r} already exists") from None
    db.log("user", "created API key", {"id": key_id, "name": name, "prefix": prefix, "scopes": scopes})
    return {"id": key_id, "name": name, "prefix": prefix, "scopes": scopes, "key": key}


def resolve_api_key(db, key: str | None) -> dict | None:
    """The active key record {id, name, prefix, scopes} for a presented key, or None."""
    key = (key or "").strip()
    if not KEY_RE.match(key):
        return None
    return db.find_api_key(hash_key(key))


# ----------------------------------------------------------------- owner login
def check_owner_password(settings, password: str | None) -> bool:
    """Constant-time comparison (both sides hashed to equal length). False when no password is configured."""
    if not settings.owner_password or password is None:
        return False
    return hmac.compare_digest(hashlib.sha256(password.encode("utf-8")).digest(),
                               hashlib.sha256(settings.owner_password.encode("utf-8")).digest())


def _session_key(settings) -> str | None:
    if settings.session_secret:
        return settings.session_secret
    if settings.owner_password:
        return hashlib.sha256(f"paypilot-session:{settings.owner_password}".encode("utf-8")).hexdigest()
    return None


def issue_owner_session(settings, now: int | None = None) -> str:
    key = _session_key(settings)
    if key is None:
        raise ValueError("Owner login is not configured (set PAYPILOT_OWNER_PASSWORD)")
    exp = int(time.time() if now is None else now) + SESSION_TTL
    return f"{exp}.{hmac_sha256_hex(key, str(exp))}"


def valid_owner_session(settings, token: str | None, now: int | None = None) -> bool:
    key = _session_key(settings)
    if key is None or not token or token.count(".") != 1:
        return False
    exp_text, mac = token.split(".")
    if not exp_text.isdigit():
        return False
    exp, now = int(exp_text), int(time.time() if now is None else now)
    if exp <= now or exp > now + SESSION_TTL + 60:  # expired, or further out than we ever issue
        return False
    return safe_equal(mac, hmac_sha256_hex(key, exp_text))
