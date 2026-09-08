<script setup lang="ts">
import { computed } from "vue";

import { statusLabels } from "../labels";
import type { TaskStatus } from "../types";

const props = defineProps<{
  status: TaskStatus;
  approvalObserved: boolean;
}>();

const terminalStatuses = ["completed", "exhausted", "failed"];

const nodeClass = computed(() => {
  const terminal = terminalStatuses.includes(props.status);
  return {
    created: props.status === "created" ? "active" : "done",
    running:
      props.status === "running" ? "active" : props.status !== "created" ? "done" : "",
    waiting_approval: props.status === "waiting_approval"
      ? "active"
      : terminal
        ? props.approvalObserved
          ? "done"
          : "bypassed"
        : "",
    terminal: terminal ? "active" : "",
  };
});

const terminalLabel = computed(() =>
  terminalStatuses.includes(props.status) ? statusLabels[props.status] || props.status : "终态",
);
</script>

<template>
  <div id="stateRail" class="state-rail">
    <div data-state="created" :class="nodeClass.created"><i></i><span>已创建</span></div><b>→</b>
    <div data-state="running" :class="nodeClass.running"><i></i><span>执行中</span></div><b>→</b>
    <div data-state="waiting_approval" :class="nodeClass.waiting_approval"><i></i><span>审批</span></div><b>→</b>
    <div data-state="terminal" :class="nodeClass.terminal"><i></i><span>{{ terminalLabel }}</span></div>
  </div>
</template>
