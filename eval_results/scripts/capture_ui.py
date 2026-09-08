# -*- coding: utf-8 -*-
"""Capture frontend screenshots of a completed task (desktop + mobile)."""
from pathlib import Path
from playwright.sync_api import sync_playwright

TASK_ID = "fd9cc23ef4384e38aef2989146cae750"
BASE = f"http://127.0.0.1:8765/?task={TASK_ID}"
OUT = Path("/root/autodl-tmp/TravelAgentHarness/docs/screenshots")
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch()
    # Desktop
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_timeout(4000)
    page.screenshot(path=str(OUT / "desktop_full.png"), full_page=True)
    page.screenshot(path=str(OUT / "desktop_viewport.png"))
    # try to capture inspector panel after scrolling
    page.mouse.wheel(0, 1200)
    page.wait_for_timeout(800)
    page.screenshot(path=str(OUT / "desktop_scrolled.png"))
    page.close()
    # Mobile
    m = browser.new_page(viewport={"width": 390, "height": 844})
    m.goto(BASE, wait_until="networkidle")
    m.wait_for_timeout(4000)
    m.screenshot(path=str(OUT / "mobile_viewport.png"))
    m.screenshot(path=str(OUT / "mobile_full.png"), full_page=True)
    m.close()
    browser.close()
print("screenshots saved to", OUT)
