<script setup lang="ts">
import { computed } from "vue";

import type { TaskLimits, TaskMetrics } from "../types";

const props = defineProps<{ metrics: TaskMetrics; limits: TaskLimits }>();

interface BudgetCard {
  key: string;
  label: string;
  hint: string;
  text: string;
  percent: number;
}

const cards = computed<BudgetCard[]>(() => {
  const { metrics: m, limits: l } = props;
  const entries: Array<[string, string, string, number, number, string]> = [
    ["step", "模型轮次", "模型与工具的最大交互轮数，对应训练口径", m.step, l.max_steps, `${m.step} / ${l.max_steps}`],
    ["tool", "工具调用", "实际发起的工具调用总数硬上限", m.tool_calls, l.max_tool_calls, `${m.tool_calls} / ${l.max_tool_calls}`],
    ["token", "Token 消耗", "累计输入与输出 Token 上限", m.total_tokens, l.max_total_tokens, `${m.total_tokens} / ${l.max_total_tokens}`],
    ["time", "已用时长", "任务累计墙钟时间上限（秒）", m.elapsed_seconds, l.max_seconds, `${m.elapsed_seconds.toFixed(1)} / ${l.max_seconds}s`],
  ];
  return entries.map(([key, label, hint, value, limit, text]) => ({
    key,
    label,
    hint,
    text,
    percent: Math.min(100, (value / limit) * 100),
  }));
});

function cardClass(percent: number): Record<string, boolean> {
  return {
    "budget-card": true,
    warning: percent >= 70 && percent < 90,
    danger: percent >= 90,
  };
}
</script>

<template>
  <div class="budget-board">
    <div
      v-for="card in cards"
      :key="card.key"
      :class="cardClass(card.percent)"
      :data-budget="card.key"
      :title="card.hint"
    >
      <span>{{ card.label }}</span>
      <strong :id="`${card.key}Metric`">{{ card.text }}</strong>
      <i><b :id="`${card.key}Fill`" :style="{ width: `${card.percent}%` }"></b></i>
    </div>
  </div>
</template>
