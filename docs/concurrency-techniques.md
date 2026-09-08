# 微服务并发优化调研 → 本项目映射（2026-09-07）

来源：两轮联网调研（见文末链接），结合 TravelAgentHarness 实测瓶颈逐条映射。
实测瓶颈排序（压测证据）：**API worker 池(写死2) > 外部工具 API 限流(高并发退化) > vLLM 算力 > harness 内部逻辑(≈0 开销)**。

## 第一轮：通用微服务手段

| 手段 | 通用做法 | 本项目落地状态 |
|---|---|---|
| 线程池调参 | IO 密集型按下游承载定上限 | **已落地**：`TRAVEL_HARNESS_API_WORKERS`（默认 2）。实测甜区 16（763 任务/时），32 不升——瓶颈不在池子大小而在下游 |
| 有界队列 + 背压 | 有界队列 + 满则拒/CallerRuns | **已落地**：`TRAVEL_HARNESS_API_QUEUE`（默认 32），超额提交返回 503 + Retry-After，不再无界静默排队 |
| 限流/熔断/降级 | QPS/并发限流，下游故障快速失败 | **已落地（客户端侧）**：外部工具 HTTP 走 urllib3 池（`block=True`，池满即排队=背压），429/5xx/传输错误指数退避重试；`TRAVEL_HARNESS_TOOL_HTTP_POOL`(8) / `_RETRIES`(2)。任务内限流（重复阻断/预算熔断）此前已有 |
| 连接复用 | 连接池 keep-alive | **已落地**：`http_client.PooledHttpClient` 复用 TCP/TLS，替代每调用新建连接 |
| 缓存 | 本地+Redis 多级 | **已落地（默认关）**：`TRAVEL_HARNESS_TOOL_CACHE_TTL`，同参数高德/Firecrawl 响应去重；评测默认 0 保证轨迹确定性，运营场景建议 300s |
| 异步化/非阻塞 IO | 消息队列削峰、非阻塞客户端 | 工具执行已线程池并行；vLLM 侧 prefix cache 88% 命中已是最大杠杆，异步化收益低 |
| 横向扩展 | 多实例+负载均衡 | 单卡单 vLLM 实例；超出现有硬件，未做 |
| 减少锁竞争 | 分片锁 | SQLite WAL 实测未成瓶颈（harness 开销≈0），不需要 |
| 监控 | QPS/RT/队列水位告警 | 压测脚本产出吞吐/延迟/GPU/vLLM 指标；生产化可加 /metrics 导出 |

## 第二轮：vLLM 侧与 FastAPI 部署侧

### vLLM 并发调优参数（本次未改，记录甜区判据）

| 参数 | 作用 | 本项目现状 |
|---|---|---|
| `--max-num-seqs` | 并发序列上限，超出排队 | 默认 256，实测 u32 未打满（decode 峰值 375 tok/s），**不是瓶颈** |
| `--max-num-batched-tokens` | 每调度步 token 数（V1 恒为 chunked prefill）| 默认；prompt:decode ≈ 28:1 的 workload 下放大可提 TTFT |
| `--gpu-memory-utilization` | KV 池占比 | 0.90，50k 上下文下 KV 充足 |
| prefix caching | 系统提示+多轮历史复用 | 已开，实测命中 88%，是最大减压阀 |

甜区判据（Red Hat 指南）：吞吐平台 + TTFT 恶化 = prefill 受限 → 调 max-num-batched-tokens；
ITL 上升 = decode 受限 → 调 max-num-seqs。本项目实测是**外部 API 受限**，vLLM 调参 ROI 低。

### FastAPI/Uvicorn 部署侧

- 阻塞任务严禁直接在 async 路由里跑 → 本项目架构已正确：路由只做 DB 读写，
  agent 循环全在 TaskService 的 ThreadPoolExecutor 里
- uvicorn `--workers` 多进程：本项目任务状态在 SQLite(WAL) + 进程内执行器，
  多进程会分裂执行器语义（每进程独立池子、审批/恢复路由错进程即失败），
  **不适用**；单进程 + 可调线程池是正解
- anyio 默认线程池 min(32, cpu+4) 只服务路由层，不是任务并发上限——
  任务并发的正确旋钮就是 TaskService 的 max_workers（已可配置化）

## 优化落地清单（2026-09-07 晚）

1. `TRAVEL_HARNESS_API_WORKERS` — API 执行池可配置（压测发现写死 2）
2. `TRAVEL_HARNESS_API_QUEUE` + 503 背压（有界队列）
3. `http_client.PooledHttpClient` — 连接复用 + 客户端限流 + 退避重试
4. `TRAVEL_HARNESS_TOOL_CACHE_TTL` — TTL 响应缓存（默认关）
5. 截断类解析错误（finish_reason=length）改为可重试 —— 压测暴露的全部 4 次 failed 都是这类

测试：61/61 通过（新增 8 个：池重试×3、缓存×3、503 背压、截断重试×2）。

## 结论（按 ROI 排序）

1. **worker 池可配置 + 对齐实测甜区（16）** —— 已落地，161→763 任务/时
2. **队列背压** —— 已落地（503+Retry-After），防雪崩
3. **外部工具 API 客户端限流+退避+连接复用** —— 已落地，针对高并发退化根因
4. **POI/搜索短时缓存** —— 已落地（开关默认关），运营场景可开
5. vLLM 调参（max-num-seqs / batched-tokens）—— 实测未饱和，暂缓
6. uvicorn 多进程 —— 与执行器语义冲突，不适用

## 来源

第一轮（通用微服务）：
- [背压机制——MQ与线程池协调桥梁（博客园）](https://www.cnblogs.com/xtkyxnx/p/19063268)
- [优化线程池：提升系统吞吐量的策略（CSDN）](https://blog.csdn.net/weixin_47681367/article/details/129397601)
- [Java高性能线程池优化与异步编程实战（CSDN）](https://blog.csdn.net/CwHPRxCp/article/details/154014365)
- [如何让你的系统抗住高并发流量（博客园）](https://www.cnblogs.com/jimoer/p/19523255)
- [如何解决高并发场景下的性能瓶颈（PingCode）](https://docs.pingcode.com/baike/5200503)
- [高并发下锁竞争排查与优化（博客园）](https://blog.csdn.net/qq_44378083/article/details/147376192)
- [基于Spring Boot的微服务高并发性能优化（云原生实践）](https://www.oryoy.com/news/ji-yu-spring-boot-de-wei-fu-wu-jia-gou-zai-gao-bing-fa-chang-jing-xia-de-xing-neng-you-hua-yu-xiang.html)

第二轮（vLLM / FastAPI）：
- [Practical strategies for vLLM performance tuning（Red Hat）](https://developers.redhat.com/articles/2026/03/03/practical-strategies-vllm-performance-tuning)
- [vLLM Continuous Batching Tuning Guide（GigaGPU）](https://gigagpu.com/vllm-continuous-batching-tuning-guide/)
- [LLM Serving Optimization: Continuous Batching, PagedAttention, Chunked Prefill（Spheron）](https://www.spheron.network/blog/llm-serving-optimization-continuous-batching-paged-attention/)
- [The Swapping Cliff: vLLM 高并发延迟尖峰（azguards）](https://azguards.com/ai-infrastructure/the-swapping-cliff-mitigating-latency-spikes-in-vllm-high-concurrency-workloads/)
- [FastAPI 中的并发：深入理解 Worker 与线程（掘金）](https://juejin.cn/post/7431754032449847305)
- [FastAPI 生产部署实战：Gunicorn + Uvicorn 与进程管理](https://www.wxy.email/archives/5a6cf9ed.html)
- [FastAPI 部署演进：从 Gunicorn+Uvicorn 到纯 Uvicorn（CSDN）](https://blog.csdn.net/weixin_42523907/article/details/161031417)
- [uvicorn 如何调节线程池大小](https://utcz.com/p/938412.html)
