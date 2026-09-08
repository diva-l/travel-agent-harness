"""Goal-conditioned page extractor, mirroring the RL training visit tool.

travel_agentic_rl/tools/tool_visit.py never feeds raw page markdown to the
planner: it runs an LLM extractor over the scraped page with EXTRACTOR_PROMPT
and returns the resulting {"rational", "evidence", "summary"} JSON string.
The trained policy was optimized against those distilled observations, so an
evaluation that feeds raw markdown instead is out-of-distribution. This module
reproduces that extraction stage behind the same OpenAI-compatible chat API.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

# Verbatim copy of travel_agentic_rl/prompt.py EXTRACTOR_PROMPT.
EXTRACTOR_PROMPT = """请处理以下网页内容和用户目标，以提取相关信息：

## **网页内容**
{webpage_content}

## **用户目标**
{goal}

## **任务指南**
1. **内容扫描以寻找合理性**：在网页内容中查找与用户目标直接相关的**特定部分/数据**。
2. **关键信息提取以寻找证据**：从内容中识别并提取**最相关的信息**，确保不遗漏任何重要信息，并尽可能输出内容的**完整原始上下文**，可以包含三个以上的段落。
3. **摘要输出以进行总结**：将信息组织成简洁明了、逻辑清晰的段落，并优先考虑信息的清晰度，同时判断信息对目标的贡献。

**最终输出格式为JSON格式，包含“rational”、“evidence”和“summary”字段。**
"""

# Cap the page text sent to the extractor: the training side truncates to
# ~100k tokens, but observed Firecrawl pages are far smaller; 30k chars keeps
# extractor latency/cost bounded while covering real pages.
EXTRACTOR_INPUT_MAX_CHARS = 30_000


@dataclass(slots=True)
class VisitExtractor:
    """Callable (markdown, goal) -> extracted JSON string; '' on failure."""

    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 60.0

    def __call__(self, markdown: str, goal: str) -> str:
        prompt = EXTRACTOR_PROMPT.format(
            webpage_content=markdown[:EXTRACTOR_INPUT_MAX_CHARS],
            goal=goal,
        )
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
        }
        if "deepseek.com" in self.base_url:
            payload["thinking"] = {"type": "disabled"}
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
            content = str(body["choices"][0]["message"]["content"] or "").strip()
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, IndexError, TypeError):
            return ""
        if not content:
            return ""
        # Training side normalizes to the outermost JSON object when the model
        # wraps it in prose; the planner only needs the JSON payload.
        try:
            json.loads(content)
        except json.JSONDecodeError:
            left, right = content.find("{"), content.rfind("}")
            if left == -1 or right == -1 or left > right:
                return ""
            content = content[left : right + 1]
        return content
