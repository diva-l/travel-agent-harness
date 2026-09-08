# 服务器部署与评测状态（2026-09-06）

AutoDL RTX 5090 (32GB) 实例上的完整部署记录与评测归档。所有服务以 setsid 独立会话后台运行，
不依赖任何终端会话。

## 运行中的服务

| 服务 | 地址 | 启动命令要点 | 日志 |
|---|---|---|---|
| vLLM（Planner，checkpoint-150，bf16） | `127.0.0.1:8000` | `--served-model-name travel-planner --max-model-len 50000 --gpu-memory-utilization 0.90`，需 `VLLM_USE_FLASHINFER_SAMPLER=0` | `/root/autodl-tmp/vllm.log` |
| Harness Web（FastAPI + Vue） | `0.0.0.0:8765` | `travel-harness --env-file .env serve --host 0.0.0.0 --port 8765` | `/root/autodl-tmp/harness.log` |

本地查看 UI（本地电脑执行，非服务器）：

```powershell
ssh -CNg -L 18765:127.0.0.1:8765 root@connect.westd.seetacloud.com -p <端口>
# 浏览器打开 http://127.0.0.1:18765
```

（本地 8765 常被其他程序占用，故示例用 18765。）

## 训练契约对齐（2026-09-06 全部完成）

| 约束 | 训练侧（train_rl.sh / infer.sh） | 部署侧 | 状态 |
|---|---|---|---|
| 单次上下文窗口 | `MAX_LENGTH=50000`（rollout `--vllm_max_model_len 50000`） | `--max-model-len 50000` | ✅ |
| 每轮输出上限 | `MAX_COMPLETION_LENGTH=5000`（per_round） | `MODEL_MAX_OUTPUT_TOKENS=5000` | ✅ |
| 最大轮次 | `max_turns=13` | `max_steps=13` | ✅ |
| 同工具重复上限 | `max_same_tool_call_rounds=3` | `repeat_call_limit=3` | ✅ |
| 采样参数 | temperature 0.2 / top_p 0.95 / top_k 50 | 同左 | ✅ |
| 累计 token 上限 | 无此概念 | 300k，仅防失控护栏（非成本约束） | ✅ |
| 系统提示词 | 训练原文逐字 | `prompts.py` tagged 分支逐字复刻 | ✅ |
| 工具观测格式 | `<tool_response>` 裸标签 + `TOOL_ERROR:` | `protocol.py` `_to_training_observation` | ✅ |
| 工具 schema | 无 minItems/maxItems/minimum/maximum 附加约束 | `tools.py` 已移除 | ✅ |

## 代码改动清单（相对原始 tar 包）

| 文件 | 改动 |
|---|---|
| `src/travel_agent_harness/prompts.py` | tagged 协议系统提示词改为训练原文（仅日期/轮次动态） |
| `src/travel_agent_harness/protocol.py` | 工具观测渲染对齐训练格式（裸 `<tool_response>`、解包 result、`TOOL_ERROR:`） |
| `src/travel_agent_harness/tools.py` | 移除训练契约外的 schema 约束（search.query 的 items 限制、around_search.radius 的范围限制） |
| `src/travel_agent_harness/config.py` | vllm 预设 `max_total_tokens=300000`（防失控护栏语义） |
| `tests/test_protocol.py` | 断言同步更新 |
| `docs/deploy-vllm-server.md` / `docs/verification.md` / `docs/evidence-ledger.md` | 部署记录、故障排查（flashinfer/sm_120）、对齐依据 |

单测：48/48 通过（`python -m unittest discover -s tests`）。

## 评测归档（`eval_results/`）

10 条测试样本（`eval_results/test_final.jsonl` 按 id 排序每隔 8 条确定性抽样，指纹 sha256
`b7d3c735…e0ef4303`），双模型对比，真实工具链（高德 Web 服务 + Firecrawl）：

| 指标 | vLLM (checkpoint-150) | DeepSeek (api) |
|---|---:|---:|
| RL 混合分（phase3 权重） | 0.1885 | 0.4825 |
| 完成率 | 0.6（4 条均撞 13 轮训练上限） | 1.0 |
| 必需工具覆盖率 | 0.70 | 0.65 |
| 工具错误率 | 0.2315 | 0.0805 |
| 平均 token（prompt/completion 拆分） | 62391 / 1197（不花钱，GPU 时间代理） | 19213 / 1349（计费） |
| 平均耗时 | 20.3s | 19.45s |

文件说明：

| 文件 | 内容 |
|---|---|
| `report.md` | 中文评测报告（总览 + RL 子项 + token 口径说明 + exhausted 明细 + 逐条） |
| `summary.json` | 聚合指标 + 训练对齐参数块 |
| `results_vllm.jsonl` / `results_api.jsonl` | 逐条原始记录（答案全文、轨迹统计、子 reward） |
| `gold_selected.jsonl` | 抽中的 10 条样本（judge 对照） |
| `run_eval.py` / `summarize.py` / `capture_ui.py` | 可复跑脚本（评测 / 聚合 / 截图） |
| `run_vllm_aligned.log` / `run_api.log` | 运行日志（`run_vllm.log` 为对齐前首次运行，已被覆盖的结果对应） |
| `screenshots/` | 前端截图 5 张（桌面 1440px ×3、移动 390px ×2，任务 fd9cc23e 杭州 3 日行程） |

复跑方式：

```bash
cd /root/autodl-tmp/TravelAgentHarness
.venv/bin/python eval_results/run_eval.py --mode vllm   # 或 --mode api
.venv/bin/python eval_results/summarize.py
```

注意：`summarize.py` 会重写 `report.md`，其中的「Token 口径说明」「exhausted 明细」两节
为手工补充，重跑后需重新合并。

## 已知边界 / 后续建议

1. checkpoint-150 的 4 条 exhausted 均为 13 轮内未收敛（非预算误杀）——模型「查证强、收尾弱」，
   是课程第三阶段（judge 权重 0.75）尚未充分训练的中间态表现；用户反馈训练在 160+ 步已收敛，
   可用最终 checkpoint 复跑对比。
2. n=10 抽样方差大，judge（deepseek-v4-flash）对长答案有系统性偏好，单次对比不构成总体结论。
3. 报告阶段的无编造约束目前为提示词级（temperature=0 + 严格规则），数值尚无程序级对账；
   用户已确认暂不收紧。
4. `.env` 含全部 API key，**不得提交仓库、不得写入日志/文档**。
5. Web demo 无鉴权，不要长期暴露公网。
