"""Shared fixtures: a fresh in-memory database with the demo data for every test, and date helpers.

All dates in tests are relative to today, matching the simulator's seed (docs/REQUIREMENTS.md, demo data).
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paypilot.agent import RuleAgent  # noqa: E402
from paypilot.backends import MockBackend  # noqa: E402
from paypilot.db import Database  # noqa: E402
from paypilot.service import PayPilotService  # noqa: E402


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


@pytest.fixture
def mock(db):
    return MockBackend(db)


@pytest.fixture
def svc(db, mock):
    return PayPilotService(db, mock)


@pytest.fixture
def agent(svc):
    return RuleAgent(svc)


def inv_by_number(source, number: str) -> dict:
    """Look up an invoice by number on a backend or a PayPilotService."""
    backend = getattr(source, "backend", source)
    return next(i for i in backend.list_invoices() if i["number"] == number)


def days_from_today(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


def next_weekday(weekday: int, today: date | None = None) -> date:
    """The next given weekday (Mon=0); on that weekday itself, the one a week later."""
    today = today or date.today()
    return today + timedelta(days=(weekday - today.weekday()) % 7 or 7)
