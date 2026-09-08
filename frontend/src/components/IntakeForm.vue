<script setup lang="ts">
import { reactive, ref } from "vue";

import { useTaskStore } from "../stores/task";
import type { TripPlanPayload } from "../types";

const store = useTaskStore();
const submitting = ref(false);

const form = reactive({
  origin: "上海",
  destination: "杭州",
  start_date: new Date(Date.now() + 86400000 * 7).toISOString().slice(0, 10),
  days: 3,
  budget_cny: 3000,
  travelers: 2,
  pace: "balanced" as TripPlanPayload["pace"],
  preferences: "人文, 美食, 城市漫步",
  notes: "",
});

async function onSubmit() {
  submitting.value = true;
  await store.submitPlan({
    origin: form.origin,
    destination: form.destination,
    start_date: form.start_date,
    days: Number(form.days),
    budget_cny: Number(form.budget_cny),
    travelers: Number(form.travelers),
    pace: form.pace,
    preferences: form.preferences.split(/[,，]/).map((x) => x.trim()).filter(Boolean),
    notes: form.notes,
  });
  submitting.value = false;
}
</script>

<template>
  <section id="intakeStage" class="intake-stage">
    <div class="intake-copy">
      <p>AI TRAVEL PLANNER</p>
      <h1>这次，想去哪里？</h1>
      <p class="intake-sub">
        由一个专门为出行规划训练的模型驱动，运行在受约束的 Agent Harness 里：
        它会真实调用天气、地点、路线等工具查证据，再产出可执行的逐日行程，而不是凭空编一份攻略。
      </p>
    </div>

    <form id="planForm" class="plan-composer" @submit.prevent="onSubmit">
      <div class="route-composer">
        <label>
          <span>出发地</span>
          <input v-model="form.origin" name="origin" required maxlength="80" autocomplete="off" />
        </label>
        <i aria-hidden="true">→</i>
        <label class="destination-field">
          <span>目的地</span>
          <input v-model="form.destination" name="destination" required maxlength="80" autocomplete="off" />
        </label>
        <button class="launch-button" type="submit" aria-label="开始规划" :disabled="submitting || store.submitDisabled">
          <span>开始规划</span><b>↗</b>
        </button>
      </div>

      <details class="trip-options">
        <summary><span>完善行程约束</span><b>日期 · 预算 · 偏好</b><i>＋</i></summary>
        <div class="options-content">
          <div class="form-grid">
            <label>出发日期<input v-model="form.start_date" type="date" name="start_date" required /></label>
            <label>行程天数<input v-model="form.days" type="number" name="days" min="1" max="30" required /></label>
            <label>总预算 / 元<input v-model="form.budget_cny" type="number" name="budget_cny" min="100" required /></label>
            <label>出行人数<input v-model="form.travelers" type="number" name="travelers" min="1" max="20" required /></label>
          </div>
          <fieldset>
            <legend>旅行节奏</legend>
            <div class="segmented">
              <label><input v-model="form.pace" type="radio" name="pace" value="relaxed" /><span>松弛</span></label>
              <label><input v-model="form.pace" type="radio" name="pace" value="balanced" /><span>均衡</span></label>
              <label><input v-model="form.pace" type="radio" name="pace" value="intensive" /><span>紧凑</span></label>
            </div>
          </fieldset>
          <div class="form-grid preference-grid">
            <label>兴趣偏好<input v-model="form.preferences" name="preferences" placeholder="例如：人文、美食、城市漫步" /></label>
            <label>补充要求<textarea v-model="form.notes" name="notes" rows="2" placeholder="例如：不想早起，想住西湖边，尽量少折返"></textarea></label>
          </div>
        </div>
      </details>
      <p id="formError" class="form-error" role="alert">{{ store.formError }}</p>
    </form>

    <div class="intake-features" aria-label="工作方式">
      <article>
        <span>01 · 会查证的规划模型</span>
        <p>规划模型经 Agentic RL 专门训练，自主决定何时查天气、搜地点、比车次、算路线，结论必须有工具证据支撑。</p>
      </article>
      <article>
        <span>02 · Harness 全程约束</span>
        <p>步数、时长、Token、调用次数四项预算兜底；每次调用按契约校验；全程留痕、可断点续跑、可从历史分叉复跑。</p>
      </article>
      <article>
        <span>03 · 可执行的行程产出</span>
        <p>第二个模型把规划结构化为交互地图路线、逐日时间线与预算明细，Planner 原始规划全文也一并呈现。</p>
      </article>
    </div>
  </section>
</template>
