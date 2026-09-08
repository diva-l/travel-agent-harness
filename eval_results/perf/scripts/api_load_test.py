# -*- coding: utf-8 -*-
"""Real HTTP-path multi-user concurrency test.

One user = one task = one POST /api/plans against the RUNNING web service,
so this exercises the full product path: FastAPI -> TaskService
ThreadPoolExecutor (TRAVEL_HARNESS_API_WORKERS) -> AgentRuntime -> vLLM ->
external tool APIs. load_test.py bypassed the API layer; this script does not.

Phases:
  --phase baseline : N=8 against the current deployment (proves the
                     configured worker cap by queue-wait times)
  --phase sweep    : N=4/8/12/16 after raising the worker cap, to find the
                     throughput knee

Outputs perf/api_load_<phase>.json and GPU samples appended to
perf/api_gpu_samples.jsonl.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "load_test", Path(__file__).resolve().with_name("load_test.py"))
lt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lt)

OUT = Path(__file__).resolve().parents[1] / "data"
API = "http://127.0.0.1:8765"
TERMINAL = {"completed", "exhausted", "failed"}

# 10 distinct "users": realistic structured trip-plan requests.
USERS = [
    ("上海", "杭州", 3, 4000, "relaxed", ["西湖", "茶文化"]),
    ("北京", "西安", 4, 6000, "balanced", ["历史古迹", "美食"]),
    ("广州", "成都", 5, 7000, "balanced", ["熊猫", "火锅"]),
    ("深圳", "厦门", 2, 3000, "intensive", ["海岛", "骑行"]),
    ("成都", "丽江", 4, 5500, "relaxed", ["古城", "雪山"]),
    ("杭州", "青岛", 3, 4500, "balanced", ["海滨", "啤酒"]),
    ("南京", "重庆", 4, 5000, "intensive", ["山城", "夜景"]),
    ("武汉", "桂林", 3, 4000, "relaxed", ["山水", "漂流"]),
    ("西安", "厦门", 5, 8000, "balanced", ["鼓浪屿", "海鲜"]),
    ("苏州", "长沙", 3, 4500, "balanced", ["美食", "博物馆"]),
]


def build_request(i: int) -> dict:
    origin, dest, days, budget, pace, prefs = USERS[i % len(USERS)]
    return {
        "origin": origin,
        "destination": dest,
        "start_date": (date.today() + timedelta(days=7 + i)).isoformat(),
        "days": days,
        "budget_cny": budget,
        "travelers": 2,
        "pace": pace,
        "preferences": prefs,
        "notes": "",
    }


def post_json(url: str, payload: dict) -> tuple[dict, float]:
    t0 = time.perf_counter()
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read()), time.perf_counter() - t0


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


def run_tier(n_users: int, poll_interval: float = 1.0, tier_timeout: float = 1200.0) -> dict:
    print(f"\n=== tier users={n_users} ===", flush=True)
    metrics_before = lt.vllm_metrics()

    # All users submit at (approximately) the same instant.
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=n_users) as pool:
        submitted = list(pool.map(
            lambda i: (i, *post_json(f"{API}/api/plans", build_request(i))),
            range(n_users)))

    tasks = {}
    for i, resp, submit_latency in submitted:
        tasks[resp["task_id"]] = {
            "user": i,
            "submit_latency_s": round(submit_latency, 3),
            "submitted_at": t0,
            "first_running_s": None,
            "terminal_s": None,
            "status": None,
        }

    deadline = t0 + tier_timeout
    pending = set(tasks)
    while pending and time.monotonic() < deadline:
        time.sleep(poll_interval)
        now = time.monotonic()
        for task_id in list(pending):
            view = get_json(f"{API}/api/plans/{task_id}")
            rec = tasks[task_id]
            if rec["first_running_s"] is None and view["status"] not in {"created"}:
                rec["first_running_s"] = round(now - t0, 2)
            if view["status"] in TERMINAL:
                rec["status"] = view["status"]
                rec["terminal_s"] = round(now - t0, 2)
                rec["metrics"] = view["metrics"]
                pending.discard(task_id)
                print(f"  user{rec['user']:>2} {view['status']:<10} "
                      f"t+{rec['terminal_s']:>7.1f}s steps={view['metrics']['step']}", flush=True)

    wall = time.monotonic() - t0
    for task_id in pending:  # timed out
        tasks[task_id]["status"] = "timeout"

    metrics_after = lt.vllm_metrics()
    dp = metrics_after.get("prompt_tokens", 0) - metrics_before.get("prompt_tokens", 0)
    dg = metrics_after.get("generation_tokens", 0) - metrics_before.get("generation_tokens", 0)

    done = [r for r in tasks.values() if r["terminal_s"] is not None]
    queue_waits = [r["first_running_s"] for r in done if r["first_running_s"] is not None]
    statuses: dict[str, int] = {}
    for r in tasks.values():
        statuses[r["status"] or "unknown"] = statuses.get(r["status"] or "unknown", 0) + 1

    result = {
        "users": n_users,
        "wall_seconds": round(wall, 1),
        "throughput_tasks_per_hour": round(len(done) / wall * 3600, 1) if wall else 0,
        "statuses": statuses,
        "queue_wait_seconds": {
            "mean": round(sum(queue_waits) / len(queue_waits), 2) if queue_waits else None,
            "max": max(queue_waits) if queue_waits else None,
        },
        "task_latency_seconds": {
            "p50": sorted(r["terminal_s"] for r in done)[len(done) // 2] if done else None,
            "max": max((r["terminal_s"] for r in done), default=None),
        },
        "vllm_tokens_per_sec": {
            "prompt": round(dp / wall, 1) if wall else 0,
            "decode": round(dg / wall, 1) if wall else 0,
        },
        "submit_latency_max_s": max(r["submit_latency_s"] for r in tasks.values()),
        "tasks": sorted(tasks.values(), key=lambda r: r["user"]),
    }
    print(json.dumps({k: v for k, v in result.items() if k != "tasks"},
                     ensure_ascii=False, indent=1), flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["baseline", "sweep"], required=True)
    parser.add_argument("--tiers", type=int, nargs="*", default=None,
                        help="override tier list, e.g. --tiers 24 32")
    args = parser.parse_args()

    default_tiers = [8] if args.phase == "baseline" else [4, 8, 12, 16]
    tiers = args.tiers or default_tiers
    sampler = threading.Thread(target=lt.gpu_sampler, daemon=True)
    sampler.start()

    results = []
    for n in tiers:
        lt._current_tier["name"] = f"api-{args.phase}-u{n}"
        results.append(run_tier(n))

    lt._gpu_stop.set()
    with open(OUT / "api_gpu_samples.jsonl", "a", encoding="utf-8") as fh:
        for sample in lt._gpu_samples:
            fh.write(json.dumps(sample) + "\n")
    out_path = OUT / f"api_load_{args.phase}.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {out_path}", flush=True)


if __name__ == "__main__":
    main()
