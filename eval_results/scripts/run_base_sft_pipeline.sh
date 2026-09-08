#!/bin/bash
# Base + SFT eval pipeline: switch vLLM model, run 10-case eval, archive, restore.
# Usage: bash eval_results/run_base_sft_pipeline.sh
set -u
cd /root/autodl-tmp/TravelAgentHarness
V=.venv/bin
BASE=/root/autodl-tmp/pretrained/models/Qwen--Qwen3-4B-Instruct-2507/snapshots/master
SFT=/root/autodl-tmp/output/qwen3_4b_sft_merged_420
RL=/root/autodl-tmp/TravelPlanner-4B

serve_and_wait() {  # $1=model path  $2=log tag
    pkill -f "vllm serve" 2>/dev/null; sleep 5
    setsid nohup env VLLM_USE_FLASHINFER_SAMPLER=0 $V/vllm serve "$1" \
        --served-model-name travel-planner --max-model-len 50000 \
        --gpu-memory-utilization 0.90 --port 8000 > "/tmp/vllm-$2.log" 2>&1 < /dev/null &
    for i in $(seq 1 60); do
        sleep 10
        curl -s -m 2 http://127.0.0.1:8000/v1/models 2>/dev/null | grep -q travel-planner && { echo "[$2] vllm READY (~$((i*10))s)"; return 0; }
        grep -q "RuntimeError\|failed to start" "/tmp/vllm-$2.log" && { echo "[$2] STARTUP FAILED"; return 1; }
    done
    echo "[$2] TIMEOUT"; return 1
}

run_eval() {  # $1=tag
    rm -f /root/autodl-tmp/eval-vllm.db eval_results/data/results_vllm.jsonl
    $V/python eval_results/scripts/run_eval.py --mode vllm > "/tmp/eval-$1.log" 2>&1
    local rc=$?
    [ -f eval_results/data/results_vllm.jsonl ] && mv eval_results/data/results_vllm.jsonl "eval_results/data/results_vllm_$1.jsonl"
    [ -f /root/autodl-tmp/eval-vllm.db ] && mv /root/autodl-tmp/eval-vllm.db "/root/autodl-tmp/eval-vllm-$1.db"
    echo "[$1] eval exit=$rc"
    return $rc
}

echo "=== BASE ==="; serve_and_wait "$BASE" base && run_eval base
echo "=== SFT ===";  serve_and_wait "$SFT"  sft  && run_eval sft
echo "=== RESTORE rl150 ==="; serve_and_wait "$RL" rl150
echo "=== COMPARE ==="; $V/python eval_results/scripts/compare_checkpoints.py > /tmp/compare.log 2>&1; echo "compare exit=$?"
echo "PIPELINE DONE"
