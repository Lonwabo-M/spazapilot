"""Start the app in mock mode on a throwaway DB, drive the UI with a headless
browser and save phone-sized screenshots to docs/screenshots/.

    pip install playwright && python -m playwright install firefox   # or chromium
    python scripts/screenshots.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ["no_proxy"] = os.environ["NO_PROXY"]
PORT = int(os.getenv("SHOT_PORT", "8765"))
URL = f"http://127.0.0.1:{PORT}"
BIG = "iswekile 5, isonka 10, amasi iphelile, mealie meal 4, oil 6, amaqanda 2, airtime R100"


def wait_up():
    for _ in range(60):
        try:
            if httpx.get(URL + "/api/health", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise RuntimeError("server did not start")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    db = Path(tempfile.mkdtemp()) / "shots.db"
    env = {**os.environ, "SPAZAPILOT_FORCE_MOCK": "1", "SPAZAPILOT_DB": str(db), "PUBLIC_BASE_URL": URL}
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT), "--log-level", "warning"],
                           cwd=ROOT, env=env)
    try:
        wait_up()
        httpx.put(URL + "/api/policy", json={"shop_name": "Nomsa's Spaza · Mbekweni"})
        with sync_playwright() as p:
            browser_type = p.chromium if os.getenv("SHOT_BROWSER") == "chromium" else p.firefox
            b = browser_type.launch()
            ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
            page = ctx.new_page()
            def shot(name, full=False):
                # in full-page captures, let the bottom nav sit at the end instead of floating mid-page
                page.evaluate(f"document.body.classList.toggle('shot', {str(full).lower()}); document.getElementById('toast').classList.add('hidden')")
                page.screenshot(path=str(OUT / name), full_page=full)
                page.evaluate("document.body.classList.remove('shot')")

            page.goto(URL + "/#restock")
            page.wait_for_selector("#modes .pill")
            shot("01-home.png")

            page.fill("#note", BIG)
            page.click("#parseBtn")
            page.wait_for_selector("#items li")
            page.evaluate("window.scrollTo(0,0)")
            time.sleep(0.4)
            shot("02-parsed-list.png", full=True)

            page.click("#runBtn")
            page.wait_for_selector(".order")
            time.sleep(0.6)
            page.evaluate("window.scrollTo(0,0)")
            shot("03-agent-result.png", full=True)
            page.locator("details.trace").scroll_into_view_if_needed()
            time.sleep(0.3)
            shot("04-agent-trace.png")

            page.click("[data-tab=approvals]")
            page.wait_for_selector("#approvalList .order")
            time.sleep(0.3)
            shot("05-approval.png", full=True)

            page.click("#approvalList [data-approve]")
            page.wait_for_selector("#approvalList .empty")
            page.click("[data-tab=ledger]")
            page.wait_for_selector("#ledgerList .order")
            time.sleep(0.4)
            shot("06-ledger-insights.png", full=True)

            # over-budget scenario to show a hard block
            httpx.put(URL + "/api/policy", json={"weekly_budget": 2500})
            page.click("[data-tab=restock]")
            page.fill("#note", "mealie meal 6, oil 10, rice 8, eggs 3")
            page.click("#parseBtn")
            page.wait_for_selector("#items li")
            page.click("#runBtn")
            page.wait_for_selector("#result .order")
            time.sleep(0.5)
            page.evaluate("window.scrollTo(0,0)")
            shot("07-budget-guardrail.png", full=True)

            page.click("[data-tab=settings]")
            time.sleep(0.4)
            shot("08-guardrails-settings.png", full=True)

            # photo path (mock vision)
            page.click("[data-tab=restock]")
            img = Path(tempfile.mkdtemp()) / "shelf.jpg"
            img.write_bytes(b"\xff\xd8\xff\xe0fake-shelf-photo")
            page.set_input_files("#photo", str(img))
            page.wait_for_selector("#parsedCard:not(.hidden) #items li")
            time.sleep(0.4)
            page.locator("#parsedCard").scroll_into_view_if_needed()
            shot("09-shelf-photo-parse.png")
            b.close()
        print("saved:", *sorted(str(p) for p in OUT.glob("*.png")), sep="\n  ")
    finally:
        srv.terminate()
        srv.wait(timeout=10)


if __name__ == "__main__":
    main()
