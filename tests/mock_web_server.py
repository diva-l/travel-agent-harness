from __future__ import annotations

import tempfile
from pathlib import Path

import uvicorn

from travel_agent_harness.api.app import create_app
from travel_agent_harness.config import HarnessConfig
from travel_agent_harness.harness import build_default_harness
from travel_agent_harness.llm import ModelTurn, ScriptedModel
from travel_agent_harness.models import ToolCall
from travel_agent_harness.reporting import ScriptedReportModel


db_path = Path(tempfile.gettempdir()) / "travel-agent-browser-smoke.db"
config = HarnessConfig(
    api_key="browser-test",
    model="scripted-browser-test",
    db_path=db_path,
    max_steps=5,
    max_seconds=30,
    max_total_tokens=1000,
    max_tool_calls=5,
    model_retries=0,
)
model = ScriptedModel(
    [
        ModelTurn(
            tool_calls=[
                ToolCall(
                    "browser-call-1",
                    "weather_search",
                    {"city": "杭州"},
                )
            ],
            prompt_tokens=18,
            completion_tokens=9,
        ),
        ModelTurn(
            content=(
                "杭州三日路线已生成。\n\n"
                "Day 1｜西湖—北山街—湖滨\n"
                "Day 2｜灵隐寺—法喜寺—龙井村\n"
                "Day 3｜小河直街—桥西历史街区\n\n"
                "天气信息来自 demo_fixture，请在出发前使用真实天气服务复核。"
            ),
            prompt_tokens=26,
            completion_tokens=42,
        ),
    ]
)
reporter = ScriptedReportModel(
    {
        "title": "杭州三日 · 山湖之间",
        "subtitle": "从西湖城市漫步到灵隐龙井，再抵达运河旧街",
        "summary": "三天按片区收拢动线，减少折返；文字只补充时间、费用与风险信息。",
        "map_kind": "geo",
        "days": [
            {
                "day": 1,
                "date": "2026-09-10",
                "theme": "西湖与北山",
                "stops": [
                    {"name": "断桥残雪", "time": "09:30", "duration_minutes": 45, "cost_cny": 0, "category": "sight", "transport_to_next": "步行 12 分钟", "note": "从北侧进入西湖，避开正午人流。", "coordinates": {"lng": 120.1484, "lat": 30.2591}},
                    {"name": "北山街", "time": "11:00", "duration_minutes": 90, "cost_cny": 0, "category": "activity", "transport_to_next": "公交", "note": "沿湖向西，串联历史建筑。", "coordinates": {"lng": 120.1427, "lat": 30.2587}},
                    {"name": "湖滨", "time": "18:00", "duration_minutes": 120, "cost_cny": 120, "category": "food", "transport_to_next": "", "note": "晚餐与夜景放在同一片区。", "coordinates": {"lng": 120.1678, "lat": 30.2521}},
                ],
            },
            {
                "day": 2,
                "date": "2026-09-11",
                "theme": "灵隐与龙井",
                "stops": [
                    {"name": "灵隐寺", "time": "08:30", "duration_minutes": 150, "cost_cny": 75, "category": "sight", "transport_to_next": "公交", "note": "上午优先安排核心景点。", "coordinates": {"lng": 120.1022, "lat": 30.2408}},
                    {"name": "法喜寺", "time": "13:30", "duration_minutes": 90, "cost_cny": 10, "category": "sight", "transport_to_next": "打车", "note": "与龙井村顺路组合。", "coordinates": {"lng": 120.0929, "lat": 30.2258}},
                    {"name": "龙井村", "time": "16:00", "duration_minutes": 120, "cost_cny": 80, "category": "activity", "transport_to_next": "", "note": "傍晚体验茶村步道。", "coordinates": {"lng": 120.1114, "lat": 30.2186}},
                ],
            },
            {
                "day": 3,
                "date": "2026-09-12",
                "theme": "运河旧街",
                "stops": [
                    {"name": "小河直街", "time": "10:00", "duration_minutes": 120, "cost_cny": 50, "category": "activity", "transport_to_next": "步行", "note": "从社区街巷开始慢游。", "coordinates": {"lng": 120.1465, "lat": 30.3204}},
                    {"name": "桥西历史街区", "time": "14:00", "duration_minutes": 150, "cost_cny": 60, "category": "sight", "transport_to_next": "", "note": "作为返程前的收尾片区。", "coordinates": {"lng": 120.1397, "lat": 30.3199}},
                ],
            },
        ],
        "budget": {"total_cny": 3000, "items": [{"label": "市内交通", "amount_cny": 260}, {"label": "餐饮与体验", "amount_cny": 980}]},
        "alerts": ["天气、开放时间与预约规则请在出发前复核。"],
        "evidence_notes": ["当前浏览器验收使用可复现的 demo_fixture。"],
    }
)
harness = build_default_harness(config, model=model, reporter=reporter)


if __name__ == "__main__":
    uvicorn.run(create_app(config, harness), host="127.0.0.1", port=8765)
