# 评测结果总索引（2026-09-08 更新）

本目录汇集 TravelAgentHarness 的评测产物：模型质量对照（Voyager-4B vs DeepSeek，
同一 Harness + 同一真实工具链）与工程压测。目录约定：报告在顶层，可复跑脚本在
[scripts/](scripts/)，原始数据在 [data/](data/)。测试集：
[data/test_final.jsonl](data/test_final.jsonl) 80 条中确定性抽样 10 条（按 id 排序每隔 8 条），
数据集指纹 sha256 `b7d3c735c13f92328aa0bf246d0649e4a1f8cfac6fcc187281775622e0ef4303`。
工具链均为真实外部 API（高德 Web 服务 + Firecrawl）。

## 一、模型质量对照（Voyager-4B vs DeepSeek）

| 文件 | 内容 |
|---|---|
| [report.md](report.md) | 主报告：总览、token 口径说明、未完成明细、逐条 |
| [data/summary.json](data/summary.json) | 聚合指标 |
| [data/results_vllm_rl150.jsonl](data/results_vllm_rl150.jsonl) | Voyager-4B 逐条原始记录 |
| [data/results_api.jsonl](data/results_api.jsonl) | DeepSeek 逐条原始记录 |
| [data/gold_selected.jsonl](data/gold_selected.jsonl) | 抽中的 10 条样本（judge 对照） |
| [scripts/run_eval.py](scripts/run_eval.py) / [scripts/summarize.py](scripts/summarize.py) | 可复跑脚本 |

核心结论：完成率 0.9 vs 0.9（各 1 条撞 13 轮上限）；必需工具覆盖率 0.775 持平；
过程奖励混合分 0.480 vs 0.461、judge 分 0.60 vs 0.57，Voyager-4B 小幅反超；
vLLM token 零边际成本、耗时同一量级。

## 二、工程压测（2026-09-08；并发 1/2/4/8 + 裸跑对照 + 真实 HTTP 路径）

| 文件 | 内容 |
|---|---|
| [perf/perf_report.md](perf/perf_report.md) | 压测报告：吞吐总览、时间拆分、harness 开销、GPU 利用、干预统计、HTTP 路径 |
| [perf/data/load_vllm.json](perf/data/load_vllm.json) | 逐任务原始记录（6 档） |
| [perf/data/gpu_samples.jsonl](perf/data/gpu_samples.jsonl) | GPU 采样 1189 条（util/显存/功耗） |
| [perf/data/api_load_baseline.json](perf/data/api_load_baseline.json) / [perf/data/api_load_sweep.json](perf/data/api_load_sweep.json) | 真实 HTTP 路径（worker=2 基线 / worker=16 扫档） |
| [perf/scripts/load_test.py](perf/scripts/load_test.py) / [perf/scripts/api_load_test.py](perf/scripts/api_load_test.py) / [perf/scripts/harness_value.py](perf/scripts/harness_value.py) | 可复跑脚本 |

核心结论：裸 vLLM c8 十请求 2.3 秒（6101 tok/s），模型侧余量巨大；
**瓶颈已彻底转移到外部工具链**（工具耗时为模型的 6~9 倍，GPU 均值仅 12~24%）；
harness 自身开销仍 ≈0（逐任务均值 -11%~+0.7%）；c4 吞吐见顶 128 任务/时；
schema-echo 护栏场均拦截 0.6 次且全部自愈；真实 HTTP 路径 worker=16 时
**645 任务/时、排队 ~3s**（worker=2 时 42.6 任务/时、排队 286s，吞吐 15×）。

## 三、前端展示截图

[../docs/screenshots/](../docs/screenshots/)：桌面 1440px ×3（整页/视口/细节），
任务为杭州 3 日 2 夜行程（fd9cc23e），含预算卡片、按天筛选、站点时间线、配图、
Planner 原始规划全文面板。

## 注意事项

- `scripts/summarize.py` 重跑会覆盖 `report.md`，其中「Token 口径说明」「未完成明细」两节为手工补充
- n=10 抽样 + judge（deepseek-v4-flash）长答案偏好：所有对比关注趋势，勿读小数点
- 轨迹含实时外部数据，不可精确复现
