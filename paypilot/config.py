"""Runtime settings, read once from environment variables (and a .env file if present).

Secrets are only read here and handed to the adapters that need them. They never go into a prompt, API
response, event, log line or error (brief, principle 4); every secret field is excluded from repr().
A real adapter is used only when its credentials are set AND demo mode is off.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

DEFAULT_POLICY: dict = {
    "allowed_actions": ["create_invoice", "send_reminder", "refund"],
    "max_invoice_amount": 50000.0,
    "max_refund_amount": 10000.0,
    "daily_refund_limit": 15000.0,
    "max_reminders_per_invoice_per_day": 1,
    "allowed_currencies": ["INR", "USD"],
    "require_approved_customer": True,
    "reminder_channels": ["provider", "email", "whatsapp"],
    "owner_notifications": {
        "invoice.paid": ["slack"],
        "action.proposed": ["slack"],
        "refund.completed": ["slack", "email"],
    },
}

# (name, email, approved, phone). Kept in step with the demo data in docs/REQUIREMENTS.md.
SEED_CUSTOMERS: list[tuple[str, str, bool, str | None]] = [
    ("Rahul Mehta", "rahul.mehta@example.com", True, "+919800000001"),
    ("Priya Nair", "priya.nair@example.com", True, "+919800000002"),
    ("Kabir Singh", "kabir@cafe-kabir.example", True, "+919800000003"),
    ("Neha Joshi", "neha.joshi@example.com", True, None),
    ("Unverified Lead", "lead@unknown.example", False, None),
]

PAYMENT_PROVIDERS = ("paypal", "razorpay")
# Signs simulated webhooks in demo mode. Not a secret worth protecting: it only authenticates simulator
# traffic, and real providers use their own secrets. Override with PAYPILOT_SIM_WEBHOOK_SECRET.
DEV_SIM_WEBHOOK_SECRET = "paypilot-dev-simulator-secret"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _flag(value: str | None) -> bool | None:
    """'true'/'false'-style env value -> bool; unset or unrecognised -> None."""
    v = (value or "").strip().lower()
    return True if v in _TRUE else False if v in _FALSE else None


def _default_db_path(env: Mapping[str, str]) -> str:
    """data/paypilot.db locally. On Vercel the deployment is read-only except the temp dir, so use that
    (it is per-instance and ephemeral: set PAYPILOT_DB to choose another location)."""
    if env.get("VERCEL"):
        import tempfile

        return os.path.join(tempfile.gettempdir(), "paypilot.db")
    return "data/paypilot.db"


def _secret():
    return field(default="", repr=False)


@dataclass(frozen=True)
class Settings:
    db_path: str = "data/paypilot.db"
    db_dir: str = ""                          # PAYPILOT_DB_DIR: demo workspaces; "" = the temp dir
    frontend_url: str = ""                    # PAYPILOT_FRONTEND_URL: "/" redirects there when https
    business_name: str = "Asha Design Studio"
    business_email: str = "asha@studio.example"
    owner_email: str = ""
    currency: str = "INR"
    model: str = "claude-sonnet-5-5"
    payment_provider: str = "paypal"
    demo_mode_override: bool | None = None   # PAYPILOT_DEMO_MODE; None = decide from keys
    open_access_override: bool | None = None  # PAYPILOT_OPEN_ACCESS; None = same as demo mode
    allow_http_webhooks: bool = False         # PAYPILOT_ALLOW_HTTP_WEBHOOKS (local testing only)
    allow_private_webhooks: bool = False      # PAYPILOT_ALLOW_PRIVATE_WEBHOOKS (local testing only)
    # Claude
    anthropic_api_key: str = _secret()
    # PayPal
    paypal_client_id: str = _secret()
    paypal_client_secret: str = _secret()
    paypal_webhook_id: str = _secret()
    # Razorpay
    razorpay_key_id: str = _secret()
    razorpay_key_secret: str = _secret()
    razorpay_webhook_secret: str = _secret()
    # Owner login and scheduled jobs
    owner_password: str = _secret()
    session_secret: str = _secret()
    cron_secret: str = _secret()
    # Simulators
    sim_webhook_secret: str = field(default=DEV_SIM_WEBHOOK_SECRET, repr=False)
    # Email (SMTP)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = _secret()
    smtp_from: str = ""
    # Slack (the incoming-webhook URL itself is the credential)
    slack_webhook_url: str = _secret()
    # WhatsApp (Twilio)
    twilio_account_sid: str = _secret()
    twilio_auth_token: str = _secret()
    twilio_whatsapp_from: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Build from os.environ (after loading .env), or from an explicit mapping in tests."""
        if env is None:
            try:
                from dotenv import load_dotenv

                load_dotenv(override=False)  # real environment variables win over .env
            except ImportError:  # pragma: no cover
                pass
            env = os.environ
        get = lambda key, default="": (env.get(key) or default).strip()  # noqa: E731
        provider = get("PAYPILOT_PAYMENT_PROVIDER", "paypal").lower()
        if provider not in PAYMENT_PROVIDERS:
            raise ValueError(f"PAYPILOT_PAYMENT_PROVIDER must be one of {', '.join(PAYMENT_PROVIDERS)}, "
                             f"not {provider!r}")
        try:
            smtp_port = int(get("SMTP_PORT", "587"))
        except ValueError:
            raise ValueError("SMTP_PORT must be a number") from None
        return cls(
            db_path=get("PAYPILOT_DB", _default_db_path(env)),
            db_dir=get("PAYPILOT_DB_DIR"),
            frontend_url=get("PAYPILOT_FRONTEND_URL"),
            owner_password=env.get("PAYPILOT_OWNER_PASSWORD") or "",
            session_secret=get("PAYPILOT_SESSION_SECRET"),
            cron_secret=get("CRON_SECRET"),
            business_name=get("PAYPILOT_BUSINESS_NAME", "Asha Design Studio"),
            business_email=get("PAYPILOT_BUSINESS_EMAIL", "asha@studio.example"),
            owner_email=get("PAYPILOT_OWNER_EMAIL"),
            currency=get("PAYPILOT_CURRENCY", "INR").upper(),
            model=get("PAYPILOT_MODEL", "claude-sonnet-5-5"),
            payment_provider=provider,
            demo_mode_override=_flag(env.get("PAYPILOT_DEMO_MODE")),
            open_access_override=_flag(env.get("PAYPILOT_OPEN_ACCESS")),
            allow_http_webhooks=bool(_flag(env.get("PAYPILOT_ALLOW_HTTP_WEBHOOKS"))),
            allow_private_webhooks=bool(_flag(env.get("PAYPILOT_ALLOW_PRIVATE_WEBHOOKS"))),
            anthropic_api_key=get("ANTHROPIC_API_KEY"),
            paypal_client_id=get("PAYPAL_CLIENT_ID"),
            paypal_client_secret=get("PAYPAL_CLIENT_SECRET"),
            paypal_webhook_id=get("PAYPAL_WEBHOOK_ID"),
            razorpay_key_id=get("RAZORPAY_KEY_ID"),
            razorpay_key_secret=get("RAZORPAY_KEY_SECRET"),
            razorpay_webhook_secret=get("RAZORPAY_WEBHOOK_SECRET"),
            sim_webhook_secret=get("PAYPILOT_SIM_WEBHOOK_SECRET", DEV_SIM_WEBHOOK_SECRET),
            smtp_host=get("SMTP_HOST"),
            smtp_port=smtp_port,
            smtp_user=get("SMTP_USER"),
            smtp_password=get("SMTP_PASSWORD"),
            smtp_from=get("SMTP_FROM"),
            slack_webhook_url=get("SLACK_WEBHOOK_URL"),
            twilio_account_sid=get("TWILIO_ACCOUNT_SID"),
            twilio_auth_token=get("TWILIO_AUTH_TOKEN"),
            twilio_whatsapp_from=get("TWILIO_WHATSAPP_FROM"),
        )

    # ------------------------------------------------------------- credentials
    @property
    def has_paypal_keys(self) -> bool:
        return bool(self.paypal_client_id and self.paypal_client_secret)

    @property
    def has_razorpay_keys(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)

    @property
    def has_real_keys(self) -> bool:
        """Any payment-provider or Claude key is set."""
        return bool(self.paypal_client_id or self.paypal_client_secret or self.razorpay_key_id
                    or self.razorpay_key_secret or self.anthropic_api_key)

    # ------------------------------------------------------------------- modes
    @property
    def demo_mode(self) -> bool:
        if self.demo_mode_override is not None:
            return self.demo_mode_override
        return not self.has_real_keys

    @property
    def open_access(self) -> bool:
        """Whether the website can be used without an owner login. Defaults to demo mode."""
        return self.demo_mode if self.open_access_override is None else self.open_access_override

    @property
    def webhook_transport(self) -> str:
        """'simulate': the app generates signed webhooks itself (demo); 'http': real providers call us."""
        return "simulate" if self.demo_mode else "http"

    @property
    def backend_name(self) -> str:
        """Which PayPal adapter make_backend uses: the real sandbox or the simulator."""
        return "sandbox" if self.has_paypal_keys and not self.demo_mode else "mock"

    @property
    def agent_name(self) -> str:
        return "llm" if self.anthropic_api_key and not self.demo_mode else "rules"
