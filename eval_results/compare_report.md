# 训练流水线对比：基座 → SFT → RL，DeepSeek 作参照

- 同一 10 条样本、同一 tagged 契约、同一真实工具链（高德 + Firecrawl）
- 基座模型未见过本任务的工具标签协议，其得分即「训练前起点」
- RL 分数为训练侧六项子 reward 原代码复算（phase3 权重），judge=deepseek-v4-flash
- 运行环境已对齐训练循环（run_tool_loop_infer.py）：第 12 步强制收尾、重复循环检测+一次强制作答机会、工具观测截断 5000 字（json2md 头尾）、visit 页面经 deepseek-v4-flash 按 EXTRACTOR_PROMPT 提炼、火车/航班为训练同款 LLM 模拟器、search/visit 训练文本格式、固定日期 2026-04-15、tagged 解析器容错（json_repair/变体/未闭合 answer）与训练原文引导消息

## 总览

| 指标 | 基座 Qwen3-4B（未微调） | SFT checkpoint-420 | RL checkpoint-150（最终） | DeepSeek（托管参照） |
|---|---:|---:|---:|---:|
| 完成率 | 1.0 | 0.8 | 0.9 | 0.9 |
| 必需工具覆盖率 | 0.65 | 0.6917 | 0.775 | 0.775 |
| 工具错误率 | 0.0192 | 0.3375 | 0.12 | 0.0472 |
| 平均轮次 | 5.6 | 6.6 | 5.1 | 4.2 |
| 平均工具调用 | 5.2 | 8.0 | 7.5 | 10.6 |
| 重复阻断 | 0 | 12 | 1 | 0 |
| Schema 错误 | 1 | 15 | 8 | 5 |
| RL 混合分（phase3） | 0.4189 | 0.2234 | 0.4803 | 0.4612 |
| 平均答案长度（字） | 392.1 | 398.5 | 561.0 | 969.9 |

## RL 子项均分

| 子 reward | 基座 Qwen3-4B（未微调） | SFT checkpoint-420 | RL checkpoint-150（最终） | DeepSeek（托管参照） |
|---|---:|---:|---:|---:|
| process_step | 0.3 | 0.11 | 0.205 | 0.255 |
| tool_schema | 0.25 | 0.25 | 0.25 | 0.25 |
| answer_tag | 0.3 | 0.04 | 0.17 | 0.17 |
| stage_aware | 0.3 | 0.04 | 0.17 | 0.17 |
| tool_efficiency | 0.264 | 0.0824 | 0.19 | 0.183 |
| llm_judge | 0.48 | 0.27 | 0.6 | 0.57 |

## 逐条状态

| case | 基座 Qwen3-4B | SFT checkpoint-420 | RL checkpoint-150 | DeepSeek |
|---|---|---|---|---|
| 00e0280e | completed / 0.4065 | completed / 0.0865 | completed / 0.5765 | completed / 0.7165 |
| 12090666 | completed / 0.5005 | exhausted / -0.113 | completed / 0.3325 | exhausted / -0.124 |
| 258fcbf3 | completed / 0.0865 | completed / 0.3936 | completed / 0.7165 | completed / 0.4365 |
| 3ec619d0 | completed / 0.5065 | completed / 0.5867 | completed / 0.3665 | completed / 0.6465 |
| 4d836a54 | completed / 0.2965 | completed / 0.3395 | completed / 0.4855 | completed / 0.7775 |
| 6dcca263 | completed / 0.0865 | completed / 0.3665 | completed / 0.4215 | completed / 0.6955 |
| 8a4741c4 | completed / 0.7165 | exhausted / -0.105 | exhausted / 0.595 | completed / 0.7135 |
| 9d66d721 | completed / 0.4365 | completed / 0.0865 | completed / 0.5065 | completed / 0.5765 |
| b28eaf9c | completed / 0.5765 | completed / 0.2965 | completed / 0.3665 | completed / 0.0865 |
| d306664e | completed / 0.5765 | completed / 0.2965 | completed / 0.4365 | completed / 0.0865 |

> n=10 抽样 + 实时外部数据 + judge 方差：关注趋势（base→SFT→RL 的单调性），勿读小数点。