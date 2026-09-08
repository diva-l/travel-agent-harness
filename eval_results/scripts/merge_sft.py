# -*- coding: utf-8 -*-
"""Merge the SFT LoRA adapter (checkpoint-420) onto Qwen3-4B-Instruct-2507."""
import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

BASE = "/root/autodl-tmp/pretrained/models/Qwen--Qwen3-4B-Instruct-2507/snapshots/master"
ADAPTER = "/root/autodl-tmp/output/qwen3_4b_sft_4gpu/v8-20260511-142452/checkpoint-420"
OUT = "/root/autodl-tmp/output/qwen3_4b_sft_merged_420"

print("loading base...", flush=True)
model = AutoModelForCausalLM.from_pretrained(BASE, torch_dtype=torch.bfloat16, device_map="cpu")
print("loading adapter...", flush=True)
model = PeftModel.from_pretrained(model, ADAPTER)
print("merging...", flush=True)
model = model.merge_and_unload()
print("saving to", OUT, flush=True)
model.save_pretrained(OUT, safe_serialization=True)
tok = AutoTokenizer.from_pretrained(ADAPTER if ( __import__("pathlib").Path(ADAPTER) / "tokenizer_config.json").exists() else BASE)
tok.save_pretrained(OUT)
print("done", flush=True)
