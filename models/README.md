# 模型权重放置目录

训练权重不进 Git 仓库，也不进发布压缩包。权重托管在 Hugging Face，下载后放在这里：

```bash
hf download fantastic-youki/Voyager-4B --local-dir models/Voyager-4B
```

目录结构：

```text
models/
└── Voyager-4B/      # RL 最终权重（Qwen3-4B，bf16，SFT + GRPO 后训练）
    ├── config.json
    ├── model.safetensors
    ├── tokenizer.json
    └── ...
```

然后用 vLLM 暴露 OpenAI-compatible API：

```bash
VLLM_USE_FLASHINFER_SAMPLER=0 python -m vllm.entrypoints.openai.api_server \
  --model models/Voyager-4B --served-model-name travel-planner \
  --max-model-len 50000 --port 8000
```

配合 `.env` 中 `TRAVEL_HARNESS_PLANNER_MODE=vllm` 即可接入 Harness。
详见 [../docs/deploy-vllm-server.md](../docs/deploy-vllm-server.md)。
