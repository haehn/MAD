export VLLM_USE_FLASHINFER_SAMPLER=0

CUDA_VISIBLE_DEVICES=0 \
vllm serve Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 \
  --host 0.0.0.0 \
  --port 8024 \
  --language-model-only \
  --max-model-len 16834 \
  --max-num-seqs 1 \
  --max-num-batched-tokens 1024 \
  --enforce-eager \
  --download-dir "/tmp/models/" \
  2>&1 | tee vllm.log
