"""Interactive first-run setup wizard (`travel-harness setup`).

Detects the host OS, offers only the planner modes that can actually run
there (vLLM has no native Windows build, so Windows gets api mode only),
and writes a ready-to-use .env so a fresh clone never starts from a
misconfigured file.
"""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Callable


def run_setup(
    output: Path | str = ".env",
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
    system: str | None = None,
) -> Path:
    system = system or platform.system()
    output = Path(output)

    def clean(raw: str) -> str:
        # Windows PowerShell pipes text to native stdin with a BOM; interactive
        # typing never produces one, but scripted runs must not leak it into keys.
        return raw.strip().lstrip("﻿")

    def ask(prompt: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        answer = clean(input_fn(f"{prompt}{suffix}: "))
        return answer or default

    def ask_required(prompt: str) -> str:
        while True:
            answer = clean(input_fn(f"{prompt}: "))
            if answer:
                return answer
            print_fn("  此项必填，请重新输入。")

    print_fn("== TravelAgentHarness 装机向导 ==")
    print_fn(f"检测到操作系统：{system}")

    # 1. Planner mode — gated by what the OS can actually run.
    if system == "Linux":
        print_fn("本机支持两种 Planner 模式：")
        print_fn("  1) api  — 托管大模型 API（DeepSeek 等 OpenAI 兼容端点），无需 GPU")
        print_fn("  2) vllm — 本地 TravelPlanner-4B（需 NVIDIA GPU，且已启动 vLLM 服务）")
        choice = ask("请选择", "1")
        while choice not in {"1", "2", "api", "vllm"}:
            choice = ask("请输入 1 或 2", "1")
        planner_mode = "vllm" if choice in {"2", "vllm"} else "api"
    else:
        planner_mode = "api"
        print_fn(f"当前系统（{system}）仅支持 api 模式（托管大模型 API）。")
        print_fn("vllm 模式（本地 TravelPlanner-4B）依赖 Linux + NVIDIA GPU，本机不可用。")

    # 2. Model credentials.
    lines: list[str] = [
        f"# 由 travel-harness setup 生成（{system}），完整配置项说明见 .env.example",
        f"TRAVEL_HARNESS_PLANNER_MODE={planner_mode}",
    ]
    if planner_mode == "api":
        lines.append(f"TRAVEL_HARNESS_API_KEY={ask_required('DeepSeek API Key（sk-...）')}")
        lines.append(f"TRAVEL_HARNESS_BASE_URL={ask('API 端点', 'https://api.deepseek.com')}")
        lines.append(f"TRAVEL_HARNESS_MODEL={ask('模型名', 'deepseek-v4-pro')}")
    else:
        print_fn("vllm 模式的采样参数、端点与预算由预设自动填充，无需配置。")
        print_fn("Report 阶段默认走托管 API（deepseek-v4-flash），需要一个独立的 key。")
        lines.append(f"TRAVEL_HARNESS_REPORT_API_KEY={ask_required('Report API Key（sk-...）')}")

    # 3. Tool data sources.
    lines.append("")
    lines.append("# 工具数据源：demo 为离线演示数据，amap/firecrawl 为真实 API")
    tool_provider = ask("地理工具数据源（demo/amap）", "demo")
    while tool_provider not in {"demo", "amap"}:
        tool_provider = ask("请输入 demo 或 amap", "demo")
    lines.append(f"TRAVEL_HARNESS_TOOL_PROVIDER={tool_provider}")
    if tool_provider == "amap":
        lines.append(f"TRAVEL_HARNESS_AMAP_KEY={ask_required('高德 Web 服务 Key')}")
    search_provider = ask("网页检索数据源（demo/firecrawl）", "demo")
    while search_provider not in {"demo", "firecrawl"}:
        search_provider = ask("请输入 demo 或 firecrawl", "demo")
    lines.append(f"TRAVEL_HARNESS_SEARCH_PROVIDER={search_provider}")
    if search_provider == "firecrawl":
        lines.append(f"TRAVEL_HARNESS_FIRECRAWL_KEY={ask_required('Firecrawl API Key')}")

    # 4. Local state + optional API guard.
    lines.append("")
    lines.append(f"TRAVEL_HARNESS_DB={ask('SQLite 数据库路径', './travel_harness.db')}")
    api_token = ask("接口鉴权 Token（留空 = 本机开放 Demo，仅暴露公网时需要）")
    if api_token:
        lines.append(f"TRAVEL_HARNESS_API_TOKEN={api_token}")

    if output.exists():
        if ask(f"{output} 已存在，覆盖？", "n").lower() not in {"y", "yes"}:
            print_fn("已取消，未修改现有文件。")
            return output

    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print_fn(f"已写入 {output}")
    print_fn("下一步：travel-harness --env-file .env serve --host 127.0.0.1 --port 8765")
    return output
