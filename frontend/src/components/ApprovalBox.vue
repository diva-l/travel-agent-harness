<script setup lang="ts">
import type { PendingCall } from "../types";

const props = defineProps<{ pendingCall: PendingCall }>();
const emit = defineEmits<{ decide: [callId: string, approved: boolean] }>();
</script>

<template>
  <div id="approvalBox" class="approval-box">
    <p>敏感工具调用等待确认</p>
    <code id="approvalDetail">{{ JSON.stringify(props.pendingCall, null, 2) }}</code>
    <div>
      <button class="ghost-button" type="button" @click="emit('decide', pendingCall.call_id, false)">拒绝</button>
      <button class="small-button" type="button" @click="emit('decide', pendingCall.call_id, true)">允许执行</button>
    </div>
  </div>
</template>
