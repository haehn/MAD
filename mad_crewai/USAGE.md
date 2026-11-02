Run from the MAD repository root. Use a separate environment to keep CrewAI's dependencies separate from MAD:

```bash
python -m venv /tmp/mad-crewai-env
/tmp/mad-crewai-env/bin/pip install -r mad_crewai/requirements.txt
/tmp/mad-crewai-env/bin/python -m mad_crewai mad_crewai/examples/solar.md --server http://localhost:8012/v1
```

`--model`, `--workers`, and `--workspace` override the defaults. `MAD_API_KEY` supplies an API key if the server requires one. Workspace paths are relative to the current directory, as in the existing examples. Worker code uses this environment's Python; install any project-specific verification dependencies there.

CrewAI uses native tool calls. For Qwen3-Coder, add these flags to your existing vLLM launch command, keeping its host, port, and memory settings:

```bash
--enable-auto-tool-choice --tool-call-parser qwen3_coder
```

See [vLLM tool calling](https://docs.vllm.ai/en/latest/features/tool_calling/) and the [Qwen3-Coder recipe](https://docs.vllm.ai/projects/recipes/en/stable/Qwen/Qwen3-Coder-480B-A35B.html). Other models need their appropriate parser.

`runtime.py` uses CrewAI's hierarchical process with one Supervisor and generic workers. Markdown supplies their guidance. The existing project parser is reused; existing MAD code is unchanged. Workers execute Python with local environment permissions, not a filesystem sandbox.

This version uses CrewAI delegation rather than MAD's actor scheduler and contract state machine. Contract saving, per-package acceptance, and dependency ordering are instructions to the model, not Python-enforced guarantees. Native hierarchical delegation does not provide MAD's parallel actor execution. A successful CLI exit means CrewAI returned a report, not that the project passed independent verification; read the final acceptance decision.

Actual execution code, stdout, stderr, failures, and the final report are saved in `.mad/audit/<run>/events.jsonl` and readable `events.md`. CrewAI's native log is saved separately as `crew.json`. Audit files are separate from deliverables; generated Python retains normal local filesystem permissions.

Tests:

```bash
/tmp/mad-crewai-env/bin/python -m unittest mad_crewai.test_runtime -v
```
