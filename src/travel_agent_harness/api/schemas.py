from __future__ import annotations

from datetime import date
from typing import Literal
import re

from pydantic import BaseModel, Field, field_validator

# 自由文本护栏：textarea 的换行/冒号/分号/标记符号会把拼出的 query 拖出
# 训练分布（实测"不早起\n尽量：少折返；住宿要好"会让 tagged RL 模型复读工具
# schema）。清洗成数据集的逗号短句风格，不改写用户措辞。
_FREE_TEXT_STRIP = re.compile(r"[：:；;#*\"'<>|&!！]+")
_FREE_TEXT_NEWLINES = re.compile(r"[\r\n]+")
_FREE_TEXT_COMMAS = re.compile(r"，{2,}")


def _clean_free_text(value: str, max_chars: int = 80) -> str:
    text = _FREE_TEXT_NEWLINES.sub("，", value.strip())
    text = _FREE_TEXT_STRIP.sub("", text)
    text = _FREE_TEXT_COMMAS.sub("，", text).strip("，。 ")
    return text[:max_chars]


class TripPlanRequest(BaseModel):
    origin: str = Field(min_length=1, max_length=80)
    destination: str = Field(min_length=1, max_length=80)
    start_date: date
    days: int = Field(ge=1, le=30)
    budget_cny: int = Field(ge=100, le=1_000_000)
    travelers: int = Field(default=1, ge=1, le=20)
    pace: Literal["relaxed", "balanced", "intensive"] = "balanced"
    preferences: list[str] = Field(default_factory=list, max_length=12)
    notes: str = Field(default="", max_length=1000)

    @field_validator("origin", "destination")
    @classmethod
    def normalize_place(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("地点不能为空")
        return normalized

    @field_validator("preferences")
    @classmethod
    def normalize_preferences(cls, values: list[str]) -> list[str]:
        # 最多取 4 个、每个 12 字，与数据集"避暑与民宿摄影"这种短偏好对齐
        cleaned = [_clean_free_text(value, 12) for value in values]
        return [value for value in cleaned if value][:4]

    def to_objective(self) -> str:
        # 严格模仿训练/测试数据集的口语句式（data/test.jsonl 100 条人工总结）：
        #   “周末两天从上海出发去莫干山，2人，避暑与民宿摄影，预算人均1200元”
        #   “五一假期从重庆到成都2日自驾，途经乐山，美食和夜生活，3人，预算总3000”
        # 特征：相对日期或“M月D号”（从不带年份/ISO）、逗号短句、1人不写人数、
        # 预算说“人均/总”、无字段标签与冒号清单。字段式模板会让 tagged RL
        # 模型 OOD（复读工具 schema 而不是调用工具）。
        delta = (self.start_date - date.today()).days
        if delta == 0:
            time_word = "今天"
        elif delta == 1:
            time_word = "明天"
        elif delta == 2:
            time_word = "后天"
        else:
            time_word = f"{self.start_date.month}月{self.start_date.day}号"
        day_word = "一天" if self.days == 1 else f"{self.days}天"
        if self.origin == self.destination:
            head = f"{time_word}在{self.destination}玩{day_word}"
        else:
            head = f"{time_word}从{self.origin}出发去{self.destination}玩{day_word}"
        parts = [head]
        if self.travelers > 1:
            parts.append(f"{self.travelers}人")
        pace_interest = {"relaxed": "慢游", "balanced": "", "intensive": ""}[self.pace]
        interests = [item for item in [pace_interest, *self.preferences] if item]
        if interests:
            parts.append("、".join(interests))
        if self.travelers > 1:
            parts.append(f"预算人均{self.budget_cny // self.travelers}元")
        else:
            parts.append(f"预算{self.budget_cny}元")
        if self.notes.strip():
            notes = _clean_free_text(self.notes)
            if notes:
                parts.append(notes)
        return "，".join(parts) + "。帮我安排一下行程吧"


class ApprovalRequest(BaseModel):
    approved: bool


class ForkRequest(BaseModel):
    checkpoint_seq: int = Field(ge=1)
    run: bool = True
