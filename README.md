<p align="center">
  <img src="docs/hero.svg" alt="TravelAgentHarness — 受限、可追溯、可评测的出行规划 Agent Harness" width="100%">
</p>

<p align="center">
  <a href="tests/"><img src="https://img.shields.io/badge/tests-84%20passed-brightgreen" alt="tests"></a>
  <a href="pyproject.toml"><img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="license"></a>
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#评测结果">评测结果</a> ·
  <a href="eval_results/perf/perf_report.md">压测报告</a> ·
  <a href="#文档索引">文档</a>
</p>

**30 秒速览**

- **是什么**：一个真正能用的出行规划 Agent——查天气、搜地点、比车次、算路线，产出有证据支撑的逐日行程与可交互路线图
- **两大核心亮点**：① 规划模型不是调 API，而是我们基于 Qwen3-4B 通过 **SFT + Agentic RL 后训练**自己训练的 **TravelPlanner-4B**；② 模型运行在一套 Harness（运行时约束框架）里，行为有边界、过程可追溯、结果可评测
- **证据**：真实 API 评测打平 DeepSeek（必需工具覆盖率 0.775）· 并发调优吞吐提升 15×，Harness 开销实测≈0 · 84 个单元测试全绿

## 界面预览

![桌面端规划结果](docs/screenshots/desktop_full.png)

更多截图见 [docs/screenshots/](docs/screenshots/)。

## 与传统方案的区别

市面上大多数「AI 旅行规划」项目，本质是写一段 Prompt 直接调用通用大模型 API——模型行为靠提示词约定，没有任何强制手段。本项目的思路完全不同：**模型自己训练，运行时由 Harness 强制约束**。

| 维度 | 传统方案：Prompt + 大模型 API | 本项目：自训练模型 + Harness |
|---|---|---|
| **模型** | 通用大模型（GPT / DeepSeek 等），能力黑盒、行为靠提示词引导 | **TravelPlanner-4B**：Qwen3-4B 基座 → SFT 学习工具调用格式 → Agentic RL 在真实工具循环里优化规划策略 |
| **行为边界** | 无。模型可以无限循环、重复调用、超预算运行 | Harness 有界 Agent Loop：步数 / 墙钟 / 累计 Token / 工具调用数**四项硬预算**，完全相同调用第 4 次直接阻断 |
| **可靠性** | 失败即终止，无中间状态 | 每轮写入 SQLite **Checkpoint**，崩溃可恢复、可从任一 Checkpoint **Fork 复跑** |
| **可观测性** | 黑盒，只看到最终回答 | **全量 Trace**：模型轮次、工具调用、状态迁移、预算消耗、失败原因逐条落库，可回放审计 |
| **输出质量** | 模型说什么就是什么，可能凭空编造 | **证据门禁**：零取证的空想答案直接拒收；Report 阶段做 Schema 校验的结构化转换 |
| **安全** | 无防护 | 工具级输入/输出 **Guardrail**；副作用工具声明 `requires_approval` 后任务暂停，等待**人工审批**放行 |
| **评测** | 凭感觉演示 | 确定性抽样测试集 + 四路对比（基座 / SFT / RL / DeepSeek）+ 逐条数据全部公开可复跑 |

Harness 层做了大量工程工作：双协议解析（原生 Function Calling + 训练模型的 `<tool_call>` 文本协议，带 json_repair 容错）、训练环境对齐开关（把 RL 训练循环的终局规则与观测分布完整搬进运行时，保证评测结论可复现）、模型请求指数退避重试、截断输出自动放大预算重试、高德 QPS 限流吸收、并发 worker 池与有界队列背压——这些都是在真实压测和评测中逐项打磨出来的，见[评测结果](#评测结果)。

## 评测结果

测试集：[eval_results/test_final.jsonl](eval_results/test_final.jsonl) 80 条中确定性抽样 10 条（指纹 `b7d3c735…e0ef4303`）；工具链为真实外部 API；judge=deepseek-v4-flash；运行环境已全开训练对齐开关。脚本与逐条数据在 [eval_results/](eval_results/README.md)。

### 基座 → SFT → RL 四路对比（DeepSeek 作参照）

| 指标 | 基座 Qwen3-4B | SFT 阶段 | **TravelPlanner-4B (RL)** | DeepSeek |
|---|---:|---:|---:|---:|
| 完成率 | 1.0 | 0.8 | 0.9 | 0.9 |
| 必需工具覆盖率 | 0.65 | 0.69 | **0.775** | **0.775** |
| 工具错误率 | 0.02 | 0.34 | 0.12 | 0.05 |
| 重复调用阻断 | 0 | 12 | **1** | 0 |
| RL 混合分（phase3） | 0.419 | 0.223 | **0.480** | 0.461 |
| LLM judge | 0.48 | 0.27 | **0.60** | 0.57 |

**TravelPlanner-4B 是最强本地模型**，与训练侧 80 条 judge 结论一致；全开训练环境对齐开关后，本地 4B 模型在必需工具覆盖率上追平 DeepSeek，评测结论与训练分布严格对齐、可复现。逐条明细：[eval_results/compare_report.md](eval_results/compare_report.md)。

### 工程压测（对齐版，2026-09-08）

| 结论 | 数据 |
|---|---|
| 模型不是瓶颈 | 裸 vLLM c8 首轮 2.3s / 6101 tok/s；Agent 循环下 GPU 均值仅 12~24% |
| 瓶颈在外部工具链 | 工具耗时为模型的 6~9 倍（含训练同款 LLM 模拟器往返） |
| Harness 开销 ≈ 0 | 逐任务「墙钟 − 模型 − 工具」均值 -11%~+0.7% |
| 并发甜区 | c4 吞吐见顶 128 tasks/h；HTTP 路径 worker=16 时 **645 tasks/h、queueing ~3s**（worker=2 时 42.6 tasks/h、queueing 286s，15×） |
| 护栏有效 | schema-echo 场均拦截 0.6 次全部自愈；重复阻断/强制收尾/作答机会按训练契约触发 |
| Prefix cache | 命中率 87.3%，多轮 prefill 的主要减压阀 |

完整报告：[eval_results/perf/perf_report.md](eval_results/perf/perf_report.md)（对齐前旧版归档于 `eval_results/perf/archive_20260906/`）。

## 这是什么

主链路：用户需求 → `AgentRuntime`（唯一执行内核）→ Agentic RL Planner 的工具循环 → Report Model 做证据约束的结构化转换 → 前端交互路线图。Evaluation 只是复用 Runtime 的离线入口，不另造 Agent 逻辑；Web 层没有自己的 Agent Loop，页面上每个运行时状态都能回溯到同一套 Runtime 和 SQLite 状态。

本仓库是 **Harness + 评测 + 产品化前端**，不含训练代码与模型权重（见[模型权重](#模型权重)）。

## 核心特性

**Agent 运行时**
- 双模型协议：原生 Function Calling，或训练模型的 `<tool_call>` / `<answer>` 文本协议（json_repair 容错、`<tool_calls>` 复数包裹、`tool`/`parameters` 变体、未闭合 `<answer>`），Observation 按训练侧格式翻译为 `<tool_response>`（json2md + 头尾各 2500 字截断）
- 有界 Agent Loop：步数 / 墙钟 / 累计 Token / 工具调用数四项预算，阻断第 4 次完全相同调用
- 可靠执行：模型请求指数退避重试；截断输出（finish_reason=length）自动放大预算重试；每轮写入 SQLite Checkpoint，可恢复、可从任一 Checkpoint Fork 复跑
- 可观测性：模型轮次、工具调用、状态迁移、预算消耗、失败原因全量 Trace，自动脱敏疑似 API Key
- 安全边界：工具级输入/输出 Guardrail（含 schema-echo 护栏：模型复读工具定义时收到明确改错消息）；副作用工具可声明 `requires_approval`，任务暂停等待人工审批
- 证据门禁：零取证的空想答案直接拒收

**训练环境对齐**（评测 RL 模型时建议全开，默认关）

把 RL 训练循环的终局规则与观测分布完整搬进 Harness：

| 开关 | 作用 |
|---|---|
| `FORCE_ANSWER_AFTER_STEPS=12` | 第 12 轮注入训练原文强制收尾消息 |
| `REPEAT_ANSWER_CHANCE=true` | 重复循环时给一次强制作答机会（训练原文） |
| `MAX_TOOL_OUTPUT_CHARS=5000` | tool_response 截断长度（json2md 头+尾） |
| `VISIT_EXTRACTOR=true` | visit 页面由 LLM 按训练 EXTRACTOR_PROMPT 提炼 |
| `TICKET_SIMULATOR=true` | 火车/航班用训练同款 LLM 模拟器（逐字 prompt） |
| `TRAINING_TOOL_FORMAT=true` | search/visit 观测用训练侧文本格式 |
| `CURRENT_DATE=...` | 固定 Planner Prompt 里的当前日期（评测用） |

解析失败/空输出时模型收到训练原文引导消息并被重问，不再静默重采样。

**数据与产品化**
- 8 个工具契约与训练 Prompt 逐字一致（search / visit / weather_search / flights_search / train_tickets_search / poi_search / around_search / route_planning），返回值带 `data_source` 标记
- 可切换数据源：离线演示 fixtures ↔ 高德 Web 服务（真实地理数据）+ Firecrawl（真实检索）；高德并发 QPS 限流（infocode 10021）由 provider 级指数退避重试吸收
- 前端按数据集口语分布拼接触发词（相对日期、逗号短句、人均预算），自由文本经清洗护栏，避免字段式模板把 RL 模型拖出训练分布
- FastAPI 任务 / 状态 / Trace / SSE 实时事件 / 审批接口；Vue 3 交互路线图（地图主、文字辅）；并发 worker 池可配置（`TRAVEL_HARNESS_API_WORKERS`），有界队列 503 背压
- Planner 只产出规划；Report Model 不重新规划，只把结果整理为受校验的路线 JSON，失败保留原文

## 快速开始

Python 3.11+：

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt   # 或 pip install -e '.[test]'
```

启动网页（默认只绑定本机；Demo 无鉴权，请勿暴露公网）：

```bash
.venv/bin/travel-harness --env-file .env serve --host 127.0.0.1 --port 8765
# 浏览器访问 http://127.0.0.1:8765
```

命令行直接运行：

```bash
.venv/bin/travel-harness --env-file .env run "明天从上海出发去杭州玩两天，2人，预算人均800元"
```

常用命令：

```bash
travel-harness trace <task-id>          # 查看轨迹
travel-harness checkpoints <task-id>    # 查看检查点
travel-harness resume <task-id>         # 恢复任务
travel-harness fork <task-id> --checkpoint 2
python -m unittest discover -s tests    # 84 个单测
```

配置全部走 `.env`（[.env.example](.env.example) 有完整注释），读取优先级：命令行参数 > `TRAVEL_HARNESS_*` > `AGENT_*` > `OPENAI_*`。

### 接入真实数据

```text
TRAVEL_HARNESS_TOOL_PROVIDER=amap        # 高德 Web 服务（地理四工具）
TRAVEL_HARNESS_AMAP_KEY=<your-key>       # 「Web 服务」类型，勿提交仓库
TRAVEL_HARNESS_SEARCH_PROVIDER=firecrawl # 真实网页检索
TRAVEL_HARNESS_FIRECRAWL_KEY=<your-key>
```

### 模型权重

训练权重不进本仓库。部署目录约定（打包归档中为空目录占位）：

```text
models/
└── checkpoint-150/        # TravelPlanner-4B 最终权重（Qwen3-4B 基座，SFT + RL，bf16）
```

用 vLLM 暴露 OpenAI-compatible API（RTX 5090 需 `VLLM_USE_FLASHINFER_SAMPLER=0`，详见 [docs/deploy-vllm-server.md](docs/deploy-vllm-server.md)）：

```bash
python -m vllm.entrypoints.openai.api_server \
  --model models/checkpoint-150 --served-model-name travel-planner \
  --max-model-len 50000 --port 8000
```

```text
TRAVEL_HARNESS_PLANNER_MODE=vllm    # 自动填入 tagged 协议、base_url、采样参数
```

无 GPU 时默认 `PLANNER_MODE=api` 直连 DeepSeek 等托管 API，同样可跑全链路。

## 仓库结构

```text
├── src/travel_agent_harness/   # Harness 内核：runtime / protocol / tools / store / api / reporting
│   ├── api/                    # FastAPI 服务（任务、SSE、审批、Inspector）
│   └── web_dist/               # 前端构建产物（pip 用户无需 Node）
├── frontend/                   # Vue 3 + Vite + TypeScript 源码（改前端才需要 Node）
├── tests/                      # 84 个单元测试（unittest，无外部依赖）
├── evals/                      # CLI eval 固定用例
├── eval_results/               # 评测/压测脚本 + 全部结果与报告
│   └── perf/                   # 负载测试脚本与数据
├── docs/                       # 部署、验证、并发、证据台账等文档 + 截图
├── models/                     # （打包为空）模型权重放置目录，见「模型权重」
├── .env.example                # 全部配置项注释
└── requirements.txt
```

## 文档索引

| 文档 | 内容 |
|---|---|
| [docs/deploy-vllm-server.md](docs/deploy-vllm-server.md) | vLLM 部署与接入 |
| [docs/concurrency-techniques.md](docs/concurrency-techniques.md) | 并发优化调研与落地映射 |
| [docs/evidence-ledger.md](docs/evidence-ledger.md) | 可公开边界与 Claim 证据 |
| [docs/verification.md](docs/verification.md) | DeepSeek Smoke Run 验证记录 |
| [docs/2026-09-07-overnight-summary.md](docs/2026-09-07-overnight-summary.md) | 训练对齐改造全程记录 |
| [eval_results/README.md](eval_results/README.md) | 评测产物总索引 |

## 安全说明

- `.env` 已在 `.gitignore` 中；任何真实 API Key 不应提交仓库（[.env.example](.env.example) 为模板）
- Trace 自动脱敏疑似 API Key；Web Demo 无鉴权，默认绑定 127.0.0.1，请勿直接暴露公网

## License

MIT，见 [LICENSE](LICENSE)。
