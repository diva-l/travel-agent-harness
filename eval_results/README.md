# 评测结果总索引（2026-09-08 更新）

本目录汇集 TravelAgentHarness 的全部评测产物。目录约定：报告在顶层，可复跑脚本在
[scripts/](scripts/)，原始数据与日志在 [data/](data/)。测试集：
[data/test_final.jsonl](data/test_final.jsonl) 80 条中确定性抽样 10 条（按 id 排序每隔 8 条），
数据集指纹 sha256 `b7d3c735c13f92328aa0bf246d0649e4a1f8cfac6fcc187281775622e0ef4303`。
工具链均为真实外部 API（高德 Web 服务 + Firecrawl）。

## 一、双模型质量评测（RL 层 + harness 层）

| 文件 | 内容 |
|---|---|
| [report.md](report.md) | 主报告：总览、RL 子项、token 口径与训练对齐说明、exhausted 明细、逐条 |
| [data/summary.json](data/summary.json) | 聚合指标（含 prompt/completion 拆分、训练对齐参数块） |
| [data/results_vllm_rl150.jsonl](data/results_vllm_rl150.jsonl) | Voyager-4B（RL） 逐条原始记录（对齐预算后重跑版） |
| [data/results_api.jsonl](data/results_api.jsonl) | DeepSeek 基线逐条原始记录 |
| [data/gold_selected.jsonl](data/gold_selected.jsonl) | 抽中的 10 条样本（judge 对照） |
| [scripts/run_eval.py](scripts/run_eval.py) / [scripts/summarize.py](scripts/summarize.py) | 可复跑脚本 |

核心结论：RL 混合分 0.189 vs 0.483；完成率 0.6 vs 1.0（4 条均撞 13 轮训练上限，非预算误杀）；
工具覆盖率 0.70 反超 DeepSeek 0.65；vLLM token 零成本、耗时持平。

## 二、工程压测（训练环境对齐版，2026-09-08 重测；并发 1/2/4/8 + 裸跑对照）

| 文件 | 内容 |
|---|---|
| [perf/perf_report.md](perf/perf_report.md) | 压测报告：吞吐总览、时间拆分、harness 开销、GPU 利用、干预统计、HTTP 路径 |
| [perf/data/load_vllm.json](perf/data/load_vllm.json) | 逐任务原始记录（6 档） |
| [perf/data/gpu_samples.jsonl](perf/data/gpu_samples.jsonl) | GPU 采样 1189 条（util/显存/功耗） |
| [perf/data/api_load_baseline.json](perf/data/api_load_baseline.json) / [perf/data/api_load_sweep.json](perf/data/api_load_sweep.json) | 真实 HTTP 路径（worker=2 基线 / worker=16 扫档） |
| [perf/scripts/load_test.py](perf/scripts/load_test.py) / [perf/scripts/api_load_test.py](perf/scripts/api_load_test.py) / [perf/scripts/harness_value.py](perf/scripts/harness_value.py) | 可复跑脚本 |

核心结论（对齐版）：裸 vLLM c8 十请求 2.3 秒（6101 tok/s），模型侧余量巨大；
**瓶颈已彻底转移到外部工具链**（工具耗时为模型的 6~9 倍，GPU 均值仅 12~24%）；
harness 自身开销仍 ≈0（逐任务均值 -11%~+0.7%）；c4 吞吐见顶 128 任务/时；
schema-echo 护栏场均拦截 0.6 次且全部自愈；HTTP 路径 worker=16 时 645 任务/时、
排队 ~3s（worker=2 时 42.6/h、排队 286s）。

## 三、前端展示截图

[../docs/screenshots/](../docs/screenshots/)：桌面 1440px ×3（整页/视口/细节），
任务为杭州 3 日 2 夜行程（fd9cc23e），含预算卡片、按天筛选、站点时间线、配图、
Planner 原始规划全文面板。

## 四、训练流水线对比（已完成 2026-09-07，训练环境深度对齐版）

[compare_report.md](compare_report.md)：基座 vs SFT vs RL-150 vs DeepSeek 四路对比。
脚本：[scripts/merge_sft.py](scripts/merge_sft.py)、[scripts/compare_checkpoints.py](scripts/compare_checkpoints.py)、
[scripts/run_aligned_compare.sh](scripts/run_aligned_compare.sh)；数据 `data/results_vllm_{base,sft,rl150}.jsonl` /
`data/results_api.jsonl`（轨迹库 `/root/autodl-tmp/eval-vllm-{base,sft,rl150}.db`、`eval-api.db`）。

**口径说明（关键）**：harness 运行时已对齐训练循环 `run_tool_loop_infer.py` 的全部
工程化实现 —— 第 12 步强制收尾（原文消息）、连续整轮重复检测+一次强制作答机会
（原文消息）、工具观测 json2md markdown + 头尾各 2500 字截断（训练 utils 逐字移植）、
visit 页面经 deepseek-v4-flash 按 EXTRACTOR_PROMPT 提炼、火车/航班为训练同款 LLM
模拟器（逐字 system prompt）、search/visit 训练文本格式、固定日期 2026-04-15、
tagged 解析器容错（json_repair/变体/未闭合 answer）+ 训练原文引导消息。

**深度对齐后结论与训练侧 80 条 judge 一致：RL-150 是最强本地模型** —— 混合分
RL 0.480 > DeepSeek 0.461 > 基座 0.419 > SFT 0.223，judge 分 RL 0.60 > DeepSeek 0.57
> 基座 0.48 > SFT 0.27；RL 重复阻断降到 1（基座口径 16）。第一轮对齐（仅终局规则
4 项）的数据为混合分 RL 0.44 > 基座 0.39 > SFT 0.34 / judge RL 0.555，深度对齐后
RL 领先幅度进一步拉大，证实观测分布与票务模拟器对齐确有收益。

## 五、真实 HTTP 路径并发压测（2026-09-08 对齐版重测）

[perf/perf_report.md](perf/perf_report.md) 第五节：多用户打真实 `POST /api/plans` 路径。
- 默认部署（worker=2）：8 用户排队均值 286s，42.6 任务/时
- worker=16：**645 任务/时，排队 ~3s**，u16 档 16/16 完成
- 吞吐 15×；上限仍由外部工具 API 决定（对齐版工具链含 LLM 模拟器往返）

## 注意事项

- `scripts/summarize.py` 重跑会覆盖 `report.md`，其中「Token 口径说明」「exhausted 明细」两节为手工补充
- n=10 抽样 + judge（deepseek-v4-flash）长答案偏好：所有对比关注趋势，勿读小数点
- 轨迹含实时外部数据，不可精确复现
