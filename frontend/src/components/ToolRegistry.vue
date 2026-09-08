<script setup lang="ts">
import type { ToolCatalogEntry } from "../types";

defineProps<{ tools: ToolCatalogEntry[] }>();
</script>

<template>
  <div class="tool-registry">
    <details v-for="tool in tools" :key="tool.name" class="tool-contract">
      <summary>
        <strong>{{ tool.name }}</strong>
        <span>{{ tool.requires_approval ? "HITL REQUIRED" : "READ ONLY" }}</span>
      </summary>
      <p>{{ tool.description }}</p>
      <code>{{ JSON.stringify(tool.parameters, null, 2) }}</code>
    </details>
  </div>
</template>
