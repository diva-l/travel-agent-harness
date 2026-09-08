from __future__ import annotations

from .config import HarnessConfig
from .llm import ModelBackend, OpenAICompatibleModel
from .protocol import TaggedOpenAICompatibleModel
from .reporting import OpenAICompatibleReportModel, ReportBackend
from .runtime import AgentRuntime
from .store import SQLiteStore
from .tools import ToolRegistry, build_travel_registry


class TravelHarness:
    def __init__(self, runtime: AgentRuntime, reporter: ReportBackend | None = None) -> None:
        self.runtime = runtime
        self.reporter = reporter

    def run(self, objective: str):
        return self.runtime.run(self.runtime.create(objective))

    def resume(self, task_id: str):
        return self.runtime.resume(task_id)

    def approve(self, task_id: str, call_id: str):
        return self.runtime.decide_approval(task_id, call_id, approved=True)

    def reject(self, task_id: str, call_id: str):
        return self.runtime.decide_approval(task_id, call_id, approved=False)

    def fork(self, task_id: str, checkpoint_seq: int):
        return self.runtime.fork(task_id, checkpoint_seq)


def build_default_harness(
    config: HarnessConfig,
    *,
    model: ModelBackend | None = None,
    tools: ToolRegistry | None = None,
    reporter: ReportBackend | None = None,
) -> TravelHarness:
    config.validate()
    if model is not None:
        selected_model = model
    elif config.model_protocol == "tagged":
        selected_model = TaggedOpenAICompatibleModel(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
            timeout_seconds=config.request_timeout_seconds,
            max_output_tokens=config.model_max_output_tokens,
            temperature=config.model_temperature,
            top_p=config.model_top_p,
            top_k=config.model_top_k,
            tool_output_max_chars=config.max_tool_output_chars,
        )
    else:
        selected_model = OpenAICompatibleModel(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
            timeout_seconds=config.request_timeout_seconds,
            max_output_tokens=config.model_max_output_tokens,
            temperature=config.model_temperature,
            top_p=config.model_top_p,
            top_k=config.model_top_k,
        )
    extractor = None
    if config.visit_extractor:
        # Training-side visit tool parity: distill pages with the report model
        # (EXTRACTOR_PROMPT) instead of feeding raw markdown to the planner.
        from .extractor import VisitExtractor

        extractor = VisitExtractor(
            api_key=config.report_api_key or config.api_key,
            base_url=config.report_base_url or config.base_url,
            model=config.report_model or config.model,
            timeout_seconds=config.request_timeout_seconds,
        )
    simulator = None
    if config.ticket_simulator:
        # Training parity: train/flight results are LLM-simulated with the
        # training prompts (travel_agentic_rl/tools/tool_train_ticket.py).
        from .simulator import TicketSimulator

        simulator = TicketSimulator(
            api_key=config.report_api_key or config.api_key,
            base_url=config.report_base_url or config.base_url,
            model=config.report_model or config.model,
            timeout_seconds=config.request_timeout_seconds,
        )
    selected_tools = tools or build_travel_registry(
        provider=config.tool_provider,
        search_provider=config.search_provider,
        amap_key=config.amap_api_key,
        amap_timeout=config.amap_timeout_seconds,
        firecrawl_key=config.firecrawl_api_key,
        firecrawl_timeout=config.firecrawl_timeout_seconds,
        tool_http_pool_size=config.tool_http_pool_size,
        tool_http_retries=config.tool_http_retries,
        tool_cache_ttl=config.tool_cache_ttl_seconds,
        visit_extractor=extractor,
        legacy_text=config.training_tool_format,
        ticket_simulator=simulator,
    )
    store = SQLiteStore(config.db_path)
    selected_reporter = reporter
    if selected_reporter is None and config.report_enabled:
        selected_reporter = OpenAICompatibleReportModel(
            api_key=config.report_api_key or config.api_key,
            base_url=config.report_base_url or config.base_url,
            model=config.report_model or config.model,
            timeout_seconds=config.request_timeout_seconds,
        )
    return TravelHarness(
        AgentRuntime(config, selected_model, selected_tools, store),
        reporter=selected_reporter,
    )
