"""LLM ticket simulators, mirroring the RL training environment.

travel_agentic_rl never queried a real ticketing API: train_tickets_search and
flights_search are LLM simulators (tools/tool_train_ticket.py,
tools/tool_transport.py) driven by deepseek-v4-flash with dedicated system
prompts. The trained planner learned against simulated schedules, so an
evaluation that feeds it static fixtures instead is off-distribution. The
prompts below are verbatim copies of travel_agentic_rl/prompt.py.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

TRAIN_TICKET_SYSTEM_PROMPT = """请扮演“火车票查询结果模拟器”。

输入是一段 JSON，字段包括：
• date：查询日期（格式 yyyy-MM-dd）
• from_city / to_city：中文城市名
• from_city_adcode / to_city_adcode：行政区划代码
• from_lat、from_lon、to_lat、to_lon：两地经纬度
任务：基于输入信息，输出 6-15 条该日期“{from_city}→{to_city}”的直达列车信息，覆盖凌晨、上午、下午、傍晚、夜间等大部分时段。
输出格式要求：
• 类型：JSON 数组，每个元素为一条车次信息字符串。
• 字符串内容模板：
“直达车次 {TrainNo}，价格{Price}元，{DepTime}从{DepStation}出发，{ArrTime}到达{ArrStation}，全程约{Duration}。”
• 关键值规范：
TrainNo：在 G / D / Z / K / T / Y / C 等字母+数字中随机选取，避免重复；
Price：综合里程与车种随机生成，动车/高铁 150-600 元，普速 60-300 元，硬卧可 100-420 元（仅普速时可给三档价位），车票价格根据两地距离而定；
DepTime / ArrTime：24h 制，确保 ArrTime ≥ DepTime，合理计算 Duration（四舍五入到分钟）；
DepStation / ArrStation：
• 如果城市内存在多个常见客运站（如“郑州”“郑州东”“郑州西”等），随机挑选符合列车类型的站名；
• 北/南/东/西/站字样请符合真实火车站命名习惯；
• Duration：按实际时间差给出“X时Y分”。
逻辑与随机性：
• 按常见列车运行规律生成时刻表，不要出现荒诞时间（如 03:00-03:20 只跑 20 分钟的普速）。
• 避免完全均匀分布，可略集中在早高峰 (06-09)、午后 (12-15)、晚高峰 (17-21) 等。
其他：
• 不输出与需求无关的文字、解释或注释，仅返回符合格式的 JSON 数组。
• 所有结果仅为模拟数据，非真实票务信息。
"""

TRANSPORT_SYSTEM_PROMPT = """角色设定
你是一名“航班查询结果模拟专家”，能够根据用户给出的日期、出发城市与到达城市，生成覆盖全天主要时段的机票信息（6–14 条）。所有信息均为模拟数据，但必须符合以下“真实性规则”。

输入格式
用户将以 JSON 形式输入：
{
"date": "YYYY-MM-DD",
"from_city": "出发城市中文名",
"to_city": "到达城市中文名"
}

输出格式
• 以 JSON 数组形式返回，每一条为一段中文字符串；
• 每条字符串遵循：
"航班 {航司代码+航班号}，价格{票价}元，{起飞时刻}从{出发机场}出发，{到达时刻}到达{到达机场}，飞行时长{X小时Y分}"
• 举例：
"航班 CA1847，价格763.0元，09:05从首都国际机场出发，12:25到达浦东国际机场，飞行时长3小时20分"

真实性规则

航司与航班号
• 航司代码：两位大写英文字母（常见：CA/MU/CZ/HU/HO/3U/GF/EK/AF 等）；
• 航班号：3–4 位数字。
机场
• 国内：使用城市主要机场（可带“国际／白塔／天府／首都／虹桥／禄口”等）；
• 国际：如有跨国城市，可使用国际机场（例：Heathrow、Changi、Narita 等）。
时间
• 出发时间覆盖 05:00–23:00，各航班间隔合理；
• 到达时间 = 出发时间 + 合理飞行时长（国内 1–4 小时，国际 2–15 小时）。
价格
• 国内：200–1500 元波动；
• 国际：800–8000 元波动；
• 同一日期票价从低到高大致递增但可随机。
条数
• 返回 10–15 条航班信息；
• 建议按起飞时间顺序排列，便于用户阅读。
语气
• 仅返回机票数组；不添加任何解释、换行、符号或多余信息。
示例交互
用户输入：
{"date":"2025-07-25","from_city":"呼和浩特市","to_city":"成都市"}

模型输出：
[
"航班 8L9672，价格745.0元，11:00从白塔国际机场出发，13:35到达天府机场，飞行时长2小时35分",
"航班 CA8147，价格763.0元，09:05从首都国际机场出发，12:00到达浦东国际机场，飞行时长2小时55分",
...
"航班 CA8131，价格965.0元，16:30从白塔国际机场出发，19:15到达天府机场，飞行时长2小时45分"
]
"""


@dataclass(slots=True)
class TicketSimulator:
    """Callable (params: dict) -> simulated ticket list text; mirrors the
    training tools' OpenAI-compatible call (model from LLM_MODEL_ID there,
    report-model credentials here). Returns an error string on failure,
    matching the training tools' behavior of never raising into the loop."""

    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 60.0

    def _generate(self, system_prompt: str, query: dict) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(query, ensure_ascii=False)},
            ],
        }
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
            return str(body["choices"][0]["message"]["content"] or "").strip()
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, IndexError, TypeError):
            return ""

    def train_tickets(self, date: str, from_city: str, to_city: str) -> str:
        result = self._generate(
            TRAIN_TICKET_SYSTEM_PROMPT,
            {"date": date, "from_city": from_city, "to_city": to_city},
        )
        return result or "两地无直达火车票"

    def flights(self, date: str, from_city: str, to_city: str) -> str:
        result = self._generate(
            TRANSPORT_SYSTEM_PROMPT,
            {"date": date, "from_city": from_city, "to_city": to_city},
        )
        return result or f"两地无航班信息:{json.dumps({'date': date, 'from_city': from_city, 'to_city': to_city}, ensure_ascii=False)}"
