# 训练流水线对比：基座 → SFT → RL，DeepSeek 作参照

- 同一 10 条样本、同一 tagged 契约、同一真实工具链（高德 + Firecrawl）
- 基座模型未见过本任务的工具标签协议，其得分即「训练前起点」
- RL 分数为训练侧六项子 reward 原代码复算（phase3 权重），judge=deepseek-v4-flash

## 总览

| 指标 | 基座 Qwen3-4B（未微调） | SFT checkpoint-420 | Voyager-4B（RL 最终） | DeepSeek（托管参照） |
|---|---:|---:|---:|---:|
| 完成率 | 0.9 | 0.6 | 0.6 | 1.0 |
| 必需工具覆盖率 | 0.675 | 0.7917 | 0.7 | 0.65 |
| 工具错误率 | 0.0364 | 0.3431 | 0.2315 | 0.0805 |
| 平均轮次 | 5.9 | 8.0 | 7.5 | 4.0 |
| 平均工具调用 | 5.5 | 10.2 | 10.8 | 8.7 |
| 重复阻断 | 0 | 24 | 16 | 0 |
| Schema 错误 | 2 | 11 | 9 | 7 |
| RL 混合分（phase3） | 0.3645 | 0.2642 | 0.1885 | 0.4825 |
| 平均答案长度（字） | 300.6 | 304.4 | 157.6 | 872.1 |

## RL 子项均分

| 子 reward | 基座 Qwen3-4B（未微调） | SFT checkpoint-420 | Voyager-4B（RL 最终） | DeepSeek（托管参照） |
|---|---:|---:|---:|---:|
| process_step | 0.21 | 0.12 | 0.12 | 0.3 |
| tool_schema | 0.225 | 0.25 | 0.25 | 0.25 |
| answer_tag | 0.17 | -0.22 | -0.22 | 0.3 |
| stage_aware | 0.17 | -0.22 | -0.22 | 0.3 |
| tool_efficiency | 0.237 | 0.0632 | 0.076 | 0.2142 |
| llm_judge | 0.43 | 0.36 | 0.25 | 0.578 |

## 逐条状态

| case | 基座 Qwen3-4B | SFT checkpoint-420 | Voyager-4B（RL） | DeepSeek |
|---|---|---|---|---|
| 00e0280e | completed / 0.3365 | completed / 0.2965 | completed / 0.4365 | completed / 0.4365 |
| 12090666 | completed / 0.3635 | exhausted / -0.101 | exhausted / -0.1176 | completed / 0.0325 |
| 258fcbf3 | completed / 0.0865 | exhausted / -0.0952 | completed / 0.2965 | completed / 0.0865 |
| 3ec619d0 | failed / -0.11 | completed / 0.5765 | completed / 0.3665 | completed / 0.5765 |
| 4d836a54 | completed / 0.5065 | completed / 0.4005 | completed / 0.5375 | completed / 0.6937 |
| 6dcca263 | completed / 0.6465 | exhausted / -0.075 | exhausted / -0.059 | completed / 0.7775 |
| 8a4741c4 | completed / 0.6465 | completed / 0.7135 | exhausted / -0.101 | completed / 0.7025 |
| 9d66d721 | completed / 0.3665 | completed / 0.4265 | completed / 0.5065 | completed / 0.5765 |
| b28eaf9c | completed / 0.2965 | completed / 0.5765 | completed / 0.0865 | completed / 0.5065 |
| d306664e | completed / 0.5065 | exhausted / -0.0767 | exhausted / -0.0675 | completed / 0.4365 |

> n=10 抽样 + 实时外部数据 + judge 方差：关注趋势（base→SFT→RL 的单调性），勿读小数点。