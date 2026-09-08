<script setup lang="ts">
import { computed, ref } from "vue";

import { statusLabels } from "../labels";
import { useTaskStore } from "../stores/task";
import ApprovalBox from "./ApprovalBox.vue";
import BudgetBoard from "./BudgetBoard.vue";
import CheckpointList from "./CheckpointList.vue";
import GuardrailGrid from "./GuardrailGrid.vue";
import RuntimeManifest from "./RuntimeManifest.vue";
import StateRail from "./StateRail.vue";
import ToolRegistry from "./ToolRegistry.vue";
import TraceList from "./TraceList.vue";

const store = useTaskStore();
const activeTab = ref<"tracePane" | "checkpointPane" | "registryPane">("tracePane");

const heading = computed(() => {
  if (store.reportWorking) {
    return {
      title: "Planner 已完成，正在生成报告",
      subtitle: "Report Model 正在把规划与工具证据转换成可视化路线。",
    };
  }
  switch (store.task?.status) {
    case "completed":
      return {
        title: "Agent 执行完成",
        subtitle: "完整 Trace、预算、门禁和 Checkpoint 已保留，可继续查看最终路线。",
      };
    case "failed":
    case "exhausted":
      return {
        title: "执行未能完成",
        subtitle: store.task?.error || "请检查 Trace 中的失败节点。",
      };
    case "waiting_approval":
      return {
        title: "Agent 正在等待确认",
        subtitle: "敏感工具调用暂停在 Checkpoint，确认后可继续执行。",
      };
    default:
      return {
        title: "Agent 正在构建路线",
        subtitle: "每一次推理、工具调用和状态变化都会实时出现在下方。",
      };
  }
});

const badgeClass = computed(() => {
  if (store.reportWorking) return "status-badge running";
  return `status-badge ${store.task?.status || "idle"}`;
});

const badgeText = computed(() => {
  if (store.reportWorking) return "报告生成中";
  return statusLabels[store.task?.status || ""] || store.task?.status || "待命";
});

const showAnswerFallback = computed(
  () => store.task?.report_status === "failed" && !!store.task?.answer,
);
</script>

<template>
  <section id="runtimeStage" class="runtime-stage">
    <header class="stage-heading">
      <div>
        <p>LIVE AGENT EXECUTION</p>
        <h2 id="runtimeTitle">{{ heading.title }}</h2>
        <span id="runtimeSubtitle">{{ heading.subtitle }}</span>
      </div>
      <div class="stage-actions">
        <div id="statusBadge" :class="badgeClass"><i></i><span>{{ badgeText }}</span></div>
        <button v-if="store.journeyReady" id="viewJourneyButton" type="button" class="journey-button" @click="store.showJourney()">查看行程</button>
        <button id="newPlanButton" type="button" @click="store.newPlan()">新建行程</button>
      </div>
    </header>

    <div id="runtimeContent" class="runtime-content">
      <div class="runtime-grid">
        <section class="trace-console">
          <header class="console-header">
            <div><i></i><i></i><i></i></div>
            <span id="taskId">EPISODE {{ store.task?.task_id.slice(0, 10) }}</span>
            <b>LIVE TRACE</b>
          </header>

          <section class="state-machine" aria-label="任务状态机">
            <div class="section-kicker">
              <span>EXECUTION STATE</span>
              <b id="checkpointMetric">CP {{ store.task?.checkpoint_seq ?? 0 }}</b>
            </div>
            <StateRail
              v-if="store.task"
              :status="store.task.status"
              :approval-observed="store.approvalObserved"
            />
          </section>

          <nav class="inspector-tabs" aria-label="Harness 查看器">
            <button
              v-for="tab in [
                { id: 'tracePane', label: 'Trace' },
                { id: 'checkpointPane', label: 'Checkpoints' },
                { id: 'registryPane', label: 'Tool Contracts' },
              ]"
              :key="tab.id"
              type="button"
              :class="{ active: activeTab === tab.id }"
              @click="activeTab = tab.id as typeof activeTab"
            >
              {{ tab.label }}
            </button>
          </nav>

          <section id="tracePane" class="inspector-pane" :class="{ active: activeTab === 'tracePane' }" :hidden="activeTab !== 'tracePane'">
            <div class="trace-heading"><h3>Agent 调用轨迹</h3><span>STREAMING</span></div>
            <TraceList :events="store.traceEvents" />
          </section>

          <section id="checkpointPane" class="inspector-pane" :class="{ active: activeTab === 'checkpointPane' }" :hidden="activeTab !== 'checkpointPane'">
            <div class="pane-note">从历史 Checkpoint 创建新 Episode，原任务不会被修改。</div>
            <CheckpointList :checkpoints="store.checkpoints" @fork="store.forkFrom" />
          </section>

          <section id="registryPane" class="inspector-pane" :class="{ active: activeTab === 'registryPane' }" :hidden="activeTab !== 'registryPane'">
            <ToolRegistry v-if="store.config" :tools="store.config.tools" class="full" />
          </section>
        </section>

        <aside class="telemetry-panel">
          <RuntimeManifest v-if="store.config" :config="store.config" />
          <BudgetBoard
            v-if="store.task"
            :metrics="store.task.metrics"
            :limits="store.task.limits"
          />
          <GuardrailGrid
            v-if="store.task"
            :guardrails="store.task.guardrails"
            :status="store.task.status"
            :checkpoint-seq="store.task.checkpoint_seq"
          />
          <ApprovalBox
            v-if="store.task?.pending_call"
            :pending-call="store.task.pending_call"
            @decide="store.decideApproval"
          />
          <article v-if="showAnswerFallback" id="answerCard" class="answer-card">
            <span>PLANNER OUTPUT</span>
            <h3>报告生成失败，暂时展示原始规划</h3>
            <pre id="fallbackAnswerText">{{ store.task?.answer }}</pre>
          </article>
        </aside>
      </div>
    </div>
  </section>
</template>
