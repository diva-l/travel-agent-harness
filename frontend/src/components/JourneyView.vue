<script setup lang="ts">
import { computed, ref } from "vue";

import { categoryColors, categoryIcons, categoryLabels, statusLabels } from "../labels";
import { renderMarkdown } from "../markdown";
import { useTaskStore } from "../stores/task";
import type { RouteDay } from "../types";

const DAY_COLORS = ["#316cff", "#19bdb4", "#5a91ff", "#258f75", "#78a9ff"];

function categoryLabel(category: string): string {
  return categoryLabels[category] || "地点";
}

function categoryIcon(category: string): string {
  return categoryIcons[category] || "📍";
}

function categoryColor(category: string): string {
  return categoryColors[category] || categoryColors.other;
}

const failedImages = ref(new Set<string>());

function hideBrokenImage(url: string) {
  failedImages.value = new Set(failedImages.value).add(url);
}

function showImage(stop: { image_url?: string }): boolean {
  return !!stop.image_url && !failedImages.value.has(stop.image_url);
}

function dayColor(day: number): string {
  return DAY_COLORS[(day - 1) % DAY_COLORS.length] || DAY_COLORS[0];
}

const store = useTaskStore();

const report = computed(() => store.task?.report ?? null);

const dayChoices = computed<Array<{ day: number; theme: string; date: string }>>(() => {
  if (!report.value) return [];
  return [{ day: 0, theme: "全程", date: "" }, ...report.value.days];
});

function selectDay(day: number) {
  store.activeDay = day;
}

const totalStops = computed(
  () => report.value?.days.reduce((sum, day) => sum + day.stops.length, 0) ?? 0,
);

const budgetTotal = computed(() => {
  const total = report.value?.budget?.total_cny;
  return total == null ? "费用待确认" : `¥${Number(total).toLocaleString("zh-CN")}`;
});

const dailyBudget = computed(() => {
  const total = report.value?.budget?.total_cny;
  const days = report.value?.days.length || 0;
  if (total == null || !days) return "—";
  return `¥${Math.round(Number(total) / days).toLocaleString("zh-CN")}`;
});

const budgetItems = computed(() => report.value?.budget?.items ?? []);

const alerts = computed(() => {
  const list = [...(report.value?.alerts ?? []), ...(report.value?.evidence_notes ?? [])].slice(0, 5);
  return list.length ? list : ["票价、开放时间与导航信息请在出发前复核。"];
});

const visibleDays = computed<RouteDay[]>(() => {
  if (!report.value) return [];
  if (store.activeDay === 0) return report.value.days;
  return report.value.days.filter((d) => d.day === store.activeDay);
});

const answerHtml = computed(() => renderMarkdown(store.task?.answer));

const badgeClass = computed(() => `status-badge ${store.task?.status || "completed"}`);
const badgeText = computed(() => statusLabels[store.task?.status || ""] || "已完成");
</script>

<template>
  <section v-if="report" id="journeyView" class="journey-view">
    <header class="journey-header">
      <div>
        <p>推荐行程</p>
        <h2 id="journeyTitle">{{ report.title || "旅行路线" }}</h2>
        <span id="journeySubtitle">{{ report.subtitle || "按日组织的可执行路线" }}</span>
      </div>
      <div class="stage-actions">
        <div id="statusBadge" :class="badgeClass"><i></i><span>{{ badgeText }}</span></div>
        <button id="viewRuntimeButton" class="ghost-dark" type="button" @click="store.showRuntime()">运行过程</button>
        <button id="newPlanButton" type="button" @click="store.newPlan()">新建行程</button>
      </div>
    </header>

    <div class="journey-stats" aria-label="行程概览">
      <article><span>行程天数</span><strong>{{ report.days.length }} 天</strong></article>
      <article><span>站点总数</span><strong>{{ totalStops }} 站</strong></article>
      <article><span>预估总预算</span><strong>{{ budgetTotal }}</strong></article>
      <article><span>日均花费</span><strong>{{ dailyBudget }}</strong></article>
    </div>

    <div id="dayTabs" class="day-tabs" aria-label="按天筛选路线">
      <button
        v-for="choice in dayChoices"
        :key="choice.day"
        type="button"
        :class="{ active: store.activeDay === choice.day }"
        @click="selectDay(choice.day)"
      >
        {{ choice.day === 0 ? "全程" : `第 ${choice.day} 天` }}
      </button>
    </div>

    <div class="journey-days">
      <section v-for="day in visibleDays" :key="day.day" class="day-section">
        <header class="day-section-head">
          <b class="day-chip" :style="{ background: dayColor(day.day) }">D{{ day.day }}</b>
          <div>
            <strong>第 {{ day.day }} 天</strong>
            <span>{{ [day.date, day.theme].filter(Boolean).join(" · ") }}</span>
          </div>
        </header>

        <ol class="stop-timeline">
          <template v-for="(stop, index) in day.stops" :key="index">
            <li class="stop-row">
              <div class="stop-time">{{ stop.time || "时间待定" }}</div>
              <i class="stop-dot" :style="{ borderColor: dayColor(day.day) }">
                <em :style="{ background: dayColor(day.day) }">{{ index + 1 }}</em>
              </i>
              <article class="stop-card">
                <div class="stop-card-body">
                  <span class="stop-card-badge" :style="{ color: categoryColor(stop.category), background: categoryColor(stop.category) + '14' }">
                    {{ categoryIcon(stop.category) }} {{ categoryLabel(stop.category) }}
                  </span>
                  <h4>{{ stop.name }}</h4>
                  <p class="stop-card-meta">
                    <span v-if="stop.duration_minutes != null">停留 {{ stop.duration_minutes }} 分钟</span>
                    <span v-if="stop.cost_cny != null">约 ¥{{ stop.cost_cny }}</span>
                  </p>
                  <p v-if="stop.address" class="stop-card-address">{{ stop.address }}</p>
                  <p v-if="stop.note" class="stop-card-note">{{ stop.note }}</p>
                </div>
                <img
                  v-if="showImage(stop)"
                  class="stop-card-img"
                  :src="stop.image_url"
                  :alt="stop.name"
                  loading="lazy"
                  referrerpolicy="no-referrer"
                  @error="hideBrokenImage(stop.image_url)"
                />
                <div
                  v-else
                  class="stop-card-img stop-card-img--empty"
                  :style="{ color: categoryColor(stop.category), background: categoryColor(stop.category) + '12' }"
                >{{ categoryIcon(stop.category) }}</div>
              </article>
            </li>
            <li v-if="index < day.stops.length - 1" class="transit-row">
              <span class="transit-chip">{{ stop.transport_to_next || "交通方式待确认" }}</span>
            </li>
          </template>
        </ol>
      </section>
    </div>

    <div class="report-support">
      <article class="route-summary">
        <span>路线设计思路</span>
        <p id="journeySummary">{{ report.summary || "路线节点来自 Planner Agent 的规划结果。" }}</p>
      </article>
      <article class="budget-summary">
        <span>预算明细（估算）</span>
        <strong id="reportBudget">{{ budgetTotal }}</strong>
        <ul id="budgetItems" class="budget-list">
          <li v-for="(item, index) in budgetItems" :key="index">
            <span>{{ item.label }}</span>
            <b>{{ item.amount_cny == null ? "以实际为准" : `¥${item.amount_cny}` }}</b>
          </li>
          <li v-if="!budgetItems.length"><span>未明确费用不做估算</span><b>—</b></li>
        </ul>
      </article>
      <article class="evidence-summary">
        <span>出发前请确认</span>
        <ul id="reportAlerts">
          <li v-for="(alert, index) in alerts" :key="index">{{ alert }}</li>
        </ul>
      </article>
    </div>

    <section class="original-plan" aria-label="Planner 原始规划">
      <header class="plan-header">
        <span>Planner 原始规划全文</span>
        <small><span id="taskId">EPISODE {{ store.task?.task_id.slice(0, 10) }}</span> · 由规划模型直接产出，以下为完整推理结论</small>
      </header>
      <div id="answerText" class="markdown-body" v-html="answerHtml"></div>
    </section>
  </section>
</template>
