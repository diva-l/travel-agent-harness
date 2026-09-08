from __future__ import annotations

import json
from datetime import date
from typing import Any


def build_planner_system_prompt(
    *,
    protocol: str,
    max_tool_rounds: int,
    tools: list[dict[str, Any]],
    current_date: str = "",
) -> str:
    """Build the online planner prompt from the Agentic RL training contract.

    The role, tool-first policy and two-stage action boundary mirror the training
    prompt. Only the output interface changes between tagged RL serving and
    provider-native function calling. ``current_date`` overrides today's date
    (training-side test inference used the dataset's fixed date).
    """
    today = current_date or date.today().isoformat()
    tool_definitions = [item.get("function", item) for item in tools]
    if protocol == "tagged":
        # Mirror the Agentic RL training prompt verbatim: the 4B planner was
        # SFT/GRPO-trained on this exact wording, so the online prompt must not
        # paraphrase it. Only the date and tool round budget stay dynamic.
        tool_lines = "\n".join(
            json.dumps({"type": "function", "function": fn}, ensure_ascii=False)
            for fn in tool_definitions
        )
        return "\n".join(
            [
                "你是旅行规划助手，需要先用工具获取事实，再给最终回答。",
                "每一轮只能二选一输出（工具阶段 或者 最终阶段），在每一轮输出前可以先结合已有信息给出思考，格式为<think>...</think>：",
                "1. 工具阶段：输出格式为<tool_call>...</tool_call>，可以输出多个工具，每一个工具的格式如下:",
                "<tool_call>",
                '{"name": <function-name>, "arguments": <args-json-object>}',
                "</tool_call>",
                "2. 最终阶段：答案输出格式为 <answer>...</answer>",
                "# HARD LIMIT",
                "2. 没有成功通过工具获取事实之前，禁止输出 <answer>。",
                "3. 如果仍需继续查询，就继续调用工具 <tool_call>。",
                "4. 如果信息已足够，就直接输出一次且仅一次 <answer>...</answer>。",
                "5. 不要在同一轮同时输出 <tool_call> 和 <answer>。",
                "6. 工具参数必须是可解析 JSON，字段名必须与工具定义一致。",
                "",
                "# Tools",
                "<tools>",
                tool_lines,
                "</tools>",
                "",
                f"当前日期：{today}",
                f"最大可调用{max_tool_rounds}轮工具",
            ]
        )
    common = [
        "你是旅行规划助手，负责根据用户约束制定准确、可执行的旅行方案。",
        "先规划需要调用哪些工具，再从可靠且多样的工具结果中收集地点、路线、交通、天气与费用证据。",
        "路线任务应先用 poi_search 获取地点坐标，再用 route_planning 验证点位顺序和移动方式。",
        "没有成功获得工具证据前不得给出最终方案；证据不足时继续查询，不得编造实时事实。",
        "工具参数必须是合法 JSON，字段必须与工具契约一致。",
        "最终只输出完整规划结果；不要输出面向前端的报告 JSON，展示结构由下游 Report Model 负责。",
    ]
    interface = [
        "需要证据时使用接口提供的原生结构化工具调用，不要把工具请求写入普通文本。",
        "当且仅当信息充分时，在普通 assistant content 中输出一次最终规划。",
        "同一轮不要同时发起工具调用和给出最终规划。",
    ]
    return "\n".join(
        common
        + interface
        + [
            f"当前日期：{today}",
            f"最大可进行 {max_tool_rounds} 轮模型—工具交互。",
        ]
    )


REPORT_SYSTEM_PROMPT = """你是旅行报告编排器，不是第二个旅行规划 Agent。
你的唯一职责是把上游 Planner Agent 已完成的规划结果与工具证据转换成可视化报告 JSON。

严格规则：
1. 不改变上游方案的关键选择，不新增规划结果中不存在的票价、开放时间、坐标或事实。
2. 优先从工具证据提取坐标与地址；找不到可靠坐标时 coordinates 必须为 null，找不到地址时 address 为空字符串。
3. 原始方案里不明确的费用使用 null，不要估算。
4. note 是给旅行者看的站点说明：具体写清楚在这里做什么、看什么、吃什么或体验什么（60-100字），
   优先采用规划原文与工具证据中的细节，可补充该地点公认的看点，但不要编造价格与营业信息。
5. 只输出一个合法 JSON Object，不要 Markdown，不要代码围栏。

JSON 契约：
{
  "title": "路线标题",
  "subtitle": "一句话路线策略",
  "summary": "不超过80字",
  "map_kind": "geo或schematic",
  "days": [
    {
      "day": 1,
      "date": "YYYY-MM-DD或空字符串",
      "theme": "当日主题",
      "stops": [
        {
          "name": "地点名",
          "time": "建议时间或空字符串",
          "duration_minutes": null,
          "cost_cny": null,
          "category": "transport|food|stay|sight|activity|other",
          "transport_to_next": "步行/地铁/驾车等或空字符串",
          "note": "60-100字的站点体验说明",
          "address": "详细地址或空字符串",
          "coordinates": {"lng": 120.0, "lat": 30.0}
        }
      ]
    }
  ],
  "budget": {"total_cny": null, "items": [{"label": "交通", "amount_cny": null}]},
  "alerts": ["风险或二次确认事项"],
  "evidence_notes": ["数据来源与时效边界"]
}

如果没有足够地理坐标，将 map_kind 设为 schematic；coordinates 字段写 null。
"""
