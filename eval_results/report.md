# 评测报告：Voyager-4B (vLLM) vs DeepSeek 基线

- 测试集：`test_final.jsonl` 确定性抽样 10/80（按 id 排序每隔 8 条）
- 数据集指纹（sha256）：`b7d3c735c13f92328aa0bf246d0649e4a1f8cfac6fcc187281775622e0ef4303`
- 工具链：高德 Web 服务（真实） + Firecrawl（真实检索）
- RL 分数：训练侧 parser-aligned 六项子 reward 原代码复算，课程第 3 阶段权重 [process 0.05, schema 0.07, answer_tag 0.03, stage 0.05, efficiency 0.10, llm_judge 0.70]
- LLM judge：deepseek-v4-flash，对照测试集 gold answer（judge 提示词与训练侧一致）

## 总览

| 指标 | vLLM (Voyager-4B) | DeepSeek (api) |
|---|---:|---:|
| 完成率 | 0.6 | 1.0 |
| 必需工具覆盖率 | 0.7 | 0.65 |
| 工具错误率 | 0.2315 | 0.0805 |
| 平均模型轮次 | 7.5 | 4.0 |
| 平均工具调用数 | 10.8 | 8.7 |
| 平均 Token（累计） | 63588.0 | 20561.9 |
| — 其中 prompt（输入） | 62390.6 | 19212.6 |
| — 其中 completion（输出） | 1197.4 | 1349.3 |
| 单次调用最大 prompt | 33425 | 15753 |
| 平均耗时（秒） | 20.274 | 19.45 |
| 重复调用阻断次数 | 16 | 0 |
| Schema 校验错误数 | 9 | 7 |
| RL 混合分（phase3 权重） | 0.1885 | 0.4825 |
| 平均答案长度（字） | 157.6 | 872.1 |

## RL 子项均分

| 子 reward | vLLM | DeepSeek |
|---|---:|---:|
| process_step | 0.12 | 0.3 |
| tool_schema | 0.25 | 0.25 |
| answer_tag | -0.22 | 0.3 |
| stage_aware | -0.22 | 0.3 |
| tool_efficiency | 0.076 | 0.2142 |
| llm_judge | 0.25 | 0.578 |

## Token 口径说明（重要）

两种模式下 token 的含义**完全不同**，不能直接横比：

- **DeepSeek (api)**：token = 钱。19,213 prompt + 1,349 completion 每 case，按托管 API 计费，是真实货币成本。
- **vLLM (Voyager-4B)**：token = 零边际成本（自有 GPU，电费已含在租机费里）。这里的数字只有两个意义：
  1. **防失控护栏记账**——harness 累计上限已放宽到 300k，只为熔断死循环；
  2. **GPU 时间代理**——本地计算不产生账单，平均耗时 20.3s 与 DeepSeek API（19.45s）相当。

**与训练配置的对齐**（`travel_agentic_rl/train_rl.sh` / `rollout.sh` / `infer.sh`）：

| 约束 | 训练侧取值 | 本评测 vLLM 侧 | 说明 |
|---|---|---|---|
| 单次上下文窗口 | `MAX_LENGTH=50000`（rollout `--vllm_max_model_len 50000`） | `--max-model-len 50000` | 已对齐（首次部署误用 32768，本轮实测最大单调用 prompt 33,425 已超出旧窗口，会被拒） |
| 每轮输出上限 | `MAX_COMPLETION_LENGTH=5000`（per_round） | `MODEL_MAX_OUTPUT_TOKENS=5000` | 已对齐 |
| 最大轮次 | `max_turns=13` | `max_steps=13` | 已对齐 |
| 同工具重复上限 | `max_same_tool_call_rounds=3` | `repeat_call_limit=3` | 已对齐 |
| 采样参数（评测） | temperature 0.2 / top_p 0.95 / top_k 50 | 同左 | 已对齐 |
| 累计 token 上限 | **无**（训练中不存在此概念） | 300k 纯护栏 | 首轮评测误设为 80k（沿用了为 DeepSeek 成本控制的思路），导致 3 条 case 被误杀；本轮已修正重跑 |

另一个结构性事实：每轮模型调用都要重发完整对话历史，累计 token 中 **prompt 占绝对大头**（vLLM 约 52:1、DeepSeek 14:1），且随轮次近似平方增长。

## vLLM 4 条 exhausted 明细（对齐预算后重跑）

4 条全部撞 **13 轮步数上限**（训练契约 `max_turns=13`），不再有 token 护栏误杀：

| case | 撞到的预算 | 累计 token | 重复阻断 |
|---|---|---:|---:|
| 12090666 | 13/13 步 | 126,479 | 6 |
| 6dcca263 | 13/13 步 | 248,013 | 0 |
| 8a4741c4 | 13/13 步 | 54,841 | 10 |
| d306664e | 13/13 步 | 98,903 | 0 |

即：在训练契约内，这 4 条是模型** genuinely 没在 13 轮内收敛**（6dcca263 烧了 24.8 万累计 token、16 次工具调用仍未收尾），而非预算配置问题——这是 Voyager-4B 的真实能力边界。

## 逐条明细

| case | query | vLLM 状态/混合分 | DeepSeek 状态/混合分 |
|---|---|---|---|
| 00e0280e | 天津到福州后天出发，高铁和飞机票价差多少 | completed / 0.4365 | completed / 0.4365 |
| 12090666 | 我要怎么规划一条路线，能顺路走完金六古盐井、三清 | exhausted / -0.1176 | completed / 0.0325 |
| 258fcbf3 | 明天南昌到香港坐高铁和飞机哪个更快 | completed / 0.2965 | completed / 0.0865 |
| 3ec619d0 | 7月28号昆明到南宁坐高铁和飞机哪个更快 | completed / 0.3665 | completed / 0.5765 |
| 4d836a54 | 请帮我规划一下从慧通寺出发，途径观澜华府-星汉广 | completed / 0.5375 | completed / 0.6937 |
| 6dcca263 | 我想去天津蓟县旅游三天两夜。想去的景点有盘山。梨 | exhausted / -0.059 | completed / 0.7775 |
| 8a4741c4 | 一天时间里，既想在朱家尖体验水上项目，又想海边露 | exhausted / -0.101 | completed / 0.7025 |
| 9d66d721 | 8月6日早上从南宁嘉和城出发去玉林金谷商务大厦， | completed / 0.5065 | completed / 0.5765 |
| b28eaf9c | 请问大名豪家具店附近有什么卖礼品或者饰品的小店吗 | completed / 0.0865 | completed / 0.5065 |
| d306664e | 从龙岩博雅实验学校去泉商希尔顿酒店故乡中餐厅，高 | exhausted / -0.0675 | completed / 0.4365 |

> 注意：n=10 抽样，RL 分数与 judge 均有方差；单次对比不构成总体优劣结论。