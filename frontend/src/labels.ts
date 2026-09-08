export const statusLabels: Record<string, string> = {
  created: "已创建",
  running: "执行中",
  waiting_approval: "待审批",
  completed: "已完成",
  exhausted: "预算耗尽",
  failed: "执行失败",
};

export const capabilityLabels: Record<string, string> = {
  bounded_loop: "有界循环",
  schema_guardrails: "参数校验",
  checkpoint_resume: "断点续跑",
  trace_stream: "全链路追踪",
  human_approval: "人工审批",
  checkpoint_fork: "历史分叉",
  offline_evaluation: "离线评测",
  runtime_metrics: "运行指标聚合",
  optional_api_auth: "可选接口鉴权",
  planner_report_separation: "双模型分工",
  interactive_route_report: "交互路线",
};

/** Plain-language explanation shown with each capability chip. */
export const capabilityHints: Record<string, string> = {
  bounded_loop: "限制步数、时长、Token 与工具调用次数，防止 Agent 失控空转",
  schema_guardrails: "每次工具调用前后都按契约校验参数与返回值",
  checkpoint_resume: "每个关键状态写入 SQLite，崩溃后可从断点继续",
  trace_stream: "模型决策、工具调用、状态迁移全部留痕并可实时查看",
  human_approval: "有副作用的操作先暂停，等人确认后再执行",
  checkpoint_fork: "从任意历史状态分叉出新任务，用于复跑与对比",
  offline_evaluation: "固定用例集离线评测，不依赖在线服务",
  runtime_metrics: "从 SQLite 聚合任务成功率、步数、Token 与工具使用分布",
  optional_api_auth: "设置 TRAVEL_HARNESS_API_TOKEN 后 API 需 Bearer 凭证访问",
  planner_report_separation: "规划与报告由两个模型分工，报告不得篡改规划",
  interactive_route_report: "规划结果渲染为按日组织的路线时间线与预算明细",
};

export const eventLabels: Record<string, string> = {
  task_created: "任务已进入 Harness",
  state_transition: "运行状态变化",
  model_request: "向模型发起推理",
  model_response: "收到模型决策",
  model_error: "模型请求重试",
  model_failed: "模型请求失败",
  tool_started: "开始调用工具",
  tool_succeeded: "工具返回证据",
  tool_failed: "工具调用失败",
  tool_blocked: "工具调用被门禁阻止",
  approval_required: "敏感操作等待审批",
  approval_granted: "人工已批准",
  approval_rejected: "人工已拒绝",
  final_rejected: "最终回答未通过证据门禁",
  budget_exhausted: "运行预算已耗尽",
  runtime_failed: "运行时异常",
  task_forked: "从 Checkpoint 创建分支",
  report_started: "Report Model 开始结构化",
  report_completed: "交互路线报告已生成",
  report_failed: "报告生成失败，保留原始规划",
};

export const categoryLabels: Record<string, string> = {
  transport: "交通",
  food: "餐饮",
  stay: "住宿",
  sight: "景点",
  activity: "活动",
  other: "地点",
};

export const categoryIcons: Record<string, string> = {
  transport: "🚄",
  food: "🍜",
  stay: "🏨",
  sight: "🏛️",
  activity: "🚶",
  other: "📍",
};

export const categoryColors: Record<string, string> = {
  transport: "#5a91ff",
  food: "#e07840",
  stay: "#7a5af5",
  sight: "#19a98c",
  activity: "#d64f8a",
  other: "#66788c",
};

export function summarizePayload(kind: string, payload: Record<string, unknown> | null): string {
  if (!payload) return "";
  if (kind === "model_request") {
    return `上下文 ${payload.message_count} 条消息 · 可用工具 ${(payload.available_tools as unknown[])?.length || 0} 个`;
  }
  if (kind === "model_response") {
    const usage = payload.usage as { completion_tokens?: number } | undefined;
    const calls = (payload.tool_calls as unknown[])?.length || 0;
    return calls
      ? `决定调用 ${calls} 个工具 · 输出 ${usage?.completion_tokens || 0} tokens`
      : `输出最终规划 · ${usage?.completion_tokens || 0} tokens`;
  }
  if (payload.tool) return `${payload.tool}${payload.error ? ` · ${payload.error}` : ""}`;
  if (payload.to) return `进入「${statusLabels[String(payload.to)] || payload.to}」状态`;
  if (payload.reason) return String(payload.reason);
  if (payload.model) return String(payload.model);
  return JSON.stringify(payload).slice(0, 150);
}

export function eventCategory(kind: string): string {
  if (kind.startsWith("model_")) return "model";
  if (kind.startsWith("report_")) return kind === "report_failed" ? "error" : "model";
  if (kind.includes("failed") || kind.includes("exhausted")) return "error";
  if (kind.includes("rejected") || kind.includes("blocked") || kind.startsWith("approval_"))
    return "guardrail";
  return "tool";
}
