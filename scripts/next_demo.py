"""Drive the PayPilot website end to end in a real browser and save screenshots.

Light and dark passes at 1366x900 cover the landing page, the assistant (including a blocked request and a
prompt-injection invoice), approvals, invoices, the dashboard, integrations, activity and settings. A 390x844
mobile pass then checks that no page scrolls sideways. Page errors and console errors fail the run.

    pip install playwright && python -m playwright install chromium
    python scripts/next_demo.py [http://localhost:3000]

Screenshots go to docs/screenshots/next/. Exit code 0 only when there are no JS errors and no mobile overflow.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000").rstrip("/")
OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots" / "next"
DESKTOP = {"width": 1366, "height": 900}
MOBILE = {"width": 390, "height": 844}
TIMEOUT = 30_000

errors: list[str] = []
overflow: list[str] = []


def watch(page: Page, label: str) -> None:
    page.on("pageerror", lambda e: errors.append(f"[{label}] page error: {e}"))
    page.on("console", lambda m: errors.append(f"[{label}] console {m.type}: {m.text}") if m.type == "error" else None)


# Full-page captures stretch the viewport to the page height, so sticky headers, sidebars and the chat composer
# would be drawn part-way down the image. Release them to normal flow while the capture is taken.
UNSTICK = "[class~='sticky'] { position: static !important; } .h-screen { height: 100% !important; }"


def shot(page: Page, name: str, full_page: bool = False) -> None:
    page.wait_for_timeout(500)  # let toasts, charts and transitions settle
    style = page.add_style_tag(content=UNSTICK) if full_page else None
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=full_page)
    if style:
        style.evaluate("el => el.remove()")
    print(f"  saved {name}.png")


def say(page: Page, text: str, expect_text: str) -> None:
    """Send a chat message and wait for one more visible copy of ``expect_text`` in the replies."""
    replies = page.get_by_text(expect_text)
    before = replies.count()
    box = page.get_by_label("Message")
    box.fill(text)
    box.press("Enter")
    expect(replies).to_have_count(before + 1, timeout=TIMEOUT)


def check_overflow(page: Page, label: str) -> None:
    fits = page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    width = page.evaluate("document.documentElement.scrollWidth")
    print(f"  {label}: page width {width}px {'OK' if fits else 'OVERFLOWS'}")
    if not fits:
        overflow.append(f"{label}: scrollWidth {width} > {MOBILE['width']}")


def desktop_pass(browser, scheme: str) -> None:
    print(f"\n== {scheme} mode, {DESKTOP['width']}x{DESKTOP['height']}")
    context = browser.new_context(viewport=DESKTOP, color_scheme=scheme)  # fresh demo workspace
    page = context.new_page()
    page.on("dialog", lambda d: d.accept())
    watch(page, scheme)
    p = f"{scheme}_"

    page.goto(BASE + "/")
    page.get_by_role("heading", level=1).wait_for(timeout=TIMEOUT)
    shot(page, p + "01_landing_hero")
    shot(page, p + "02_landing_full", full_page=True)

    page.goto(BASE + "/app/chat")
    page.get_by_text("What should we get paid for today?").wait_for(timeout=TIMEOUT)
    shot(page, p + "03_chat_empty")
    say(page, "Invoice Rahul for ₹4,000 for logo design, due Friday", "Drafted and waiting")
    say(page, "Who hasn't paid me?", "Total outstanding")
    say(page, "Invoice Rahul for ₹90,000 for app development", "Blocked by the safety gate")
    expect(page.get_by_text("Blocked by the safety gate").last).to_be_visible()
    say(page, "Show invoice 0005", "looks like hidden instructions")
    say(page, "Remind Neha", "Drafted and waiting")
    shot(page, p + "04_chat_conversation", full_page=True)

    page.goto(BASE + "/app/approvals")
    page.get_by_role("tab", name="Waiting (2)").wait_for(timeout=TIMEOUT)
    page.get_by_role("button", name="Approve", exact=True).first.click()
    page.get_by_role("tab", name="Waiting (1)").wait_for(timeout=TIMEOUT)
    shot(page, p + "05_approvals")

    page.goto(BASE + "/app/invoices")
    page.get_by_role("button", name="Customer pays", exact=True).first.click()
    page.get_by_text("Payment received").first.wait_for(timeout=TIMEOUT)
    shot(page, p + "06_invoices")

    page.goto(BASE + "/app")
    bar = page.locator("figure [role=img] svg path").first
    bar.wait_for(timeout=TIMEOUT)
    page.wait_for_timeout(600)
    bar.hover()
    shot(page, p + "07_dashboard")

    page.goto(BASE + "/app/integrations")
    page.get_by_text("Tally XML").first.wait_for(timeout=TIMEOUT)
    shot(page, p + "08_integrations_apps", full_page=True)
    page.get_by_role("tab", name="Webhooks").click()
    page.get_by_label("Endpoint URL").fill("https://hooks.example.com/paypilot")
    page.get_by_role("button", name="Add webhook", exact=True).click()
    page.get_by_text("Signing secret (shown once)").wait_for(timeout=TIMEOUT)
    shot(page, p + "09_integrations_webhooks", full_page=True)
    page.get_by_role("tab", name="API keys").click()
    page.get_by_label("Name", exact=True).fill("zapier")
    page.get_by_role("button", name="Create key", exact=True).click()
    page.get_by_text("Your new key (shown once)").wait_for(timeout=TIMEOUT)
    shot(page, p + "10_integrations_keys")

    page.goto(BASE + "/app/activity")
    page.get_by_role("group", name="Filter by actor").wait_for(timeout=TIMEOUT)
    shot(page, p + "11_activity")

    page.goto(BASE + "/app/settings")
    page.get_by_label("Max invoice amount").wait_for(timeout=TIMEOUT)
    shot(page, p + "12_settings", full_page=True)
    context.close()


def mobile_pass(browser) -> None:
    print(f"\n== mobile, {MOBILE['width']}x{MOBILE['height']}")
    context = browser.new_context(viewport=MOBILE, color_scheme="light", is_mobile=True, has_touch=True)
    page = context.new_page()
    watch(page, "mobile")

    page.goto(BASE + "/")
    page.get_by_role("heading", level=1).wait_for(timeout=TIMEOUT)
    check_overflow(page, "landing")
    shot(page, "mobile_01_landing")

    page.goto(BASE + "/app")
    page.locator("figure [role=img] svg path").first.wait_for(timeout=TIMEOUT)
    check_overflow(page, "dashboard")
    shot(page, "mobile_02_dashboard")

    page.get_by_role("button", name="Open menu").click()
    menu = page.get_by_role("dialog")
    menu.wait_for(timeout=TIMEOUT)
    shot(page, "mobile_03_menu")
    menu.get_by_role("link", name="Assistant", exact=True).click()
    page.get_by_text("What should we get paid for today?").wait_for(timeout=TIMEOUT)
    check_overflow(page, "assistant")
    say(page, "Refund Priya ₹2,000 because the project was cut short", "Drafted and waiting")
    check_overflow(page, "assistant after reply")
    shot(page, "mobile_04_chat")
    context.close()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"PayPilot UI demo against {BASE}\nScreenshots: {OUT}")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for scheme in ("light", "dark"):
                desktop_pass(browser, scheme)
            mobile_pass(browser)
        finally:
            browser.close()
    print()
    for line in errors + overflow:
        print("FAIL", line)
    if errors or overflow:
        print(f"{len(errors)} JS error(s), {len(overflow)} mobile overflow(s).")
        return 1
    print("No JS errors, no mobile overflow.")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
