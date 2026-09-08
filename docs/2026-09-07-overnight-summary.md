# 明早总结：2026-09-07 夜间工作（并发压测修正 + 优化 + 四路对比）

## 一句话

你质疑得对——之前的压测绕过了 Web 服务层。真实路径下旧部署并发上限就是 **2**（其余无界排队）；
修复后甜区 **16 并发用户、763 任务/时**；并发优化五项已落地（62/62 测试）。四路对比初版
「基座 > SFT > RL」是**评测环境未对齐训练分布**造成的假象；按你的要求把训练循环的终局规则
和 visit 观测分布搬进 harness 后重跑，结论反转：**RL-150 是最强本地模型**（混合分 0.44、
judge 0.555，本地三者中最高），与训练侧 80 条 judge 一致。

## 1. 并发测评（你问的「最多多少个用户」）

走真实 HTTP 路径（POST /api/plans，一用户一任务）：

| 部署 | 结果 |
|---|---|
| 旧（worker=2） | 8 用户排队均值 76s/最久 160s，161 任务/时 |
| worker=16 | u16 档 **763 任务/时**、排队均值 8s、vLLM 21.4k tok/s、GPU 84% |
| worker=32 | u24/u32 吞吐 622-647/h 不再涨 → **系统上限 ≈650-760/h，瓶颈在外部 API** |

生产建议：`TRAVEL_HARNESS_API_WORKERS=16`（服务当前已按此运行）。

## 2. 落地的并发优化（docs/concurrency-techniques.md 有调研映射）

1. `TRAVEL_HARNESS_API_WORKERS` / `TRAVEL_HARNESS_API_QUEUE` — 池可配置 + 有界队列 503 背压
2. `http_client.PooledHttpClient` — 外部 API 连接复用 + 池满阻塞 + 429/5xx 退避重试
3. `TRAVEL_HARNESS_TOOL_CACHE_TTL` — 响应缓存（默认关，保评测确定性）
4. 截断输出（finish_reason=length）自动 1.5× 预算重试 —— 此前全部 failed 都是这类
5. 服务监听改 127.0.0.1（无鉴权不应对公网暴露；SSH 隧道不受影响）

**诚实声明**：优化后的 A/B 复测被 Firecrawl 上游同日恶化 2.5 倍污染（00 时段均值 10.2s →
16-17 时段 22.9-26.8s/次），绝对吞吐不可跨时段比；同窗口内 pool 16 > pool 8。
另：vLLM 重启需 `VLLM_USE_FLASHINFER_SAMPLER=0`（flashinfer JIT 与 CUDA 12.8/SM 12.x 不兼容，
torch 原生采样无性能损失）。

## 3. 四路对比（eval_results/compare_report.md）——训练环境对齐版（深夜二次更新）

你指出「训练项目里 RL 明明最好」——核实属实：训练侧 80 条双向 judge 为
RL 6.94 > SFT 6.31 > 基座 6.12（gemini 复核同向），我抽的 10 条在训练侧也是
RL 6 胜 2 负 2 平。此前 harness 测出相反结论，根因是运行环境不在 RL 的训练分布上。

第一轮对齐（终局规则 4 项）后你又追问「工程化实现怎么还有遗漏」——
遂对 travel_agentic_rl 做了全量工程审计，把剩余 8 类差距全部搬进 harness
（默认关，env 开启）：
- `TRAVEL_HARNESS_FORCE_ANSWER_AFTER_STEPS=12`（原文消息注入）
- `TRAVEL_HARNESS_REPEAT_ANSWER_CHANCE=true`（连续 3 轮整轮重复检测 + 一次强制作答机会）
- `TRAVEL_HARNESS_MAX_TOOL_OUTPUT_CHARS=5000`（json2md markdown + 头尾各 2500 字截断，
  与训练侧 utils/markdown.py / text.py 逐字一致）
- `TRAVEL_HARNESS_VISIT_EXTRACTOR=true`（deepseek-v4-flash + 训练同款 EXTRACTOR_PROMPT）
- `TRAVEL_HARNESS_TICKET_SIMULATOR=true`（火车/航班为训练同款 LLM 模拟器，
  TRAIN_TICKET/TRANSPORT system prompt 逐字，训练侧从未调真实票务 API）
- `TRAVEL_HARNESS_TRAINING_TOOL_FORMAT=true`（search/visit 观测用训练侧文本格式）
- `TRAVEL_HARNESS_CURRENT_DATE=2026-04-15`（数据集固定日期）
- tagged 解析器容错常态化：json_repair、`<tool_calls>` 复数包裹、
  tool/parameters 等变体、未闭合 `<answer>`、answer 优先于同轮 tool_call；
  解析失败注入训练原文引导消息（tool-first/partial-call/no-tool 三条逐字）后重问

深度对齐后重跑四路（同 10 条、同 judge，eval_results/run_aligned_compare.sh）：

| 指标 | 基座 | SFT | RL-150 | DeepSeek |
|---|---:|---:|---:|---:|
| 完成率 | 1.0 | 0.8 | 0.9 | 0.9 |
| 混合分(phase3) | 0.419 | 0.223 | **0.480** | 0.461 |
| judge | 0.48 | 0.27 | **0.60** | 0.57 |
| 重复阻断 | 0 | 12 | 1 | 0 |
| 必需工具覆盖率 | 0.65 | 0.69 | **0.775** | 0.775 |

**结论维持反转且差距拉大：RL-150 是最强本地模型**，judge 甚至超过 DeepSeek 参照
（0.60 vs 0.57），混合分领先基座幅度从 +0.05 扩到 +0.06；SFT 重复阻断 21→12、
RL 10→1，模拟器+训练文本格式让观测分布进一步贴近训练。旧结果归档为
compare_report_unaligned.md 与 *_aligned1.jsonl（第一轮对齐版）。

## 4. 服务现状

- vLLM：checkpoint-150 @ 127.0.0.1:8000（`VLLM_USE_FLASHINFER_SAMPLER=0`）
- harness API：workers=16 @ 127.0.0.1:8765（SSH 隧道 18765 不变），**已带训练对齐
  环境变量**（强制收尾/重复作答机会/观测 5000 字/visit 提炼/票务模拟器/训练文本格式）
- 另修一个演示链路 bug：RL 模型对字段式目标描述会复述工具 JSON Schema 当参数
  （评测集里也有 1 例，2f8591c1）；现在校验层识别 schema echo 并回显明确错误
  「arguments 里填的是工具定义…请写成具体值」，模型即可自我纠正（冒烟已验证），
  同时 Web 目标模板改成口语句式（贴近训练查询分布）
- 测试：82/82 通过（新增终局规则、抽取器、解析容错、连续轮次检测、模拟器接线、
  schema echo 引导用例）

## 5. 产物索引

- 对比报告：`eval_results/compare_report.md`
- 压测报告（含真实路径章节）：`eval_results/perf/perf_report.md`
- 调研映射：`docs/concurrency-techniques.md`
- 总索引：`eval_results/README.md`
