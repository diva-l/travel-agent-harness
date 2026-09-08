<script setup lang="ts">
import { nextTick, ref, watch } from "vue";

import { eventCategory, eventLabels, summarizePayload } from "../labels";
import type { TraceEvent } from "../types";

const props = defineProps<{ events: TraceEvent[] }>();

const listEl = ref<HTMLOListElement | null>(null);

watch(
  () => props.events.length,
  async () => {
    await nextTick();
    if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight;
  },
);
</script>

<template>
  <ol id="traceList" ref="listEl" class="trace-list">
    <li v-for="event in events" :key="event.id" :class="eventCategory(event.kind)">
      <i class="trace-dot"></i>
      <div>
        <b>{{ eventLabels[event.kind] || event.kind }}</b>
        <p>{{ summarizePayload(event.kind, event.payload) }}</p>
        <details>
          <summary>查看结构化事件</summary>
          <pre>{{ JSON.stringify(event.payload, null, 2) }}</pre>
        </details>
      </div>
      <time>S{{ event.step }}</time>
    </li>
  </ol>
</template>
