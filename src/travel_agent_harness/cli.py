from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .config import HarnessConfig, load_env_file
from .evaluation import load_cases, run_evaluation
from .harness import build_default_harness


def _state_summary(state) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "task_id": state.task_id,
        "status": state.status.value,
        "step": state.step,
        "checkpoint_seq": state.checkpoint_seq,
        "tool_calls": state.tool_calls,
        "successful_tool_calls": state.successful_tool_calls,
        "tool_errors": state.tool_errors,
        "total_tokens": state.total_tokens,
        "elapsed_seconds": round(state.elapsed_seconds, 3),
        "model_fingerprint": state.model_fingerprint,
    }
    if state.pending_call:
        summary["pending_call"] = state.pending_call
    if state.answer:
        summary["answer"] = state.answer
    if state.error:
        summary["error"] = state.error
    if state.forked_from:
        summary["forked_from"] = state.forked_from
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded and traceable travel Agent Harness")
    parser.add_argument("--env-file", help="path to a local .env file")
    parser.add_argument("--db", help="SQLite state/trace database")
    parser.add_argument("--base-url", help="OpenAI-compatible API base URL")
    parser.add_argument("--model", help="served model name")
    parser.add_argument(
        "--protocol", choices=["native", "tagged"], help="model tool-call protocol"
    )
    parser.add_argument(
        "--planner-mode",
        choices=["api", "vllm"],
        help="planner backend preset (api = hosted API, vllm = local vLLM server)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="start and run a new task")
    run_parser.add_argument("objective")

    resume_parser = subparsers.add_parser("resume", help="resume a persisted task")
    resume_parser.add_argument("task_id")

    for command in ("approve", "reject"):
        decision_parser = subparsers.add_parser(command, help=f"{command} a pending tool call")
        decision_parser.add_argument("task_id")
        decision_parser.add_argument("call_id")

    trace_parser = subparsers.add_parser("trace", help="print a task trace")
    trace_parser.add_argument("task_id")

    checkpoints_parser = subparsers.add_parser("checkpoints", help="list task checkpoints")
    checkpoints_parser.add_argument("task_id")

    fork_parser = subparsers.add_parser("fork", help="fork a task from a checkpoint")
    fork_parser.add_argument("task_id")
    fork_parser.add_argument("--checkpoint", type=int, required=True)
    fork_parser.add_argument("--run", action="store_true", help="immediately run the fork")

    eval_parser = subparsers.add_parser("eval", help="run a fixed evaluation set")
    eval_parser.add_argument(
        "--cases",
        default=str(Path(__file__).resolve().parents[2] / "evals" / "cases.jsonl"),
    )
    setup_parser = subparsers.add_parser(
        "setup", help="interactive first-run wizard: detect OS, pick a planner mode, write .env"
    )
    setup_parser.add_argument("--output", default=".env", help="env file to write")
    serve_parser = subparsers.add_parser("serve", help="run the Travel Agent web application")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    if args.command == "setup":
        # Runs before any config validation: the whole point is to create a
        # valid .env for someone who does not have one yet.
        from .setup_wizard import run_setup

        run_setup(args.output)
        return
    load_env_file(args.env_file)
    overrides: dict[str, object] = {}
    if args.db:
        overrides["db_path"] = Path(args.db)
    if args.base_url:
        overrides["base_url"] = args.base_url
    if args.model:
        overrides["model"] = args.model
    if args.protocol:
        overrides["model_protocol"] = args.protocol
    if args.planner_mode:
        overrides["planner_mode"] = args.planner_mode
    config = HarnessConfig.from_env(**overrides)
    harness = build_default_harness(config)

    if args.command == "serve":
        import uvicorn

        from .api.app import create_app

        uvicorn.run(create_app(config, harness), host=args.host, port=args.port)
        return

    if args.command == "run":
        result = _state_summary(harness.run(args.objective))
    elif args.command == "resume":
        result = _state_summary(harness.resume(args.task_id))
    elif args.command == "approve":
        result = _state_summary(harness.approve(args.task_id, args.call_id))
    elif args.command == "reject":
        result = _state_summary(harness.reject(args.task_id, args.call_id))
    elif args.command == "trace":
        result = {"task_id": args.task_id, "events": harness.runtime.store.trace(args.task_id)}
    elif args.command == "checkpoints":
        result = {
            "task_id": args.task_id,
            "checkpoints": harness.runtime.store.list_checkpoints(args.task_id),
        }
    elif args.command == "fork":
        state = harness.fork(args.task_id, args.checkpoint)
        if args.run:
            state = harness.runtime.run(state)
        result = _state_summary(state)
    elif args.command == "eval":
        result = run_evaluation(harness, load_cases(args.cases))
    else:
        raise AssertionError(f"unsupported command: {args.command}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
