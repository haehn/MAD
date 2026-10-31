export VLLM_USE_FLASHINFER_SAMPLER=0

CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 \
vllm serve Qwen/Qwen3-Coder-Next \
  --tensor-parallel-size 8 \
  --max-model-len 32768 \
  --host 0.0.0.0 \
  --port 8012 \
  --download-dir "/tmp/models/" \
  2>&1 | tee vllm.log
