<script setup lang="ts">
import { onMounted, ref } from "vue";

import IntakeForm from "./components/IntakeForm.vue";
import JourneyView from "./components/JourneyView.vue";
import MetricsView from "./components/MetricsView.vue";
import RuntimeStage from "./components/RuntimeStage.vue";
import { useTaskStore } from "./stores/task";

const store = useTaskStore();
const metricsVisible = ref(false);

onMounted(() => store.initialize());
</script>

<template>
  <div class="ambient-grid" aria-hidden="true"></div>

  <header class="app-header">
    <a class="brand" href="/" aria-label="行迹首页">
      <span class="brand-mark">行</span>
      <span><b>行迹</b><small>AGENTIC TRAVEL</small></span>
    </a>
    <div class="header-actions">
      <button
        class="metrics-toggle"
        :class="{ active: metricsVisible }"
        type="button"
        @click="metricsVisible = !metricsVisible"
      >
        运行指标
      </button>
      <div class="runtime-chip" :class="{ online: store.healthOnline }">
        <i></i>
        <span id="healthText">
          {{ store.healthOnline ? `${store.config?.model} · ${store.config?.protocol}` : "运行时离线" }}
        </span>
      </div>
    </div>
  </header>

  <main>
    <MetricsView v-if="metricsVisible" />
    <template v-else>
      <IntakeForm v-if="store.intakeVisible" />
      <template v-else-if="store.task">
        <JourneyView v-if="store.journeyReady && !store.runtimeVisible" />
        <RuntimeStage v-else />
      </template>
    </template>
  </main>

  <footer>
    <span>LOCAL-FIRST AGENT HARNESS</span>
    <p>API Key 不返回前端 · Trace 与 Checkpoint 写入本地 SQLite</p>
  </footer>
</template>

<style scoped>
.header-actions { display: flex; align-items: center; gap: 10px; }
.metrics-toggle {
  padding: 8px 16px;
  color: #475467;
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 99px;
  cursor: pointer;
  transition: all .15s;
}
.metrics-toggle:hover { border-color: var(--blue); color: var(--blue-dark); }
.metrics-toggle.active { color: #fff; background: var(--text); border-color: var(--text); }
</style>
