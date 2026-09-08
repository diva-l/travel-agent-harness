# Claim 证据台账

公开简历、README 和面试回答只使用已经运行验证的 Claim。没有测量的数据写为“待验证”，不做插值或估算。

| 可表述 Claim | 证据位置 | 当前状态 |
|---|---|---|
| 实现步数 / 时间 / Token / 工具调用四类预算控制 | `runtime.py`、`test_runtime.py` | 17 项自动化测试全部通过（2026-09-04） |
| 实现 SQLite Checkpoint、任务恢复和历史分叉 | `store.py`、`runtime.py`、单测 | 离线测试通过；Windows 连接关闭问题已修复 |
| 实现工具白名单、参数 Schema 校验与输入/输出 Guardrail | `tools.py`、单测 | 离线测试通过 |
| 实现副作用工具人工审批后恢复执行 | `runtime.py`、审批单测 | 离线测试通过；未接真实预订接口 |
| 接入 DeepSeek 跑通真实 Tool Calling | `verification.md`、本地 gitignored Trace DB | 单次 Smoke Run 通过（2026-09-04） |
| FastAPI + SSE + Runtime Inspector 复用同一 Runtime | `api/`、`web/`、`test_api.py`、浏览器截图 | 状态机、预算、门禁、Trace、3 个 Checkpoint 与 8 份工具契约均通过浏览器验收 |
| 从 Web 查看 Checkpoint 并创建历史 Fork | `/checkpoints`、`/fork`、`test_api.py` | API 测试验证历史 Checkpoint 可创建独立 `created` 任务；页面提供显式 Fork 操作 |
| 兼容训练模型的标签协议 | `protocol.py`、`test_protocol.py` | `<tool_call>` / `<answer>` / `<tool_response>` 契约测试通过；尚未加载本地权重实测 |
| Planner 与 Report Model 职责分离 | `prompts.py`、`reporting.py`、`TaskService._run_pipeline()` | API 测试验证原始 answer 保留、报告单独生成并记录 Trace |
| 地图优先交互报告 | `frontend/src/components/`、浏览器回归 | 日期筛选、8 个路线节点、节点详情和缩放通过无头 Edge 验收 |
| DeepSeek 跑通 Planner → Report 双模型链路 | `verification.md`、本地 gitignored Trace DB | 单次任务 4 轮、12/12 工具成功、0 Schema 违规，生成 2 天 8 站报告 |
| 工具数据源可在演示与真实高德 API 间切换且契约不变 | `tools.py`、`amap.py`、`test_amap.py` | 离线解析测试通过（8 项）；2026-09-06 vLLM 闭环中高德真实数据在线跑通（含一次 QPS 限流恢复） |
| 接入训练后模型并完成同协议替换 | vLLM 启动记录 + 冻结评测对比 | 2026-09-06 已在 RTX 5090 部署 checkpoint-150 并跑通 tagged 闭环；期间修复三处训练契约漂移（search/around_search 约束、system prompt、tool_response 格式）；n=2 评测对比见 verification.md |
| 准确率、完成率或性能提升百分比 | 固定评测集报告 | 仅完成 n=2 对比（api 2/2，vllm 1/2），样本太小，禁止写入简历 |

## 不进入 GitHub 的内容

- `.env`、API Key、请求 Authorization Header。
- 任何第三方项目的源码、数据或内部文档。
- 训练 Checkpoint、Adapter、基础模型权重；它们应放模型仓库或对象存储，并先确认许可证。
- 未经复现实验得到的分数、提升率或占位数字。
