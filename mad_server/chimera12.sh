export VLLM_USE_FLASHINFER_SAMPLER=0

CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 \
vllm serve Qwen/Qwen3.5-397B-A17B \
  --tensor-parallel-size 8 \
  --language-model-only \
  --max-model-len 65536 \
  --host 0.0.0.0 \
  --port 8000
