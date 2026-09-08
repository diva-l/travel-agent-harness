"""Observation rendering ported from the RL training environment.

travel_agentic_rl/utils/markdown.py (json2md) and utils/text.py
(truncate_text) define how tool results looked to the planner during SFT/GRPO:
structured tool payloads were rendered as heading-style markdown, truncated
head+tail at a whitespace boundary. The harness stores provider-neutral JSON
envelopes for persistence/reporting; this module renders them back into the
training observation format at the tagged-protocol boundary.
"""

from __future__ import annotations

import re
from typing import Any


def json2md(json_block: Any, depth: int = 1, htag: str = "#") -> str:
    """Verbatim port of travel_agentic_rl/utils/markdown.py json2md."""
    markdown = ""

    def buildHeaderChain(depth: int, title: str) -> str:
        return "\n" + htag * (depth + 1) + f" {title}\n\n"

    def buildValueChain(key: Any, value: Any) -> str:
        return str(key) + f": {value}\n"

    def addHeader(value: str, depth: int) -> None:
        nonlocal markdown
        markdown += buildHeaderChain(depth, value.title())

    def addValue(key: Any, value: Any) -> None:
        nonlocal markdown
        markdown += buildValueChain(key, value)

    def parseDict(d: dict, depth: int) -> None:
        nonlocal markdown
        for k in d:
            if isinstance(d[k], (dict, list)):
                addHeader(k, depth)
                parseJSON(d[k], depth + 1)
            else:
                addValue(k, d[k])
        markdown += "\n"

    def parseList(items: list, depth: int) -> None:
        nonlocal markdown
        for i, value in enumerate(items):
            addHeader(str(i + 1), depth)
            if not isinstance(value, (dict, list)):
                addValue(items.index(value), value)
            else:
                parseDict(value, depth)
        markdown += "\n"

    def parseJSON(block: Any, depth: int) -> None:
        if isinstance(block, dict):
            parseDict(block, depth)
        if isinstance(block, list):
            parseList(block, depth)

    parseJSON(json_block, depth)
    return markdown.strip()


def truncate_text(text: str, max_len: int = 5000) -> str:
    """Verbatim port of travel_agentic_rl/utils/text.py truncate_text.

    Head+tail truncation at whitespace boundaries so both ends of the
    observation survive (the tail often carries the conclusion/pricing).
    """
    if len(text) <= max_len:
        return text

    head_len = max_len // 2
    tail_len = max_len // 2

    head_part = text[:head_len]
    head_matches = list(re.finditer(r"\s", head_part))
    if head_matches:
        head_end_index = head_matches[-1].start()
    else:
        head_end_index = head_len
    head = text[:head_end_index]

    tail_part = text[-tail_len:]
    tail_match = re.search(r"\s", tail_part)
    if tail_match:
        tail_start_index = len(text) - tail_len + tail_match.start()
        tail = text[tail_start_index:].lstrip()
    else:
        tail = tail_part

    truncated_chars = len(text) - len(head) - len(tail)
    ellipsis = f"\n\n... [内容已截断，共省略 {truncated_chars} 字符] ...\n\n"

    return head + ellipsis + tail
