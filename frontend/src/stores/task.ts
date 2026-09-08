import { defineStore } from "pinia";

import { api } from "../api";
import type {
  CheckpointInfo,
  RuntimeConfig,
  TaskView,
  TraceEvent,
  TripPlanPayload,
} from "../types";

interface TaskState {
  config: RuntimeConfig | null;
  healthOnline: boolean;
  task: TaskView | null;
  traceEvents: TraceEvent[];
  checkpoints: CheckpointInfo[];
  activeDay: number;
  approvalObserved: boolean;
  formError: string;
  intakeVisible: boolean;
  /** true = 监控视图在主屏；报告就绪后自动切到行程视图。 */
  runtimeVisible: boolean;
}

let eventSource: EventSource | null = null;
let lastEventId = 0;

export const useTaskStore = defineStore("task", {
  state: (): TaskState => ({
    config: null,
    healthOnline: false,
    task: null,
    traceEvents: [],
    checkpoints: [],
    activeDay: 0,
    approvalObserved: false,
    formError: "",
    intakeVisible: true,
    runtimeVisible: true,
  }),

  getters: {
    reportWorking(state): boolean {
      return (
        state.task?.status === "completed" &&
        ["pending", "running"].includes(state.task.report_status)
      );
    },
    submitDisabled(): boolean {
      if (!this.task) return false;
      return (
        this.task.status === "running" ||
        this.task.status === "created" ||
        this.reportWorking
      );
    },
    journeyReady(state): boolean {
      return state.task?.report_status === "completed" && state.task.report !== null;
    },
  },

  actions: {
    async initialize() {
      try {
        const [, config] = await Promise.all([
          api<{ status: string }>("/api/health"),
          api<RuntimeConfig>("/api/config"),
        ]);
        this.config = config;
        this.healthOnline = true;
      } catch {
        this.healthOnline = false;
      }
      const taskId = new URLSearchParams(window.location.search).get("task");
      if (!taskId) return;
      try {
        const task = await api<TaskView>(`/api/plans/${encodeURIComponent(taskId)}`);
        this.beginTask(task);
      } catch (error) {
        this.formError = `无法恢复任务：${(error as Error).message}`;
      }
    },

    async submitPlan(payload: TripPlanPayload) {
      this.formError = "";
      try {
        const task = await api<TaskView>("/api/plans", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        this.beginTask(task);
      } catch (error) {
        this.formError = (error as Error).message;
      }
    },

    beginTask(task: TaskView) {
      const isNew = this.task?.task_id !== task.task_id;
      this.task = task;
      if (isNew) {
        const url = new URL(window.location.href);
        url.searchParams.set("task", task.task_id);
        window.history.replaceState({}, "", url);
        lastEventId = 0;
        this.traceEvents = [];
        this.checkpoints = [];
        this.approvalObserved = false;
        this.activeDay = 0;
        this.intakeVisible = false;
        this.formError = "";
      }
      // 恢复一个已带报告的任务时直接进行程视图；运行中的任务先看监控。
      this.runtimeVisible = !this.journeyReady;
      this.loadCheckpoints();
      // Always connect: the SSE stream replays persisted trace history first,
      // then closes itself once the task settles.
      this.connectEvents();
    },

    connectEvents() {
      if (!this.task) return;
      eventSource?.close();
      eventSource = new EventSource(
        `/api/plans/${this.task.task_id}/events?after=${lastEventId}`,
      );
      eventSource.addEventListener("trace", (event) => {
        const item = JSON.parse((event as MessageEvent).data) as TraceEvent;
        lastEventId = Math.max(lastEventId, item.id);
        if (item.kind.startsWith("approval_")) this.approvalObserved = true;
        this.traceEvents.push(item);
      });
      eventSource.addEventListener("state", (event) => {
        const task = JSON.parse((event as MessageEvent).data) as TaskView;
        this.applyTask(task);
        if (this.isStreamSettled(task)) eventSource?.close();
      });
      eventSource.onerror = () => eventSource?.close();
    },

    isStreamSettled(task: TaskView): boolean {
      if (["failed", "exhausted", "waiting_approval"].includes(task.status)) return true;
      return (
        task.status === "completed" && !["pending", "running"].includes(task.report_status)
      );
    },

    applyTask(task: TaskView) {
      const previousSeq = this.task?.checkpoint_seq;
      this.task = task;
      if (task.checkpoint_seq !== previousSeq) this.loadCheckpoints();
      if (task.error) this.formError = task.error;
      if (task.report_status === "failed" && task.answer) {
        this.formError = `路线报告生成失败：${task.report_error || "可查看 Planner 原始结果"}`;
      }
      // 报告一就绪就把主屏让给行程视图，监控退为可切换视图。
      if (this.journeyReady) this.runtimeVisible = false;
    },

    showRuntime() {
      this.runtimeVisible = true;
    },

    showJourney() {
      if (this.journeyReady) this.runtimeVisible = false;
    },

    async loadCheckpoints() {
      if (!this.task) return;
      try {
        const result = await api<{ checkpoints: CheckpointInfo[] }>(
          `/api/plans/${this.task.task_id}/checkpoints`,
        );
        if (this.task) this.checkpoints = result.checkpoints;
      } catch (error) {
        this.formError = (error as Error).message;
      }
    },

    async forkFrom(checkpointSeq: number) {
      if (!this.task) return;
      if (!window.confirm(`从 CP${checkpointSeq} 创建并运行一条新 Episode？原任务不会被修改。`))
        return;
      try {
        const task = await api<TaskView>(`/api/plans/${this.task.task_id}/fork`, {
          method: "POST",
          body: JSON.stringify({ checkpoint_seq: checkpointSeq, run: true }),
        });
        this.beginTask(task);
      } catch (error) {
        this.formError = (error as Error).message;
      }
    },

    async decideApproval(callId: string, approved: boolean) {
      if (!this.task) return;
      try {
        const task = await api<TaskView>(
          `/api/plans/${this.task.task_id}/approvals/${callId}`,
          { method: "POST", body: JSON.stringify({ approved }) },
        );
        this.applyTask(task);
        this.connectEvents();
      } catch (error) {
        this.formError = (error as Error).message;
      }
    },

    newPlan() {
      eventSource?.close();
      eventSource = null;
      this.task = null;
      this.traceEvents = [];
      this.checkpoints = [];
      this.activeDay = 0;
      this.approvalObserved = false;
      this.formError = "";
      this.intakeVisible = true;
      this.runtimeVisible = true;
      window.history.replaceState({}, "", window.location.pathname);
    },
  },
});
