# 设计来源、采纳边界与检索补充

本文说明设计来源，避免把"参考过概念"误写成"原创了所有思想"。当前实现为独立编写，没有复制任何第三方项目的源码。

## 企业内部 Agent 后端工程的采纳边界

仅参考了一个企业内部模型管理工程的通用后端做法：FastAPI 应用/路由分层、Pydantic 请求契约、健康检查、SQLite 连接生命周期、HTTP 状态码映射和前后端 API 边界。本项目没有搬入该工程的认证用户体系、业务规则、内部地址、日志格式或任何真实数据，也没有复制其页面视觉。

这些能力被放在 `api/` 薄层，任务执行仍全部回到 `AgentRuntime`。参考后端增强的是"怎么对外提供服务"，没有替换 Harness 的状态机、Tool Registry、Trace、Checkpoint 或预算控制。

## Agentic RL 训练项目的采纳边界

本项目的 Planner 侧配合一个独立训练领域的旅行规划模型（训练工程不在本仓库内）：

- 已采纳：旅行规划角色、工具优先、`<think>` 后二阶段动作、`<tool_call>` / `<answer>` 互斥、未取得成功工具事实前禁止收口、合法 JSON 参数、当前日期与最大工具轮次；在线注册表保留训练侧约定的 8 个工具名与参数契约，避免模型上线后出现工具分布漂移。
- 接口适配：Tagged Adapter 保留标签协议并把 Observation 转换成 `<tool_response>`；Native Adapter 改用供应商原生 Function Calling，最终结果放普通 assistant content。改变的是服务接口，不是 Planner 的决策约束。
- 新增而非沿用：Report Model 的 JSON Prompt、`normalize_report()` 校验和交互地图属于本项目的在线产品化层；训练模型仍只输出规划文本。
- 已由 Harness 自身覆盖：工具优先证据门禁、无效工具错误回填、有界循环、重复调用阻断、完整轨迹与明确终止状态。
- 明确不迁入本仓库：数据构建、教师蒸馏、SFT/GRPO 等训练流程、训练调度及权重同步。这些属于训练侧职责；本仓库只消费训练产物（权重），通过 vLLM 的 OpenAI-compatible API 接入。

## 设计要点对照

| 设计要点 | 本项目如何落地 | 代码位置 |
|---|---|---|
| 统一任务状态 + 有界 Agent Loop | `TaskStatus` 统一任务生命周期；Runtime 限制步数、时间、Token、工具调用数 | `models.py`、`runtime.py` |
| Checkpoint、重试、断点续跑 | SQLite 保存任务和历史 Checkpoint；模型请求指数退避；提供 `resume` | `store.py`、`runtime.py`、CLI |
| 全链路 Trace 与失败归因 | 记录模型轮次、工具调用、状态迁移、预算耗尽和失败原因，并做密钥脱敏 | `store.py`、`redaction.py` |
| Evaluation Harness | 固定 JSONL 用例，统计完成率、必需工具覆盖率、工具错误率、步数和 Token | `evaluation.py`、`evals/cases.jsonl` |
| 格式 / 证据门禁 | 工具参数做 Schema 校验；未成功调用工具时拒绝最终回答；工具结果必须带数据来源标记 | `tools.py`、`runtime.py` |
| 上下文与预算管理 | 当前落地步数、时间、Token、工具调用四类预算 | `config.py`、`runtime.py` |
| 人工审批 | 对副作用工具支持暂停、批准/拒绝、继续执行 | `runtime.py`、CLI |

## 暂时没有做的点

- 多 Agent 角色分工（Critic / Reflection 等）：MVP 先验证单 Agent Runtime；没有证据时堆角色只会增加延迟。
- Redis Streams、死信队列、PostgreSQL、多租户：当前本地单进程验证用 SQLite 足够；服务化之后再引入。
- Prompt / 工具集自动进化：容易出现"用测试集优化"的数据泄漏；先做冻结评测、版本记录和人工晋级。
- 三层 Memory：将在真实多会话需求和数据隔离规则明确后实现，当前只保留任务消息和 Checkpoint。

## 网上检索后补充的点

1. **工具级 Guardrail。** 不能只在 Agent 输入/最终输出处检查；每次工具执行前后都需要校验。本项目在 `ToolSpec` 上提供输入/输出 Guardrail。
2. **人工审批要可序列化和恢复。** 副作用工具应暂停到 `waiting_approval`，审批绑定具体 `call_id`，再从持久化状态继续。本项目已实现最小闭环。
3. **Trace 隐私控制。** 全量可观测不等于无条件保存敏感数据；本项目不记录请求 Header，递归遮盖疑似 Key，并允许关闭 Payload Trace。
4. **确定性编排测试。** Runtime 单测不应每次都付费调用模型；`ScriptedModel` 固定输出工具调用和最终回答，用于验证状态机、门禁、审批和 Checkpoint。
5. **Checkpoint 时间旅行。** 除了断点续跑，还应从历史状态 Fork 新任务，比较新模型/新 Prompt 的后续轨迹。本项目提供 `fork`，后续可补自动差异报告。
6. **后端与运行时分层。** DeepSeek 和 vLLM 都能提供 OpenAI-compatible API，因此将模型调用封装成 `ModelBackend`，自训练模型上线后不改 Runtime。
7. **模型与配置指纹。** 每个任务保存 `base_url + model` 指纹，避免回放时不知道原任务用的哪套服务；后续应加入 Prompt、工具 Schema 和权重哈希。
8. **副作用幂等。** 真实预订/下单工具需要 `idempotency_key` 与补偿动作。当前没有接真实写操作，所以只实现审批接口，未假装已解决分布式幂等。
9. **本地模型量化回归门禁。** 自训练模型在有限显存环境中可能需要量化；量化后要在同一冻结评测集上比较工具调用有效率、完成率与延迟，再允许替换在线模型。

## 官方资料

- DeepSeek Chat Completions 与 Tool Calls：<https://api-docs.deepseek.com/api/create-chat-completion/>、<https://api-docs.deepseek.com/guides/tool_calls/>
- OpenAI Agents SDK Human-in-the-loop：<https://openai.github.io/openai-agents-python/human_in_the_loop/>
- OpenAI Agents SDK Guardrails 与 Tracing：<https://openai.github.io/openai-agents-python/guardrails/>、<https://openai.github.io/openai-agents-python/tracing/>
- LangGraph Checkpoint Time Travel：<https://docs.langchain.com/oss/python/langgraph/use-time-travel>
- vLLM OpenAI-compatible Server 与量化：<https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/>、<https://docs.vllm.ai/en/latest/features/quantization/>
- 高德开放平台 Web 服务 API：<https://lbs.amap.com/api/webservice/guide/api/newpoisearch>、<https://lbs.amap.com/api/webservice/guide/api-advanced/new_route>
- GitHub 仓库限制：<https://docs.github.com/en/repositories/creating-and-managing-repositories/repository-limits>
