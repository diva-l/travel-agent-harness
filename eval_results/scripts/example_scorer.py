# -*- coding: utf-8 -*-
"""Example scorer for `run_eval.py --scorer`.

A scorer is any Python file exposing `score(messages) -> float`: it receives
the full trajectory as training-format messages (system/user/assistant with
<tool_call>/<answer> blocks) and returns a scalar in [-1, 1].

This example is a deliberately trivial heuristic (does the trajectory end
with a substantive <answer>?) to show the interface shape — it is NOT the
scorer used for the numbers in report.md.
"""

from __future__ import annotations

import re


def score(messages: list[dict]) -> float:
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        match = re.search(r"<answer>(.*?)</answer>", message.get("content") or "", re.DOTALL)
        if match:
            answer = match.group(1).strip()
            # 200+ chars of final answer counts as a substantive close-out.
            return 1.0 if len(answer) >= 200 else 0.0
    return -1.0
