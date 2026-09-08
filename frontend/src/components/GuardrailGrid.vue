<script setup lang="ts">
import { computed } from "vue";

import type { Guardrails, TaskStatus } from "../types";

const props = defineProps<{
  guardrails: Guardrails;
  status: TaskStatus;
  checkpointSeq: number;
}>();

const schemaGate = computed(() =>
  props.guardrails.schema_validation.passed
    ? { state: "pass", label: `${props.guardrails.schema_validation.validation_errors} 次违规`, hint: "每次工具调用的参数与返回值都按契约校验，全部通过" }
    : { state: "fail", label: "已拦截", hint: "存在违反契约的调用，已被 Harness 拦截" },
);

const evidenceGate = computed(() => {
  const gate = props.guardrails.evidence_gate;
  if (gate.passed)
    return { state: "pass", label: `${gate.successful_tools} 条工具证据`, hint: "最终规划已附上真实工具返回的证据，防止模型凭空编造" };
  return {
    state: props.status === "completed" ? "fail" : "pending",
    label: "等待证据",
    hint: "任务结束前必须积累至少一条成功的工具返回，否则不允许产出结论",
  };
});

const checkpointGate = computed(() => ({
  state: props.checkpointSeq > 0 ? "pass" : "pending",
  label: props.checkpointSeq > 0 ? `已存 ${props.checkpointSeq} 个断点` : "尚未落盘",
  hint: "关键状态持续写入 SQLite，进程崩溃后可从最近的断点继续",
}));
</script>

<template>
  <div class="guardrail-grid">
    <div id="schemaGate" class="gate-card" :class="schemaGate.state" :title="schemaGate.hint">
      <span>参数校验</span><strong>{{ schemaGate.label }}</strong><small>SCHEMA GATE</small>
    </div>
    <div id="evidenceGate" class="gate-card" :class="evidenceGate.state" :title="evidenceGate.hint">
      <span>证据门禁</span><strong>{{ evidenceGate.label }}</strong><small>EVIDENCE GATE</small>
    </div>
    <div id="checkpointGate" class="gate-card" :class="checkpointGate.state" :title="checkpointGate.hint">
      <span>断点保护</span><strong>{{ checkpointGate.label }}</strong><small>CHECKPOINT</small>
    </div>
  </div>
</template>
