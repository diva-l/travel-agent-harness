<script setup lang="ts">
import { capabilityHints, capabilityLabels } from "../labels";
import type { RuntimeConfig } from "../types";
import ToolRegistry from "./ToolRegistry.vue";

defineProps<{ config: RuntimeConfig }>();
</script>

<template>
  <section class="runtime-manifest" aria-label="Harness 运行时清单">
    <div class="manifest-header"><span>LIVE RUNTIME MANIFEST</span><i></i></div>
    <p class="manifest-explainer">
      这里展示的是 Harness（Agent 运行时骨架）的实时配置：专门训练的规划模型在这套骨架里循环
      「决策 → 校验 → 调工具 → 观察」，每一步都被约束、留痕、可恢复。模型负责想，Harness 负责让它不出格。
    </p>
    <div class="runtime-identity">
      <div><small>运行时内核</small><strong id="engineName">{{ config.runtime.engine }}</strong></div>
      <div>
        <small>通信协议 / 模型</small>
        <strong id="protocolName">{{ config.protocol }} / {{ config.model }}</strong>
      </div>
      <div><small>状态存储</small><strong id="storeName">{{ config.runtime.state_store }}</strong></div>
    </div>
    <div class="runtime-flow" aria-label="Harness 执行链">
      <span title="模型给出下一步决策">模型决策</span><b>→</b>
      <span title="按契约校验参数">参数校验</span><b>→</b>
      <span title="调用受管工具">工具执行</span><b>→</b>
      <span title="把证据写回上下文">观察反馈</span>
    </div>
    <div id="capabilityStrip" class="capability-strip">
      <span v-for="name in config.capabilities" :key="name" :title="capabilityHints[name] || ''">
        {{ capabilityLabels[name] || name }}
      </span>
    </div>
    <details class="registry-preview" open>
      <summary>
        <span>工具注册表</span>
        <b id="toolCount">{{ config.tools.length }} CONTRACTS · 契约锁定</b>
      </summary>
      <ToolRegistry id="toolRegistryPreview" :tools="config.tools" />
    </details>
  </section>
</template>
