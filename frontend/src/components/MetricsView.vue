<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";

import { api } from "../api";
import { statusLabels } from "../labels";
import type { MetricsSummary } from "../types";

const metrics = ref<MetricsSummary | null>(null);
const error = ref("");
const updatedAt = ref<Date | null>(null);
let timer: number | undefined;

async function refresh() {
  try {
    metrics.value = await api<MetricsSummary>("/api/metrics");
    error.value = "";
    updatedAt.value = new Date();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  }
}

onMounted(() => {
  refresh();
  timer = window.setInterval(refresh, 10_000);
});
onUnmounted(() => window.clearInterval(timer));

const successRateText = computed(() => {
  const rate = metrics.value?.success_rate;
  return rate == null ? "—" : `${Math.round(rate * 1000) / 10}%`;
});

const totalTokens = computed(() => {
  const t = metrics.value?.tokens;
  return t ? t.prompt_total + t.completion_total : 0;
});

const statusRows = computed(() =>
  Object.entries(metrics.value?.tasks_by_status || {}).map(([status, count]) => ({
    status,
    count,
    label: statusLabels[status] || status,
  })),
);

const toolUsageRows = computed(() => {
  const entries = Object.entries(metrics.value?.tool_usage || {});
  const peak = Math.max(1, ...entries.map(([, count]) => count));
  return entries.map(([name, count]) => ({
    name,
    count,
    width: `${Math.max(6, Math.round((count / peak) * 100))}%`,
  }));
});

const reportLabels: Record<string, string> = {
  completed: "已生成",
  failed: "生成失败",
  skipped: "未请求",
  pending: "排队中",
  running: "生成中",
};

const reportRows = computed(() =>
  Object.entries(metrics.value?.reports || {})
    .filter(([, count]) => count > 0)
    .map(([status, count]) => ({ label: reportLabels[status] || status, count })),
);

function formatSeconds(value: number): string {
  if (value >= 60) return `${(value / 60).toFixed(1)} min`;
  return `${value.toFixed(1)} s`;
}

function formatCount(value: number): string {
  return value >= 1000 ? `${(value / 1000).toFixed(1)}k` : String(value);
}
</script>

<template>
  <section class="metrics-view">
    <div class="metrics-header">
      <div>
        <h2>运行指标</h2>
        <p>
          SQLite tasks / traces 表实时聚合
          <template v-if="updatedAt">· 更新于 {{ updatedAt.toLocaleTimeString() }}（每 10s 自动刷新）</template>
        </p>
      </div>
      <button class="refresh-btn" type="button" @click="refresh">刷新</button>
    </div>

    <p v-if="error" class="metrics-error">{{ error }}</p>

    <template v-if="metrics">
      <div class="kpi-grid">
        <div class="kpi-card">
          <span>任务总数</span>
          <strong>{{ metrics.tasks_total }}</strong>
          <small>{{ metrics.terminal_tasks }} 个已到终局</small>
        </div>
        <div class="kpi-card">
          <span>终局成功率</span>
          <strong>{{ successRateText }}</strong>
          <small>completed / 终局任务</small>
        </div>
        <div class="kpi-card">
          <span>工具调用</span>
          <strong>{{ metrics.tool_calls.successful }} / {{ metrics.tool_calls.total }}</strong>
          <small>成功 / 总计 · {{ metrics.tool_calls.validation_errors }} 次契约校验拦截</small>
        </div>
        <div class="kpi-card">
          <span>Token 消耗</span>
          <strong>{{ formatCount(totalTokens) }}</strong>
          <small>输入 {{ formatCount(metrics.tokens.prompt_total) }} · 输出 {{ formatCount(metrics.tokens.completion_total) }}</small>
        </div>
      </div>

      <div class="metrics-columns">
        <div class="metrics-panel">
          <h3>状态分布</h3>
          <div class="status-chips">
            <span v-for="row in statusRows" :key="row.status" class="status-chip" :class="row.status">
              {{ row.label }} <b>{{ row.count }}</b>
            </span>
            <span v-if="!statusRows.length" class="empty-hint">还没有任务记录</span>
          </div>
          <h3>Report 阶段</h3>
          <div class="status-chips">
            <span v-for="row in reportRows" :key="row.label" class="status-chip report">
              {{ row.label }} <b>{{ row.count }}</b>
            </span>
            <span v-if="!reportRows.length" class="empty-hint">还没有报告记录</span>
          </div>
          <h3>护栏计数</h3>
          <div class="status-chips">
            <span class="status-chip">工具错误 <b>{{ metrics.tool_calls.errors }}</b></span>
            <span class="status-chip">校验拦截 <b>{{ metrics.tool_calls.validation_errors }}</b></span>
          </div>
        </div>

        <div class="metrics-panel">
          <h3>工具使用分布</h3>
          <div class="tool-bars">
            <div v-for="row in toolUsageRows" :key="row.name" class="tool-bar-row">
              <span class="tool-name">{{ row.name }}</span>
              <div class="tool-bar-track">
                <div class="tool-bar-fill" :style="{ width: row.width }"></div>
              </div>
              <b>{{ row.count }}</b>
            </div>
            <span v-if="!toolUsageRows.length" class="empty-hint">还没有成功的工具调用</span>
          </div>
        </div>
      </div>

      <div class="metrics-panel dist-panel">
        <h3>单任务分布（终局任务）</h3>
        <table>
          <thead>
            <tr><th></th><th>avg</th><th>p50</th><th>max</th></tr>
          </thead>
          <tbody>
            <tr>
              <td>推理步数</td>
              <td>{{ metrics.steps.avg }}</td>
              <td>{{ metrics.steps.p50 }}</td>
              <td>{{ metrics.steps.max }}</td>
            </tr>
            <tr>
              <td>任务耗时</td>
              <td>{{ formatSeconds(metrics.elapsed_seconds.avg) }}</td>
              <td>{{ formatSeconds(metrics.elapsed_seconds.p50) }}</td>
              <td>{{ formatSeconds(metrics.elapsed_seconds.max) }}</td>
            </tr>
            <tr>
              <td>Token / 任务</td>
              <td colspan="3">平均 {{ formatCount(metrics.tokens.per_task_avg) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>
</template>

<style scoped>
.metrics-view {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 24px;
  box-shadow: var(--shadow);
  padding: 36px 40px 40px;
}
.metrics-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; }
.metrics-header h2 { margin: 0; font: 700 26px/1.3 "STZhongsong", "SimSun", serif; letter-spacing: .04em; }
.metrics-header p { margin: 6px 0 0; color: var(--muted); font-size: 13px; }
.refresh-btn {
  padding: 8px 22px;
  color: #fff;
  background: var(--text);
  border: none;
  border-radius: 99px;
  cursor: pointer;
  transition: opacity .15s;
}
.refresh-btn:hover { opacity: .82; }
.metrics-error { color: var(--danger); }
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 14px;
  margin-top: 26px;
}
.kpi-card {
  display: grid;
  gap: 6px;
  padding: 20px 22px;
  background: var(--surface-soft);
  border: 1px solid var(--line);
  border-radius: 16px;
}
.kpi-card span { color: var(--muted); font-size: 12px; letter-spacing: .08em; }
.kpi-card strong { font: 700 26px/1.2 "Bahnschrift", "Segoe UI Variable", sans-serif; }
.kpi-card small { color: #98a2b3; font-size: 12px; }
.metrics-columns {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 14px;
  margin-top: 14px;
}
.metrics-panel {
  padding: 22px 24px;
  background: var(--surface-soft);
  border: 1px solid var(--line);
  border-radius: 16px;
}
.metrics-panel h3 {
  margin: 0 0 12px;
  color: var(--muted);
  font: 700 11px "Bahnschrift", ui-monospace, monospace;
  letter-spacing: .16em;
  text-transform: uppercase;
}
.metrics-panel h3:not(:first-child) { margin-top: 22px; }
.status-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.status-chip {
  padding: 5px 13px;
  font-size: 13px;
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 99px;
}
.status-chip b { margin-left: 4px; }
.status-chip.completed { color: #0d7a52; border-color: #a9e5cd; background: #eafaf3; }
.status-chip.failed { color: #b42318; border-color: #f5c6c2; background: #fdf0ef; }
.status-chip.running { color: var(--blue-dark); border-color: #c3d4ff; background: #eef3ff; }
.status-chip.exhausted { color: #97590a; border-color: #f3ddb0; background: #fdf6e7; }
.empty-hint { color: #98a2b3; font-size: 13px; }
.tool-bars { display: grid; gap: 10px; }
.tool-bar-row { display: grid; grid-template-columns: 150px 1fr 34px; align-items: center; gap: 12px; }
.tool-name { font: 12px ui-monospace, Consolas, monospace; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tool-bar-track { height: 8px; background: #fff; border: 1px solid var(--line); border-radius: 99px; overflow: hidden; }
.tool-bar-fill { height: 100%; background: linear-gradient(90deg, var(--blue), var(--cyan)); border-radius: 99px; }
.tool-bar-row b { text-align: right; font-size: 13px; }
.dist-panel { margin-top: 14px; }
.dist-panel table { width: 100%; border-collapse: collapse; font-size: 13px; }
.dist-panel th, .dist-panel td { padding: 9px 6px; text-align: left; border-bottom: 1px solid var(--line); }
.dist-panel th { color: var(--muted); font: 700 11px "Bahnschrift", ui-monospace, monospace; letter-spacing: .12em; }
.dist-panel tbody tr:last-child td { border-bottom: none; }
@media (max-width: 640px) {
  .metrics-view { padding: 24px 18px; }
  .tool-bar-row { grid-template-columns: 110px 1fr 30px; }
}
</style>
