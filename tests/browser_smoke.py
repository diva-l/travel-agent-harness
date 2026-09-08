from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


root = Path(__file__).resolve().parents[1]
artifacts = root / "artifacts"
artifacts.mkdir(exist_ok=True)
console_errors: list[str] = []
existing_task_id = os.getenv("TRAVEL_HARNESS_EXISTING_TASK", "").strip()
port = int(os.getenv("TRAVEL_HARNESS_PORT", "8765"))

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(
        headless=True,
        executable_path=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    )
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    url = (
        f"http://127.0.0.1:{port}/?task={existing_task_id}"
        if existing_task_id
        else f"http://127.0.0.1:{port}"
    )
    page.goto(url, wait_until="networkidle")
    page.locator("#healthText").filter(has_not_text="连接运行时").wait_for(timeout=10_000)
    if not existing_task_id:
        page.screenshot(path=artifacts / "ui-intake.png", full_page=True)
        page.get_by_role("button", name="开始规划").click()
    else:
        # 恢复已完成任务时主屏是行程视图，先切到监控视图
        page.get_by_role("button", name="运行过程").click()
    page.get_by_text("LIVE RUNTIME MANIFEST").wait_for()
    page.locator("#toolCount").filter(has_text="8 CONTRACTS").wait_for()
    page.screenshot(path=artifacts / "ui-desktop.png", full_page=True)
    if existing_task_id:
        page.get_by_role("button", name="查看行程").click()
    page.locator("#statusBadge").filter(has_text="已完成").wait_for(timeout=90_000)
    page.locator("#journeyView").wait_for(state="visible")
    page.locator("#journeyView .stop-card").first.wait_for()
    stop_count = page.locator("#journeyView .stop-card").count()
    day_count = page.locator("#dayTabs button").count()
    # 切到第 2 天，验证按天筛选只展示该天的站点卡片
    second_day_button = page.locator("#dayTabs button").nth(2) if day_count > 2 else None
    filtered_stops = stop_count
    if second_day_button is not None:
        second_day_button.click()
        filtered_stops = page.locator("#journeyView .stop-card").count()
        page.locator("#dayTabs button").first.click()
    answer_length = len(page.locator("#answerText").text_content() or "")
    page.screenshot(path=artifacts / "ui-completed.png", full_page=True)

    # 行程是主视图；监控面板通过「运行过程」切回
    page.get_by_role("button", name="运行过程").click()
    page.locator("#evidenceGate.pass").wait_for()
    page.locator("#checkpointGate.pass").wait_for()
    page.locator("#stateRail [data-state='terminal'].active").wait_for()
    page.get_by_role("button", name="Checkpoints").click()
    page.locator("#checkpointList li").first.wait_for()
    checkpoint_count = page.locator("#checkpointList li").count()
    page.get_by_role("button", name="Trace").click()
    page.screenshot(path=artifacts / "ui-runtime.png", full_page=True)
    task_id = page.locator("#taskId").inner_text()
    trace_count = page.locator("#traceList li").count()

    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=artifacts / "ui-mobile.png", full_page=True)
    browser.close()

print(
    json.dumps(
        {
            "task": task_id,
            "trace_events_rendered": trace_count,
            "answer_chars": answer_length,
            "route_stops": stop_count,
            "day_filters": day_count,
            "filtered_stops_day2": filtered_stops,
            "checkpoints_rendered": checkpoint_count,
            "harness_manifest": True,
            "console_errors": console_errors,
            "screenshots": ["ui-desktop.png", "ui-completed.png", "ui-mobile.png"],
        },
        ensure_ascii=False,
    )
)
