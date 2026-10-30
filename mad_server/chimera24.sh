CUDA_VISIBLE_DEVICES=0 \
vllm serve Qwen/Qwen3.5-27B-GPTQ-Int4 \
  --host 0.0.0.0 \
  --port 8000 \
  --language-model-only \
  --max-model-len 8192 \
  --max-num-seqs 1 \
  --max-num-batched-tokens 1024 \
  --enforce-eager \
  2>&1 | tee vllm.log
