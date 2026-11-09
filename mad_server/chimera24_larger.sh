export VLLM_USE_FLASHINFER_SAMPLER=0
export VLLM_ALLREDUCE_USE_FLASHINFER=0

CUDA_VISIBLE_DEVICES=0,1 \
vllm serve Qwen/Qwen3-Coder-Next \
  --host 0.0.0.0 \
  --port 8024 \
  --tensor-parallel-size 2 \
  --language-model-only \
  --max-model-len 32768 \
  --max-num-seqs 1 \
  --max-num-batched-tokens 2048 \
  --disable-custom-all-reduce \
  --gdn-prefill-backend triton \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --download-dir "/tmp/models2/" \
  2>&1 | tee vllm.log
