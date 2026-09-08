"""Firecrawl provider for the two web tools (``search`` and ``visit``).

Live web retrieval for evaluation runs, paired with the AMap provider so the
planner sees a fully real tool environment. Contracts stay identical to the
demo fixtures; every result carries ``data_source`` so the output guardrail
and the evidence boundary still apply.

Simplification vs. the RL training environment: by default ``visit`` returns
the scraped main-content markdown (truncated) directly. Pass ``extractor``
(see extractor.VisitExtractor) to reproduce the training-side LLM extraction
stage so the trained planner sees in-distribution observations.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable

from .http_client import PooledHttpClient, TTLCache
from .tools import ToolValidationError


BASE_URL = "https://api.firecrawl.dev"
SEARCH_RESULT_LIMIT = 5
PAGE_CONTENT_MAX_CHARS = 6000

Opener = Callable[..., Any]


def _default_opener(request: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


class FirecrawlProvider:
    """Small Firecrawl v1 HTTP client with an injectable opener for tests."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 30.0,
        opener: Opener | None = None,
        pool_size: int = 4,
        retries: int = 2,
        cache_ttl: float = 0.0,
        extractor: Callable[[str, str], str] | None = None,
        legacy_text: bool = False,
    ) -> None:
        if not api_key:
            raise ToolValidationError("firecrawl provider requires an API key")
        self._key = api_key
        self._timeout = timeout
        self._opener = opener or PooledHttpClient(maxsize=pool_size, retries=retries).open
        self._cache = TTLCache(cache_ttl)
        self._extractor = extractor
        self._legacy_text = legacy_text

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        cache_key = (path, data)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return json.loads(cached.decode("utf-8"))
        request = urllib.request.Request(
            f"{BASE_URL}{path}",
            data=data,
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            raw = self._opener(request, timeout=self._timeout)
            body = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ToolValidationError(f"firecrawl request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ToolValidationError("firecrawl returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise ToolValidationError("firecrawl returned an unexpected payload")
        if body.get("success") is False:
            raise ToolValidationError(f"firecrawl error: {body.get('error', 'unknown')}")
        if isinstance(raw, bytes):
            self._cache.put(cache_key, raw)
        return body

    # --- tool handlers -------------------------------------------------

    def search(self, query: list[str]) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for item in query:
            try:
                body = self._post("/v1/search", {"query": item, "limit": SEARCH_RESULT_LIMIT})
                data = body.get("data") or []
                if isinstance(data, dict):  # newer API nests web/news/images
                    data = data.get("web") or []
                results.append({
                    "query": item,
                    "items": [
                        {
                            "title": str(ref.get("title") or ""),
                            "url": str(ref.get("url") or ""),
                            "snippet": str(ref.get("description") or ""),
                        }
                        for ref in data[:SEARCH_RESULT_LIMIT]
                        if isinstance(ref, dict)
                    ],
                })
            except ToolValidationError as exc:
                results.append({"query": item, "items": [], "error": str(exc)})
        payload: dict[str, Any] = {
            "data_source": "firecrawl_search",
            "queries": query,
            "results": results,
            "notice": "网页检索结果来自 Firecrawl；价格、开放时间与预约规则请二次确认。",
        }
        if self._legacy_text:
            # Training-side layout (tools/tool_web_search.py): numbered
            # "[title](url)\nsnippet: ..." lines under a Web Results header.
            parts: list[str] = []
            for entry in results:
                q = entry["query"]
                if entry.get("error"):
                    parts.append(
                        f"Firecrawl search Timeout or error ({entry['error']}); "
                        "return None, Please try again later."
                    )
                    continue
                items = entry["items"]
                if not items:
                    parts.append(
                        f"No results found for query: '{q}'. Use a less specific query."
                    )
                    continue
                refs = "\n\n".join(
                    f"{i}. [{it['title']}]({it['url']})\nsnippet: {it['snippet']}"
                    for i, it in enumerate(items, start=1)
                )
                parts.append(f"A web search for '{q}' returned:\n\n## Web Results\n\n{refs}")
            payload["text"] = "\n".join(parts)
        return payload

    def visit(self, url: str | list[str], goal: str) -> dict[str, Any]:
        urls = [url] if isinstance(url, str) else url
        pages: list[dict[str, Any]] = []
        for item in urls:
            try:
                body = self._post(
                    "/v1/scrape",
                    {"url": item, "formats": ["markdown"], "onlyMainContent": True},
                )
                data = body.get("data") or {}
                markdown = str(data.get("markdown") or "").strip()
                if not markdown:
                    raise ToolValidationError("firecrawl returned no page content")
                summary = ""
                if self._extractor is not None:
                    try:
                        summary = self._extractor(markdown, goal)
                    except Exception:
                        summary = ""
                pages.append({"url": item, "summary": summary or markdown[:PAGE_CONTENT_MAX_CHARS]})
            except ToolValidationError as exc:
                pages.append({"url": item, "summary": "", "error": str(exc)})
        payload: dict[str, Any] = {
            "data_source": "firecrawl_scrape",
            "goal": goal,
            "pages": pages,
            "notice": (
                "网页正文由 Firecrawl 抓取并经 LLM 按目标提炼；价格、开放时间和交通状态请二次确认。"
                if self._extractor is not None
                else "网页正文由 Firecrawl 抽取（未做 LLM 提炼）；价格、开放时间和交通状态请二次确认。"
            ),
        }
        if self._legacy_text or self._extractor is not None:
            # Training-side visit returns the per-page texts joined by
            # "=======" (tool_visit.py), not a structured page list.
            payload["text"] = "\n=======\n".join(
                p["summary"] or f"Error fetching content {p['url']}: {p.get('error', '')}"
                for p in pages
            ).strip()
        return payload


def firecrawl_handlers(
    api_key: str,
    *,
    timeout: float = 30.0,
    opener: Opener | None = None,
    pool_size: int = 4,
    retries: int = 2,
    cache_ttl: float = 0.0,
    extractor: Callable[[str, str], str] | None = None,
    legacy_text: bool = False,
) -> dict[str, Any]:
    """Return handler overrides for the two web tools (contracts stay identical)."""
    provider = FirecrawlProvider(
        api_key, timeout=timeout, opener=opener,
        pool_size=pool_size, retries=retries, cache_ttl=cache_ttl,
        extractor=extractor, legacy_text=legacy_text,
    )
    return {
        "search": provider.search,
        "visit": provider.visit,
    }
