<script setup lang="ts">
import { computed } from "vue";

import type { CheckpointInfo } from "../types";

const props = defineProps<{ checkpoints: CheckpointInfo[] }>();
const emit = defineEmits<{ fork: [checkpointSeq: number] }>();

const ordered = computed(() => [...props.checkpoints].reverse());

function formatTime(epoch: number): string {
  return new Date(epoch * 1000).toLocaleTimeString("zh-CN");
}
</script>

<template>
  <ol id="checkpointList" class="checkpoint-list">
    <li v-for="checkpoint in ordered" :key="checkpoint.seq">
      <span class="cp-index">CP{{ checkpoint.seq }}</span>
      <div>
        <strong>{{ String(checkpoint.status).toUpperCase() }} · STEP {{ checkpoint.step }}</strong>
        <small>{{ formatTime(checkpoint.created_at) }}</small>
      </div>
      <button type="button" class="fork-button" @click="emit('fork', checkpoint.seq)">FORK ↗</button>
    </li>
  </ol>
</template>
