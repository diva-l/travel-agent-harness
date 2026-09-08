<script setup lang="ts">
import { onMounted } from "vue";

import IntakeForm from "./components/IntakeForm.vue";
import JourneyView from "./components/JourneyView.vue";
import RuntimeStage from "./components/RuntimeStage.vue";
import { useTaskStore } from "./stores/task";

const store = useTaskStore();

onMounted(() => store.initialize());
</script>

<template>
  <div class="ambient-grid" aria-hidden="true"></div>

  <header class="app-header">
    <a class="brand" href="/" aria-label="行迹首页">
      <span class="brand-mark">行</span>
      <span><b>行迹</b><small>AGENTIC TRAVEL</small></span>
    </a>
    <div class="runtime-chip" :class="{ online: store.healthOnline }">
      <i></i>
      <span id="healthText">
        {{ store.healthOnline ? `${store.config?.model} · ${store.config?.protocol}` : "运行时离线" }}
      </span>
    </div>
  </header>

  <main>
    <IntakeForm v-if="store.intakeVisible" />
    <template v-else-if="store.task">
      <JourneyView v-if="store.journeyReady && !store.runtimeVisible" />
      <RuntimeStage v-else />
    </template>
  </main>

  <footer>
    <span>LOCAL-FIRST AGENT HARNESS</span>
    <p>API Key 不返回前端 · Trace 与 Checkpoint 写入本地 SQLite</p>
  </footer>
</template>
