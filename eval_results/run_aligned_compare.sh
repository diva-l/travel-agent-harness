#!/bin/bash
# Training-aligned 4-way comparison: base / SFT / RL-150 / DeepSeek.
# Aligns the harness runtime with the RL training loop terminal rules and
# observation distribution (travel_agentic_rl/run_tool_loop_infer.py):
#   - force-answer injection at step 12 (verbatim training message)
#   - one forced-answer chance on repeat-block (verbatim training message)
#   - tool observations capped at 5000 chars (tool_response_max_chars)
#   - visit pages distilled by deepseek-v4-flash with EXTRACTOR_PROMPT
#     (same model the training-side visit tool used)
# Usage: bash eval_results/run_aligned_compare.sh
set -u
cd /root/autodl-tmp/TravelAgentHarness
V=.venv/bin
BASE=/root/autodl-tmp/pretrained/models/Qwen--Qwen3-4B-Instruct-2507/snapshots/master
SFT=/root/autodl-tmp/output/qwen3_4b_sft_merged_420
RL=/root/autodl-tmp/output/grpo_parser_aligned_run/v8-20260514-140934/checkpoint-150

# Training-aligned runtime knobs (read by HarnessConfig.from_env).
export TRAVEL_HARNESS_FORCE_ANSWER_AFTER_STEPS=12
export TRAVEL_HARNESS_REPEAT_ANSWER_CHANCE=true
export TRAVEL_HARNESS_MAX_TOOL_OUTPUT_CHARS=5000
export TRAVEL_HARNESS_VISIT_EXTRACTOR=true
# Deep parity additions: train/flight tickets are LLM-simulated exactly like
# the training loop (verbatim simulator prompts); tool observations are
# rendered as json2md markdown + training-format search/visit text; the
# dataset's fixed date replaces today's date in the planner prompt.
export TRAVEL_HARNESS_TICKET_SIMULATOR=true
export TRAVEL_HARNESS_TRAINING_TOOL_FORMAT=true
export TRAVEL_HARNESS_CURRENT_DATE=2026-04-15
# Pin the extractor to the exact model the training-side visit tool used,
# independent of planner mode.
export TRAVEL_HARNESS_REPORT_BASE_URL=https://api.deepseek.com
export TRAVEL_HARNESS_REPORT_MODEL=deepseek-v4-flash

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

run_eval() {  # $1=mode  $2=tag
    rm -f /root/autodl-tmp/eval-$1.db eval_results/results_$1.jsonl
    $V/python eval_results/run_eval.py --mode "$1" > "/tmp/eval-$2.log" 2>&1
    local rc=$?
    if [ "$1" != "$2" ]; then
        [ -f eval_results/results_$1.jsonl ] && mv eval_results/results_$1.jsonl "eval_results/results_${1}_$2.jsonl"
        [ -f /root/autodl-tmp/eval-$1.db ] && mv /root/autodl-tmp/eval-$1.db "/root/autodl-tmp/eval-$1-$2.db"
    fi
    echo "[$2] eval exit=$rc"
    return $rc
}

echo "=== BASE ===";  serve_and_wait "$BASE" base  && run_eval vllm base
echo "=== SFT ===";   serve_and_wait "$SFT"  sft   && run_eval vllm sft
echo "=== RL150 ==="; serve_and_wait "$RL"   rl150 && run_eval vllm rl150
echo "=== API (DeepSeek) ==="; pkill -f "vllm serve" 2>/dev/null; sleep 3; run_eval api api
echo "=== RESTORE rl150 service ==="; serve_and_wait "$RL" rl150
echo "=== COMPARE ==="; $V/python eval_results/compare_checkpoints.py > /tmp/compare.log 2>&1; echo "compare exit=$?"
echo "PIPELINE DONE"
