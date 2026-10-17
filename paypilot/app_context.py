"""Wires the app together once: settings -> database -> integration hub -> service -> agent.

Every entry point (the REST API, the MCP server, scripts and tests) builds its objects through here, so they
all share the same safety gate, adapters and audit log.
"""
from __future__ import annotations

from dataclasses import dataclass

from .agent import make_agent
from .config import Settings
from .db import Database
from .integrations.hub import IntegrationHub
from .service import PayPilotService


@dataclass
class AppContext:
    settings: Settings
    db: Database
    hub: IntegrationHub
    service: PayPilotService
    agent: object  # ClaudeAgent | RuleAgent


def build_context(settings: Settings | None = None, db: Database | None = None, db_path: str | None = None,
                  agent_client=None) -> AppContext:
    """``agent_client`` injects a Claude client (tests pass a fake); it forces the Claude agent."""
    settings = settings or Settings.from_env()
    db = db or Database(db_path or settings.db_path)
    hub = IntegrationHub(settings, db)
    service = PayPilotService(db, hub=hub)
    agent = make_agent(settings, service, client=agent_client)
    return AppContext(settings=settings, db=db, hub=hub, service=service, agent=agent)
