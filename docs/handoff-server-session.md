# 服务器会话交接文档（部署 + 对比评测）

> **这是一份会话交接文档**：你（服务器上的 Claude Code 会话）接手时，
> 本地会话已经完成了全部代码改造与打包。你的任务是在这台 GPU 服务器上
> 把系统跑起来并完成对比评测。命令细节查 [deploy-vllm-server.md](deploy-vllm-server.md)，
> 本文档告诉你做什么、为什么、怎么算完成。
>
> （此文档属临时交付物，项目发布 GitHub 前删除。）

## 1. 项目是什么

Travel Agent Harness：一个有界、可追溯的 Agent 运行时（预算/检查点/证据门/人工审批），
上面跑一个旅行规划产品。双模型流水线：

- **Planner（规划模型）**：驱动 agent 循环，调 8 个工具收集证据，产出方案
- **Report（报告模型）**：把 Planner 的结论整理成结构化 RouteReport JSON 给前端渲染

Planner 有两个后端，用 `TRAVEL_HARNESS_PLANNER_MODE` 一键切换：

| 模式 | Planner 后端 | 协议 | 用途 |
|---|---|---|---|
| `api` | 托管 DeepSeek API | native function-calling | 基线 |
| `vllm` | 本机 vLLM 服务的自托管微调 checkpoint | tagged（`<tool_call>`/`<answer>` 标签） | **本次部署的主角** |

## 2. 本地会话已经完成了什么（不要重做）

- ✅ 双模式切换与 vllm 预设（tagged 协议、`127.0.0.1:8000/v1`、采样参数
  temp 0.2 / top_p 0.95 / top_k 50 / max_tokens 5000、预算 600s）
- ✅ 采样参数全面环境变量化（`TRAVEL_HARNESS_MODEL_*`）
- ✅ 真实工具链：高德 provider（4 个地理工具）+ Firecrawl provider（search/visit），
  独立开关、可组合
- ✅ 前端已构建进 `src/travel_agent_harness/web_dist`（无需 Node）
- ✅ 48 个单元测试全部通过
- ✅ 操作手册 [deploy-vllm-server.md](deploy-vllm-server.md)（含故障排查表）

## 3. 服务器上的前置状态（先核实，缺什么补什么）

```bash
nvidia-smi                      # 4090 24GB；右上角 CUDA Version 决定 vllm 装法
python --version                # ≥3.11（镜像为 3.12）
df -h /root/autodl-tmp          # 数据盘 ≥30GB 空闲
ls /root/autodl-tmp/            # 应有 TravelAgentHarness/（已解压）和 checkpoint-150/
```

- **项目包**：`travel-agent-harness-server.tar.gz`（用户已上传）；未解压则
  `cd /root/autodl-tmp && tar -xzf travel-agent-harness-server.tar.gz`
- **模型**：`checkpoint-150/` = Qwen3-4B 架构 bf16 合并权重（~7.6GB），
  自带 tokenizer 与 `chat_template.jinja`（vLLM 自动加载，无需额外参数）。
  若用户放在别的路径，以实际为准
- **Claude Code 自身**：已按 Kimi K3 配置（`~/.claude/settings.json`），无需处理

## 4. 你的任务清单（按序执行）

### T1 安装（~10 分钟）
```bash
cd /root/autodl-tmp/TravelAgentHarness
python -m venv .venv && source .venv/bin/activate
pip install . --no-cache-dir
```
再装 vLLM：**先 `nvidia-smi` 看右上角 CUDA Version**——≥13.0 直接
`pip install -U vllm --no-cache-dir`；12.8/12.9 用
`pip install uv && uv pip install vllm --torch-backend auto`。
**绝不手动单独装 torch**（ABI 硬耦合，vLLM 会带正确的版本）。

### T2 启动 vLLM（~3-5 分钟模型加载）
```bash
tmux new -s vllm   # 或另开终端
source /root/autodl-tmp/TravelAgentHarness/.venv/bin/activate
vllm serve /root/autodl-tmp/checkpoint-150 \
  --served-model-name travel-planner \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.90 \
  --port 8000
```
验证：`curl http://127.0.0.1:8000/v1/models` 能看到 `travel-planner`。

### T3 配置 harness
**向用户索取三个 key**（不要自己猜、不要打印已存 key 的值）：
DeepSeek key（Report 阶段用）、高德 Web 服务 key、Firecrawl key。
然后写 `/root/autodl-tmp/TravelAgentHarness/server.env`（此文件绝不提交进仓库）：

```bash
TRAVEL_HARNESS_PLANNER_MODE=vllm
TRAVEL_HARNESS_REPORT_API_KEY=<DeepSeek key>
TRAVEL_HARNESS_TOOL_PROVIDER=amap
TRAVEL_HARNESS_AMAP_KEY=<高德 key>
TRAVEL_HARNESS_SEARCH_PROVIDER=firecrawl
TRAVEL_HARNESS_FIRECRAWL_KEY=<Firecrawl key>
TRAVEL_HARNESS_DB=/root/autodl-tmp/travel-harness.db
```

### T4 CLI 冒烟（不经 UI，先验证链路）
```bash
travel-harness --env-file server.env run \
  "规划 2026-10-02 从杭州到上海的两日行程，预算 1800 元；先核对交通和核心地点，再给方案。"
```
期望：`status: completed`，有 answer；随后 `travel-harness --env-file server.env trace <task_id>`
能看到真实的高德/Firecrawl 工具调用记录。

### T5 Web 服务
```bash
tmux new -s harness
travel-harness --env-file server.env serve --host 0.0.0.0 --port 8765
```
指导用户本地访问：`ssh -CNg -L 8765:127.0.0.1:8765 root@<实例地址> -p <端口>` 后开
`http://127.0.0.1:8765`，或按 AutoDL 自定义服务方式映射端口。
用户在 UI 上跑一个行程，确认：规划完成 → 行程时间线渲染 → 站点卡片带高德实拍图。

### T6 对比评测（api vs vllm）
另配 `api.env`（`PLANNER_MODE=api` + DeepSeek key + 相同的工具 provider 配置），
固定用例集 `evals/cases.jsonl` 两种模式各跑一遍：
```bash
travel-harness --env-file api.env    eval --cases evals/cases.jsonl > eval-api.json
travel-harness --env-file server.env eval --cases evals/cases.jsonl > eval-vllm.json
```
逐 case 对比：完成状态、步数、工具调用数/成功率、token 消耗、elapsed。
如果 vllm 模式出现 tagged protocol error：先查输出是否被截断
（`TRAVEL_HARNESS_MODEL_MAX_OUTPUT_TOKENS` 是否生效为 5000），再看采样参数。

### T7 交付
向用户汇总：两个 eval JSON 的对比结论 + 一个完整 UI 演示任务的 task_id，
服务保持运行。

## 5. 关键事实（容易踩的坑）

1. **Report 与 Planner 凭证分离**：vllm 模式下 report_api_key 绝不继承 planner key；
   缺 `TRAVEL_HARNESS_REPORT_API_KEY` 时 harness 启动直接报错——这是设计，不是 bug
2. **高德 key 必须是「Web服务」平台类型**，否则全部地理工具报 10001
3. **Firecrawl key 从 .env 复制时注意剥掉包裹的引号**（401 就是这个）；
   402 = 额度耗尽；单条查询失败只标记 error 不拖垮整批，属正常
4. **visit 工具是简化实现**：直接返回 Firecrawl 正文 markdown（截 6000 字），
   未做训练环境里的 LLM 提炼。模型在长正文上明显退化才需要补提炼器
5. **采样参数已在 vllm 预设里对齐训练评测条件**，不要画蛇添足改它们
6. 显存不够时把 `--max-model-len` 降到 16384，不要动量化（4090 24GB 跑 bf16 很宽裕）

## 6. 验收标准（全部满足才算完成）

- [ ] `curl http://127.0.0.1:8000/v1/models` 返回 `travel-planner`
- [ ] CLI 冒烟任务 `status=completed`，trace 里有真实工具调用（data_source 为 amap/firecrawl）
- [ ] 浏览器打开 UI 完成一次行程规划，站点卡片带实拍图
- [ ] `eval-api.json` 与 `eval-vllm.json` 都产出且做了对比汇总
