"""Turn human phrases into structured values: due dates ("Friday") and amounts ("₹4,000")."""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from .backends import round_money

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
DEFAULT_DUE_DAYS = 14


def _add_months(d: date, n: int) -> date:
    month = d.month - 1 + n
    year, month = d.year + month // 12, month % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _day_month(day: int, month: int, today: date) -> date | None:
    """30 Oct -> this year's 30 Oct, or next year's if that has passed. None if not a real date."""
    for year in (today.year, today.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            return None
        if d >= today:
            return d
    return None


def parse_due_date(text: str | None, today: date | None = None, default_days: int = DEFAULT_DUE_DAYS) -> str:
    """'Friday', 'today', 'tomorrow', 'in 10 days', 'next week', 'end of month', '2026-10-30', '30 Oct',
    'Oct 30'. Anything unrecognised gives today + 14 days. Returns ISO YYYY-MM-DD."""
    today = today or date.today()
    default = (today + timedelta(days=default_days)).isoformat()
    t = " ".join((text or "").lower().replace(",", " ").split()).strip(" .!?")
    if not t:
        return default
    if m := re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t):
        try:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        except ValueError:
            return default
    if re.search(r"\btoday\b", t):
        return today.isoformat()
    if re.search(r"\btomorrow\b", t):
        return (today + timedelta(days=1)).isoformat()
    if m := re.search(r"\bin\s+(\d{1,3})\s+(day|week|month)s?\b", t):
        n, unit = int(m[1]), m[2]
        if unit == "month":
            return _add_months(today, n).isoformat()
        return (today + timedelta(days=n * (7 if unit == "week" else 1))).isoformat()
    if re.search(r"\b(end of (the )?month|month end|month-end)\b", t):
        return today.replace(day=calendar.monthrange(today.year, today.month)[1]).isoformat()
    for i, wd in enumerate(WEEKDAYS):
        if re.search(rf"\b({wd}|{wd[:3]})\b", t):
            ahead = (i - today.weekday()) % 7 or 7  # "Friday" on a Friday means next week's
            return (today + timedelta(days=ahead)).isoformat()
    if re.search(r"\bnext week\b", t):
        return (today + timedelta(days=7)).isoformat()
    month_re = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
    m = (re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{month_re}", t)
         or re.search(rf"\b{month_re}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", t))
    if m:
        day, mon = (m[1], m[2]) if m[1].isdigit() else (m[2], m[1])
        d = _day_month(int(day), MONTHS.index(mon[:3]) + 1, today)
        return d.isoformat() if d else default
    return default


CURRENCY_WORDS = {"₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR", "rupee": "INR", "rupees": "INR",
                  "$": "USD", "usd": "USD", "dollar": "USD", "dollars": "USD"}
AMOUNT_RE = re.compile(
    r"(?P<pre>₹|\$|\brs\.?|\binr\b|\busd\b)?\s*"
    r"(?P<num>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<mult>k\b|lakhs?\b|lacs?\b)?\s*"
    r"(?P<post>inr\b|usd\b|rupees?\b|dollars?\b|rs\b)?",
    re.IGNORECASE)


def parse_amount(text: str | None, default_currency: str = "INR") -> tuple[float | None, str]:
    """'₹4,000', 'Rs 500', 'INR 1,20,000', '$120.50', '5k', '300 usd', '1.5 lakh' -> (amount, currency).
    Returns (None, default_currency) when there is no number."""
    m = AMOUNT_RE.search(text or "")
    if not m:
        return None, default_currency
    amount = float(m["num"].replace(",", ""))
    mult = (m["mult"] or "").lower()
    if mult == "k":
        amount *= 1000
    elif mult.startswith(("lakh", "lac")):
        amount *= 100000
    token = (m["pre"] or m["post"] or "").lower()
    return round_money(amount), CURRENCY_WORDS.get(token, default_currency.upper())
