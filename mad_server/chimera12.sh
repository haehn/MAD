export VLLM_USE_FLASHINFER_SAMPLER=0

CUDA_VISIBLE_DEVICES=0 \
vllm serve Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 \
  --tensor-parallel-size 1 \
  --max-model-len 16384 \
  --gpu-memory-utilization 0.92 \
  --host 0.0.0.0 \
  --port 8012 \
  --download-dir "/tmp/models/" \
  2>&1 | tee vllm.log
