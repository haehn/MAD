# ============================================================
# CHIMERA24: WORKING vLLM / H200 SETUP
# ============================================================

# ------------------------------------------------------------
# 1. System / admin configuration
# ------------------------------------------------------------

# NVIDIA driver was upgraded to:
#   595.91.07
#
# nvidia-smi reports:
#   CUDA Version: 13.2
#
# GPU:
#   NVIDIA H200
#
# MIG is enabled, and the visible partition is:
#   NVIDIA H200 MIG 1g.35gb


# ------------------------------------------------------------
# 2. Create a fresh Conda environment
# ------------------------------------------------------------

conda create -n VLLM python=3.12 pip -y

conda activate VLLM


# ------------------------------------------------------------
# 3. Install vLLM
# ------------------------------------------------------------

python -m pip install vllm


# ------------------------------------------------------------
# 4. Verify PyTorch / CUDA / GPU
# ------------------------------------------------------------

python -c "
import torch
print(torch.__version__)
print(torch.version.cuda)
print(torch.cuda.is_available())
print(torch.cuda.get_device_name(0))
"

# Working output:
#
# 2.13.0+cu130
# 13.0
# True
# NVIDIA H200 MIG 1g.35gb


# ------------------------------------------------------------
# 5. Verify vLLM
# ------------------------------------------------------------

python -c "import vllm; print(vllm.__version__)"


# ------------------------------------------------------------
# 6. Start a model
# ------------------------------------------------------------

CUDA_VISIBLE_DEVICES=0 \
vllm serve Qwen/Qwen3-8B \
  --host 0.0.0.0 \
  --port 8000


# ------------------------------------------------------------
# 7. Verify that the API server is running
# ------------------------------------------------------------

curl http://localhost:8000/v1/models

# This successfully returned:
#
# Qwen/Qwen3-8B
#
# with max_model_len 40960


# ------------------------------------------------------------
# 8. Test actual inference
# ------------------------------------------------------------

curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-8B",
    "messages": [
      {
        "role": "user",
        "content": "Reply with exactly: vLLM works"
      }
    ],
    "max_tokens": 20
  }'


# ------------------------------------------------------------
# 9. Useful monitoring
# ------------------------------------------------------------

watch -n 1 nvidia-smi

# Check listener:
ss -ltnp | grep 8000

# Check process:
ps aux | grep '[v]llm'