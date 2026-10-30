CUDA_VISIBLE_DEVICES=0 \
vllm serve Qwen/Qwen3.5-27B-GPTQ-Int4 \
  --host 0.0.0.0 \
  --port 8000 \
  --language-model-only \
  --reasoning-parser qwen3 \
  --max-model-len 16384 \
  --max-num-seqs 2 \
  --gpu-memory-utilization 0.90

