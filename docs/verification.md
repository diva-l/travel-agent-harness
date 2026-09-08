# 验证记录

## 2026-09-06：vLLM 自托管部署与训练契约对齐（RTX 5090 / CUDA 13.0）

环境：AutoDL RTX 5090 32GB，驱动 CUDA 13.0，Python 3.12，vLLM 0.28.0，
checkpoint 为本机 `output/grpo_parser_aligned_run/v8-20260514-140934/checkpoint-150`（Qwen3 4B bf16）。

部署中发现并修复的问题：

1. **FlashInfer 采样器不兼容 sm_120**：vLLM 0.28 默认启用 flashinfer 采样，其 JIT 检查
   在 RTX 5090（sm_120）上误报 "requires GPUs with sm75 or higher" 导致引擎启动失败。
   以 `VLLM_USE_FLASHINFER_SAMPLER=0` 回退 PyTorch 原生采样后正常。
2. **工具契约漂移**：在线注册表给 `search.query` 加了 `maxItems: 8`、给 `around_search.radius`
   加了 `minimum/maximum`，训练 Prompt 均无此约束。训练后的模型一次发 16 条 query 被 Schema
   拒绝，且不会拆分重试，原样重发直到重复阻断。已从注册表删除两处添加，恢复与训练 Prompt
   完全一致（tools.py；48 项测试通过）。
3. **System Prompt 改写漂移**：tagged 模式的在线 Prompt 是训练 Prompt 的改写版，模型出现
   原地重复同一 `route_planning` 调用的退化行为。已将 tagged 分支替换为训练 Prompt 原文
   （仅日期与最大轮次保持动态），并将 `<tool_response name="...">` 改为训练格式的裸
   `<tool_response>`，成功结果拆包 `{"ok","result"}` 信封、失败渲染为 `TOOL_ERROR: ...`
   （prompts.py、protocol.py）。

修复后真实闭环（任务 `19f2386007c34c31840e831cb8cd84de`，Planner=checkpoint-150 经 vLLM，
工具=高德真实数据 + Firecrawl）：

| 指标 | 单次结果 |
|---|---:|
| Planner 状态 | completed |
| 模型轮次 | 5 |
| 工具调用 / 成功 | 20 / 19 |
| 累计 Token | 36479 |
| 墙钟时间 | 26.3 秒 |

唯一一次工具错误为高德 `CUQPS_HAS_EXCEEDED_THE_LIMIT`（单轮 10 个并行 poi_search 触发
QPS 限流），模型用其余 9 个结果继续，未影响收口。

固定评测集 `evals/cases.jsonl`（n=2）对比，同日同工具链：

| 模式 | 完成率 | 必需工具覆盖率 | 工具错误率 | 平均步数 | 总 Token |
|---|---|---|---|---|---|
| api（DeepSeek 基线） | 2/2 | 1.0 | 0.0 | 4.0 | 39437 |
| vllm（checkpoint-150） | 1/2 | 1.0 | 0.067 | 7.5 | 132625 |

证据边界：n=2 只能说明链路在两种模式下都可用，不能推断总体准确率差距。
vllm 模式 `weather-route` 用例中模型连续 5 次 visit 长正文网页（单页截断 6000 字），
未判断"信息已足够"，累计 Token 超出 80000 预算；这属于待训练侧改进的收口行为，
非 Harness 缺陷。vllm 预设的 `max_total_tokens` 已因此从 24000 调整为 80000
（config.py；本地模型单轮大量并行调用会反复重读全文）。

## 2026-09-04：DeepSeek Tool-Calling Smoke Run（v0.2 历史基线）

目标：验证“模型决策 → Harness 校验 → 工具执行 → Observation 回传 → 预算计算 → 最终回答”能完整闭环。API Key 从本地 `.env` 读取，没有写入命令输出、代码、Trace 或本文。下列工具名属于 v0.2 基线，v0.3 已改为与 Agentic RL Prompt 对齐的 8 工具契约。

输入任务：规划 2026-10-02 从杭州到上海的两日行程，总预算 1800 元；核对交通、住宿和天气，完成预算汇总，并说明证据边界。

结果：

| 指标 | 单次结果 |
|---|---:|
| 状态 | completed |
| 模型轮次 | 3 |
| 工具调用 | 5 |
| 成功工具调用 | 5 |
| 工具错误 | 0 |
| 累计 Token | 4963 |
| 墙钟时间 | 13.257 秒 |

工具轨迹：

1. `search_transport`：查询杭州到上海交通演示数据。
2. `search_stays`：查询上海一晚住宿演示数据。
3. `get_weather`：分别查询两天的天气演示数据。
4. `calculate_itinerary_budget`：以模型选择的交通、住宿和逐日开销计算 1801 元总额。

最终回答识别出方案超过 1800 元预算 1 元，给出压缩住宿或逐日开销的调整方向，并明确交通、住宿、天气均为 `demo_fixture`，不能作为真实预订依据。

证据边界：这只是一次真实模型 API + 演示工具的 Smoke Run，只能证明链路在该用例上跑通；不能据此声称完成率、准确率、召回率或性能提升。批量指标必须运行固定评测集后再填写。

## 2026-09-04：Web / API 端到端验证

目标：确认出行规划页面不是与 Harness 分离的展示层，而是通过 FastAPI 和 SSE 直接驱动同一个 `AgentRuntime`。

- 使用真实 DeepSeek 配置从网页提交“上海到杭州 3 天、2 人、预算 3000 元”的结构化需求。
- 任务经 `POST /api/plans` 创建，由后台 Worker 调用 Runtime；网页通过 SSE 接收真实 Trace 和状态。
- 抽取其中一次完整运行：3 个模型轮次、7 次工具调用、7 次成功、0 次工具错误、6233 Token、21.018 秒，最终状态 `completed`。
- v0.2 无头 Edge 基线成功渲染桌面和移动布局；当时回归渲染 9 条 Trace、3 个 Checkpoint、状态机、四类预算、3 类门禁及 4 份工具契约，最终答案 101 字，控制台错误为 0。
- `/api/config` 只返回模型名、协议和预算，不返回 API Key；密钥仍只从仓库外的 `.env` 注入。

截图位于 `artifacts/ui-desktop.png`、`artifacts/ui-completed.png` 和 `artifacts/ui-mobile.png`。浏览器回归使用 `ScriptedModel`，不产生外部 API 费用；真实模型结果与确定性 UI 回归分开记录。Runtime Inspector 中的数据来自 `/api/config`、任务状态、Trace 和 Checkpoint API，不是写死在页面中的演示数字。

## 2026-09-04：Planner → Report → Interactive Route 回归

- 自动化测试共 17 项，覆盖 Runtime、Tagged 协议、8 工具契约、POI/路线几何、Report JSON 校验、API Pipeline、Checkpoint/Fork 与密钥脱敏。
- 确定性 Web 回归验证了 Planner 原始文本与 Report JSON 分离；Trace 出现 `report_started` 和 `report_completed`，而不是由 JavaScript 伪造报告完成状态。
- 结果页按地图优先渲染，共 3 天、8 个节点、4 个日期筛选按钮；节点详情点击与 SVG 缩放生效，浏览器控制台错误为 0。
- 当前地图是路线关系可视化，不宣称逐向导航；使用真实地图 API 前继续保留显式数据边界。

真实 DeepSeek 闭环复跑（任务 `d19201f340a349869c3eab3200906494`）：

| 指标 | 单次结果 |
|---|---:|
| Planner 状态 | completed |
| Report 状态 | completed |
| Planner 模型轮次 | 4 |
| 工具调用 / 成功 | 12 / 12 |
| Schema 违规 | 0 |
| Planner 累计 Token | 10793 |
| Planner 墙钟时间 | 31.1 秒 |
| 报告规模 | 2 天 / 8 个路线节点 |
| 地图模式 | schematic |

首跑使用 12 次“物理工具调用”硬上限时，模型在两轮并行请求 14 个工具，Harness 于第 12 次后正确终止并跳过 Report。核对训练工程后确认其 13 指的是“模型—工具交互轮次”，并非单个并行 Tool Call 数；因此 v0.3 将默认预算对齐为 13 轮，同时保留 40 次物理调用硬上限与第 4 次完全相同调用阻断。复跑随后完整收口。该记录反映一次运行，不代表总体准确率。
