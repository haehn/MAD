conda activate VLLM

CUDA_VISIBLE_DEVICES=0 \
vllm serve Qwen/Qwen3-8B \
  --host 0.0.0.0 \
  --port 8000
