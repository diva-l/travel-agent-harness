# 服务器部署指南（vLLM 自托管规划模型）

这份文档描述如何在带 GPU 的 Linux 服务器上运行 Travel Agent Harness：
Planner（规划模型）由本机 vLLM 服务的自托管 checkpoint 承担，Report（报告模型）继续走托管 API。
harness 与 vLLM 跑在**同一台机器**上，通过 `127.0.0.1` 直连，无需任何远程隧道配置。

## 架构

```
┌──────────────────── 同一台 GPU 服务器 ────────────────────┐
│                                                          │
│   travel-harness serve (FastAPI, :8765)                  │
│      │                                                   │
│      ├─ Planner → http://127.0.0.1:8000/v1 (vLLM, bf16)  │
│      └─ Report  → https://api.deepseek.com (托管 API)    │
│                                                          │
│   vllm serve <checkpoint> (:8000)                        │
└──────────────────────────────────────────────────────────┘
        ▲                                    ▲ 外网
        │ 浏览器访问（SSH 隧道或平台端口映射）   └─ 工具数据：高德 Web 服务 + Firecrawl
       你的电脑
```

测试期的工具链与训练环境对齐：四个地理工具走**高德 Web 服务**
（`TRAVEL_HARNESS_TOOL_PROVIDER=amap`），`search`/`visit` 走 **Firecrawl**
（`TRAVEL_HARNESS_SEARCH_PROVIDER=firecrawl`），两者独立开关、可自由组合。
正式发布前把这两个 provider 与对应 key 一并去掉，回到纯 demo 数据源。

## 两种运行模式

harness 通过 `TRAVEL_HARNESS_PLANNER_MODE` 一键切换 Planner 后端：

| 模式 | Planner | 适用 |
|---|---|---|
| `api`（默认） | 托管 API（DeepSeek），native function-calling 协议 | 日常开发、无 GPU |
| `vllm` | 本机 vLLM 服务的自托管模型，tagged 标签协议 | GPU 服务器 |

`vllm` 模式自动带入这些默认值（显式环境变量始终优先）：

| 配置项 | vllm 预设值 | 说明 |
|---|---|---|
| `TRAVEL_HARNESS_BASE_URL` | `http://127.0.0.1:8000/v1` | 本机 vLLM |
| `TRAVEL_HARNESS_MODEL` | `travel-planner` | 与 `--served-model-name` 对应 |
| `TRAVEL_HARNESS_MODEL_PROTOCOL` | `tagged` | `<tool_call>`/`<answer>` 标签协议 |
| `TRAVEL_HARNESS_MODEL_TEMPERATURE` | `0.2` | 对齐推理评测条件 |
| `TRAVEL_HARNESS_MODEL_TOP_P` | `0.95` | 同上 |
| `TRAVEL_HARNESS_MODEL_TOP_K` | `50` | vLLM 扩展字段，仅设置时才发送 |
| `TRAVEL_HARNESS_MODEL_MAX_OUTPUT_TOKENS` | `5000` | 容纳长篇 `<answer>` 规划文本 |
| `TRAVEL_HARNESS_MAX_SECONDS` | `600` | 本地模型比托管 API 慢，放宽预算 |
| `TRAVEL_HARNESS_MAX_TOTAL_TOKENS` | `300000` | 自托管 token 不花钱，仅作防失控护栏；训练侧无累计上限概念，真实约束是单次上下文 50k（由 vLLM `--max-model-len` 强制） |
| `TRAVEL_HARNESS_API_KEY` | `vllm-local` | vLLM 未设 `--api-key` 时任意值均可 |

**注意**：vllm 模式下 Report 阶段默认回到托管 API（`deepseek-v4-flash`），
且**不会**继承 Planner 的 key——必须显式设置 `TRAVEL_HARNESS_REPORT_API_KEY`，
或用 `TRAVEL_HARNESS_REPORT_ENABLED=false` 关掉报告阶段。

## 服务器要求

- GPU：**≥16GB 显存**即可运行 4B bf16 不压缩模型；推荐 **4090 24GB**（可同时容纳 32k 上下文与多路并发）
- 磁盘：≥30GB 空闲（checkpoint ~8GB + vLLM 环境 ~8GB + 项目）
- Python ≥3.11；CUDA 12.x（平台预装镜像通常自带）
- 能访问托管 API（Report 阶段与高德 Web 服务需要外网）

## 部署步骤

### 1. 上传代码与模型

本地打包项目（排除虚拟环境与产物）：

```bash
tar -czf travel-agent-harness.tar.gz \
  --exclude=.venv --exclude=artifacts --exclude=__pycache__ \
  --exclude=node_modules --exclude='*.db' \
  TravelAgentHarness/
```

模型 checkpoint 目录单独上传（体积大，建议用平台网盘或 `scp`/`rsync`）：

```bash
scp -P <端口> -r Voyager-4B/ root@<实例地址>:/root/autodl-tmp/
```

### 2. 安装

```bash
tar -xzf travel-agent-harness.tar.gz && cd TravelAgentHarness
python -m venv .venv && source .venv/bin/activate
pip install .          # harness 本体（含已构建的前端资源）
pip install -U vllm    # 推理服务
```

> **不要单独手动安装 PyTorch。** vLLM 的 CUDA 内核与其绑定的 torch 版本是
> ABI 硬耦合（当前版本对应 torch 2.13），装 vLLM 时会自动拉取正确的 torch；
> 镜像里预装的旧 torch 会被替换，不需要管它。手动指定 torch 版本反而可能
> 导致 segfault / undefined symbol。
>
> 装之前先 `nvidia-smi` 看右上角 **CUDA Version**（驱动支持的最高 CUDA）：
> - **≥ 13.0** → 直接 `pip install -U vllm`
> - **12.8 / 12.9**（驱动较旧，跑不动 CUDA 13 运行时）→ 用 uv 自动匹配：
>   `pip install uv && uv pip install vllm --torch-backend auto`
>   它会按驱动选择 cu128/cu129 构建，避免 CUDA 版本不匹配。

### 3. 启动 vLLM（不压缩，bf16 原样加载）

```bash
vllm serve /root/autodl-tmp/Voyager-4B \
  --served-model-name travel-planner \
  --max-model-len 50000 \
  --gpu-memory-utilization 0.90 \
  --port 8000
```

`--max-model-len 50000` 对齐训练侧 `MAX_LENGTH=50000`（`train_rl.sh`，rollout 服务器同款
`--vllm_max_model_len 50000`）。用更小的窗口会在长取证轨迹上直接拒绝训练分布内的请求
（2026-09-06 实测：10 条评测中单次调用最大 prompt 达 33,425，超过 32768）。

checkpoint 自带的 `chat_template.jinja` 会被自动加载。验证：

```bash
curl http://127.0.0.1:8000/v1/models
```

建议用 `tmux`/`nohup` 挂后台：`nohup vllm serve ... > vllm.log 2>&1 &`

### 4. 配置 harness

写一份 `server.env`（**不要提交到仓库**）：

```bash
TRAVEL_HARNESS_PLANNER_MODE=vllm
TRAVEL_HARNESS_REPORT_API_KEY=<你的 DeepSeek key>
TRAVEL_HARNESS_TOOL_PROVIDER=amap
TRAVEL_HARNESS_AMAP_KEY=<你的高德 Web 服务 key>
TRAVEL_HARNESS_SEARCH_PROVIDER=firecrawl
TRAVEL_HARNESS_FIRECRAWL_KEY=<你的 Firecrawl key>
TRAVEL_HARNESS_DB=/root/autodl-tmp/travel-harness.db
```

### 5. 启动 harness

```bash
travel-harness --env-file server.env serve --host 0.0.0.0 --port 8765
```

本机访问 UI（二选一）：

```bash
# 方式 A：SSH 隧道（推荐，不暴露公网）
ssh -CNg -L 8765:127.0.0.1:8765 root@<实例地址> -p <端口>
# 然后浏览器打开 http://127.0.0.1:8765

# 方式 B：平台自定义服务端口映射（按平台文档把 8765 映射出去）
```

### 6. 冒烟验证

```bash
# CLI 直接跑一个任务（不经 UI）
travel-harness --env-file server.env run \
  "规划 2026-10-02 从杭州到上海的两日行程，预算 1800 元；先核对交通和核心地点，再给方案。"

# 查看轨迹与 checkpoint
travel-harness --env-file server.env trace <task_id>
```

## 切回 api 模式

```bash
TRAVEL_HARNESS_PLANNER_MODE=api
TRAVEL_HARNESS_API_KEY=<DeepSeek key>     # Planner 与 Report 共用
```

## 对比评测（api vs vllm）

同一固定用例集 `evals/cases.jsonl` 分别跑两种模式，比较完成率、工具调用数、
步骤数与 token 消耗：

```bash
# api 模式
travel-harness --env-file api.env eval --cases evals/cases.jsonl > eval-api.json

# vllm 模式
travel-harness --env-file server.env eval --cases evals/cases.jsonl > eval-vllm.json
```

两组输出均为 JSON，包含每个 case 的状态、步数、工具调用统计，
直接 `diff` 或并排比较即可。

## 故障排查

| 现象 | 排查 |
|---|---|
| vLLM 启动 OOM | 降 `--max-model-len 16384`，或 `--gpu-memory-utilization 0.85` |
| RTX 50 系（sm_120）启动即报 "FlashInfer requires GPUs with sm75 or higher" | vLLM 0.28 默认的 flashinfer 采样器 JIT 检查误伤新架构；加环境变量 `VLLM_USE_FLASHINFER_SAMPLER=0` 回退原生采样（2026-09-06 在 RTX 5090 上实测） |
| harness 报 "missing API key" | vllm 模式只补 Planner key；Report 需 `TRAVEL_HARNESS_REPORT_API_KEY` |
| Report 阶段 401 | Report 回托管 API 了，检查 `TRAVEL_HARNESS_REPORT_API_KEY` 是否有效 |
| tagged protocol error | 确认 `TRAVEL_HARNESS_MODEL_PROTOCOL=tagged` 且采样参数为预设值；模型输出被截断时调大 `TRAVEL_HARNESS_MODEL_MAX_OUTPUT_TOKENS` |
| 工具调用 400 | vLLM 侧确认 `--served-model-name` 与 `TRAVEL_HARNESS_MODEL` 一致 |
| 高德工具报错 10001 | key 平台类型必须是「Web服务」 |
| search/visit 报错 firecrawl error | 检查 `TRAVEL_HARNESS_FIRECRAWL_KEY` 与额度（402=额度耗尽，429=限流，重试即可）；单条查询失败不会拖垮整批 |
| visit 的 summary 是原始正文 | 当前实现直接返回 Firecrawl 抽取的正文 markdown（截断 6000 字），未做训练环境里的 LLM 提炼；如模型在长正文上表现明显退化再考虑补提炼器 |
