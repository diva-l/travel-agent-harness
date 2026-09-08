from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_env_file(path: str | Path | None) -> None:
    """Load a simple KEY=VALUE file without overwriting process environment."""
    if not path:
        return
    env_path = Path(path).expanduser()
    if not env_path.is_file():
        raise FileNotFoundError(f"env file not found: {env_path}")
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


# Planner backend presets. Explicit env vars always win over preset values;
# the mode only fills in what the user did not set.
PLANNER_PRESETS: dict[str, dict[str, str]] = {
    "api": {},
    "vllm": {
        # vLLM serves an OpenAI-compatible endpoint on the same host; without
        # --api-key it accepts any bearer token, hence the placeholder key.
        "api_key": "vllm-local",
        "base_url": "http://127.0.0.1:8000/v1",
        "model": "travel-planner",
        "model_protocol": "tagged",
        "max_seconds": "600",
        # Self-hosted planner: tokens cost nothing, so this cumulative budget
        # is NOT cost control — it is only a runaway-loop guardrail. The real
        # training-side constraint (travel_agentic_rl/train_rl.sh) is the
        # per-call context window MAX_LENGTH=50000, enforced by the vLLM
        # server itself via --max-model-len 50000. 13 rounds of contexts
        # capped at 50k sum to well under 300k, so this never cuts a
        # training-plausible trajectory.
        "max_total_tokens": "300000",
        "model_temperature": "0.2",
        "model_top_p": "0.95",
        "model_top_k": "50",
        "model_max_output_tokens": "5000",
    },
}


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-pro"
    model_protocol: str = "native"
    db_path: Path = Path("travel_harness.db")
    request_timeout_seconds: float = 90.0
    max_steps: int = 13
    max_seconds: float = 120.0
    max_total_tokens: int = 24_000
    max_tool_calls: int = 40
    model_retries: int = 2
    repeat_call_limit: int = 3
    max_tool_output_chars: int = 6_000
    report_enabled: bool = True
    report_api_key: str = ""
    report_base_url: str = ""
    report_model: str = ""
    tool_provider: str = "demo"
    amap_api_key: str = ""
    amap_timeout_seconds: float = 15.0
    search_provider: str = "demo"
    firecrawl_api_key: str = ""
    firecrawl_timeout_seconds: float = 30.0
    model_temperature: float = 0.1
    model_top_p: float = 1.0
    model_top_k: int = 0
    model_max_output_tokens: int = 1800
    planner_mode: str = "api"
    require_tool_before_final: bool = True
    trace_payloads: bool = True
    parallel_tool_calls: bool = True
    # Concurrent task executions on the HTTP/API path (TaskService pool).
    # Each task holds one worker thread for its whole agent loop, so this is
    # the "how many users can run at once" knob for the web service.
    api_workers: int = 2
    # Bounded submission queue (backpressure): tasks beyond
    # api_workers + api_queue_size are rejected with 503 instead of queueing
    # silently forever.
    api_queue_size: int = 32
    # Outbound tool HTTP: pooled connections (keep-alive) whose maxsize doubles
    # as a client-side concurrency limit toward rate-limited upstreams, with
    # retry+backoff on transient errors; optional TTL response cache (0 = off,
    # keep eval trajectories deterministic unless explicitly enabled).
    tool_http_pool_size: int = 8
    tool_http_retries: int = 2
    tool_cache_ttl_seconds: float = 0.0
    # Training-environment alignment (travel_agentic_rl/run_tool_loop_infer.py).
    # The RL planner was trained/evaluated under these terminal rules, so the
    # harness only measures the trained policy fairly when they are enabled.
    # force_answer_after_steps=12 mirrors --force_answer_after_turns 12: after
    # that step the user message "信息已经足够…" is injected once per step.
    force_answer_after_steps: int = 0
    # repeat_answer_chance mirrors the training loop's repeated-call handler:
    # the first repeat-block per task injects the "最后一次机会" forced-answer
    # message instead of silently looping on blocked calls.
    repeat_answer_chance: bool = False
    # visit_extractor mirrors tools/tool_visit.py: page markdown is distilled
    # by an LLM extractor (EXTRACTOR_PROMPT) before reaching the planner,
    # instead of raw markdown. Uses the report-model credentials.
    visit_extractor: bool = False
    # ticket_simulator mirrors tools/tool_train_ticket.py / tool_transport.py:
    # train/flight results are LLM-simulated (deepseek-v4-flash with the
    # training prompts) instead of static demo fixtures.
    ticket_simulator: bool = False
    # training_tool_format makes search/visit return the training-side plain
    # text layouts ("A web search for ... returned:" / extractor JSON string)
    # inside the envelope's "text" field, which the tagged renderer prefers.
    training_tool_format: bool = False
    # current_date overrides the system prompt's 当前日期 (training-side test
    # inference used the dataset's fixed date, e.g. 2026-04-15). Empty = today.
    current_date: str = ""

    @classmethod
    def from_env(cls, **overrides: object) -> "HarnessConfig":
        planner_mode = str(
            overrides.get("planner_mode")
            or _first_env("TRAVEL_HARNESS_PLANNER_MODE", default="api")
        ).lower()
        preset = PLANNER_PRESETS.get(planner_mode, {})
        values: dict[str, object] = {
            "api_key": _first_env(
                "TRAVEL_HARNESS_API_KEY",
                "AGENT_API_KEY",
                "OPENAI_API_KEY",
                default=preset.get("api_key", ""),
            ),
            "base_url": _first_env(
                "TRAVEL_HARNESS_BASE_URL",
                "AGENT_BASE_URL",
                "OPENAI_BASE_URL",
                default=preset.get("base_url", "https://api.deepseek.com"),
            ),
            "model": _first_env(
                "TRAVEL_HARNESS_MODEL",
                "AGENT_MODEL_ID",
                "LLM_MODEL_ID",
                default=preset.get("model", "deepseek-v4-pro"),
            ),
            "model_protocol": _first_env(
                "TRAVEL_HARNESS_MODEL_PROTOCOL",
                default=preset.get("model_protocol", "native"),
            ),
            "db_path": Path(
                _first_env("TRAVEL_HARNESS_DB", default="travel_harness.db")
            ),
            "request_timeout_seconds": float(
                _first_env("TRAVEL_HARNESS_TIMEOUT", default="90")
            ),
            "max_steps": int(_first_env("TRAVEL_HARNESS_MAX_STEPS", default="13")),
            "max_seconds": float(
                _first_env("TRAVEL_HARNESS_MAX_SECONDS", default=preset.get("max_seconds", "120"))
            ),
            "max_total_tokens": int(
                _first_env("TRAVEL_HARNESS_MAX_TOTAL_TOKENS", default=preset.get("max_total_tokens", "24000"))
            ),
            "max_tool_calls": int(
                _first_env("TRAVEL_HARNESS_MAX_TOOL_CALLS", default="40")
            ),
            "model_retries": int(
                _first_env("TRAVEL_HARNESS_MODEL_RETRIES", default="2")
            ),
            "repeat_call_limit": int(
                _first_env("TRAVEL_HARNESS_REPEAT_CALL_LIMIT", default="3")
            ),
            "max_tool_output_chars": int(
                _first_env("TRAVEL_HARNESS_MAX_TOOL_OUTPUT_CHARS", default="6000")
            ),
            "report_enabled": _first_env(
                "TRAVEL_HARNESS_REPORT_ENABLED", default="true"
            ).lower() not in {"0", "false", "no"},
            "tool_provider": _first_env("TRAVEL_HARNESS_TOOL_PROVIDER", default="demo"),
            "amap_api_key": _first_env("TRAVEL_HARNESS_AMAP_KEY"),
            "amap_timeout_seconds": float(
                _first_env("TRAVEL_HARNESS_AMAP_TIMEOUT", default="15")
            ),
            "search_provider": _first_env("TRAVEL_HARNESS_SEARCH_PROVIDER", default="demo"),
            "firecrawl_api_key": _first_env("TRAVEL_HARNESS_FIRECRAWL_KEY"),
            "firecrawl_timeout_seconds": float(
                _first_env("TRAVEL_HARNESS_FIRECRAWL_TIMEOUT", default="30")
            ),
            "model_temperature": float(
                _first_env(
                    "TRAVEL_HARNESS_MODEL_TEMPERATURE",
                    default=preset.get("model_temperature", "0.1"),
                )
            ),
            "model_top_p": float(
                _first_env("TRAVEL_HARNESS_MODEL_TOP_P", default=preset.get("model_top_p", "1.0"))
            ),
            "model_top_k": int(
                _first_env("TRAVEL_HARNESS_MODEL_TOP_K", default=preset.get("model_top_k", "0"))
            ),
            "model_max_output_tokens": int(
                _first_env(
                    "TRAVEL_HARNESS_MODEL_MAX_OUTPUT_TOKENS",
                    default=preset.get("model_max_output_tokens", "1800"),
                )
            ),
            "planner_mode": planner_mode,
            "parallel_tool_calls": _first_env(
                "TRAVEL_HARNESS_PARALLEL_TOOL_CALLS", default="true"
            ).lower() not in {"0", "false", "no"},
            "api_workers": int(_first_env("TRAVEL_HARNESS_API_WORKERS", default="2")),
            "api_queue_size": int(_first_env("TRAVEL_HARNESS_API_QUEUE", default="32")),
            "tool_http_pool_size": int(
                _first_env("TRAVEL_HARNESS_TOOL_HTTP_POOL", default="8")),
            "tool_http_retries": int(
                _first_env("TRAVEL_HARNESS_TOOL_HTTP_RETRIES", default="2")),
            "tool_cache_ttl_seconds": float(
                _first_env("TRAVEL_HARNESS_TOOL_CACHE_TTL", default="0")),
            "force_answer_after_steps": int(
                _first_env("TRAVEL_HARNESS_FORCE_ANSWER_AFTER_STEPS", default="0")),
            "repeat_answer_chance": _first_env(
                "TRAVEL_HARNESS_REPEAT_ANSWER_CHANCE", default="false"
            ).lower() not in {"0", "false", "no"},
            "visit_extractor": _first_env(
                "TRAVEL_HARNESS_VISIT_EXTRACTOR", default="false"
            ).lower() not in {"0", "false", "no"},
            "ticket_simulator": _first_env(
                "TRAVEL_HARNESS_TICKET_SIMULATOR", default="false"
            ).lower() not in {"0", "false", "no"},
            "training_tool_format": _first_env(
                "TRAVEL_HARNESS_TRAINING_TOOL_FORMAT", default="false"
            ).lower() not in {"0", "false", "no"},
            "current_date": _first_env("TRAVEL_HARNESS_CURRENT_DATE", default=""),
        }
        report_env = {
            "report_api_key": _first_env("TRAVEL_HARNESS_REPORT_API_KEY"),
            "report_base_url": _first_env("TRAVEL_HARNESS_REPORT_BASE_URL"),
            "report_model": _first_env("TRAVEL_HARNESS_REPORT_MODEL"),
        }
        if planner_mode == "vllm":
            # The local planner is not the report writer: default the report
            # stage to the hosted API instead of inheriting planner settings.
            report_env["report_base_url"] = report_env["report_base_url"] or "https://api.deepseek.com"
            report_env["report_model"] = report_env["report_model"] or "deepseek-v4-flash"
        values.update({key: value for key, value in overrides.items() if value is not None})
        for key, planner_key in {
            "report_api_key": "api_key",
            "report_base_url": "base_url",
            "report_model": "model",
        }.items():
            if key not in overrides or overrides[key] is None:
                if planner_mode == "vllm" and key == "report_api_key":
                    # The planner key belongs to the local vLLM server; the
                    # hosted report model needs its own explicit credential.
                    values[key] = report_env[key]
                else:
                    values[key] = report_env[key] or values[planner_key]
        return cls(**values)

    def validate(self) -> None:
        if not self.api_key:
            raise ValueError(
                "missing API key; set TRAVEL_HARNESS_API_KEY or pass an env file"
            )
        if self.max_steps < 1 or self.max_tool_calls < 1:
            raise ValueError("step and tool-call budgets must be positive")
        if self.model_protocol not in {"native", "tagged"}:
            raise ValueError("model protocol must be native or tagged")
        if self.planner_mode not in PLANNER_PRESETS:
            raise ValueError("planner mode must be one of: " + ", ".join(sorted(PLANNER_PRESETS)))
        if (
            self.planner_mode == "vllm"
            and self.report_enabled
            and not self.report_api_key
        ):
            raise ValueError(
                "vllm mode: the report stage runs on the hosted API; "
                "set TRAVEL_HARNESS_REPORT_API_KEY or TRAVEL_HARNESS_REPORT_ENABLED=false"
            )
        if self.max_tool_output_chars < 500:
            raise ValueError("max tool output must be at least 500 characters")
        if self.report_enabled and not (self.report_api_key or self.api_key):
            raise ValueError("report model is enabled but has no API key")
        if self.tool_provider not in {"demo", "amap"}:
            raise ValueError("tool provider must be demo or amap")
        if self.tool_provider == "amap" and not self.amap_api_key:
            raise ValueError("amap tool provider requires TRAVEL_HARNESS_AMAP_KEY")
        if self.search_provider not in {"demo", "firecrawl"}:
            raise ValueError("search provider must be demo or firecrawl")
        if self.search_provider == "firecrawl" and not self.firecrawl_api_key:
            raise ValueError("firecrawl search provider requires TRAVEL_HARNESS_FIRECRAWL_KEY")
        if self.model_temperature < 0:
            raise ValueError("model temperature must be non-negative")
        if not 0 < self.model_top_p <= 1:
            raise ValueError("model top_p must be in (0, 1]")
        if self.model_top_k < 0:
            raise ValueError("model top_k must be non-negative (0 = server default)")
        if self.model_max_output_tokens < 256:
            raise ValueError("model max output tokens must be at least 256")
        if self.force_answer_after_steps < 0:
            raise ValueError("force answer step must be non-negative (0 = disabled)")
        if self.visit_extractor and not (self.report_api_key or self.api_key):
            raise ValueError("visit extractor requires an LLM credential (report or planner key)")
        if self.ticket_simulator and not (self.report_api_key or self.api_key):
            raise ValueError("ticket simulator requires an LLM credential (report or planner key)")
