Run from the MAD repository root using the isolated CrewAI environment:

```bash
python -m venv /tmp/mad-crewai-env
/tmp/mad-crewai-env/bin/pip install -r mad_crewai/requirements.txt
/tmp/mad-crewai-env/bin/python -m mad_crewai mad_crewai/examples/solar.md
```

For a smaller, non-software project:

```bash
/tmp/mad-crewai-env/bin/python -m mad_crewai mad_crewai/examples/brief.md
```

The project Markdown declares PROJECT, WORKSPACE, INTELLIGENCE, AGENTS, INPUT, OUTPUT, ACCEPTANCE, OWNER, and QA. Structured sections contain YAML. Role paths resolve relative to the Markdown file. Workspace paths resolve relative to the current directory.

INTELLIGENCE supplies default `server` and `model`. Each AGENTS entry has `name` and a `role` Markdown path, and may override `server` and `model`. This supports a larger Supervisor, smaller role-specific workers, and a separate QA model. `--server` and `--model` override shared defaults while preserving explicit agent overrides. `--workspace` overrides the workspace. `MAD_API_KEY` supplies a server key when required.

The human project is the root contract. Planning is tool-free: the Supervisor returns contracts before inspecting future deliverables. The Supervisor plans flat delegated contracts with SETTING, INPUT, TASK, OUTPUT, ACCEPTANCE, OWNER, QA, and DEPENDS_ON. Python validates and saves them before work starts. Accepted prerequisite file paths and inline values are inserted into dependent contracts. Independent owners run concurrently; each agent has at most one active assignment, including QA.

Every delivery goes to its separately assigned QA agent, then to Supervisor review. QA must cover every acceptance condition and cite its own recorded observations. Failed or unverified checks prevent acceptance. Corrections create revised contracts and repeat QA. Accepted work unlocks dependencies. Root QA checks the integrated project after all delegated contracts are accepted. Root failures can spawn corrective contracts followed by another root QA pass. The default limit is three revisions per contract; the Supervisor can stop honestly.

`agent.py` wraps CrewAI role and model configuration. `contract.py` holds the contract and project parsing. `Runtime.assemble()` creates the configured team; `runtime.py` schedules work, QA, and Supervisor decisions. Each assignment uses a fresh CrewAI executor. Native hierarchical delegation is disabled so it cannot bypass saved contracts or QA. `workspace.py` handles generic execution, artifact validation, and evidence. `ui.py` shows roles, models, contract IDs, revisions, phases, and state changes.

Audit files live in `.mad/audit/<run>/`: complete events in `events.jsonl`, readable Markdown in `events.md`, and verbose CrewAI output in `crew-output.log`. Saved contract revisions live in `.mad/contracts/<run>/`. Actual deliverables remain outside `.mad`. CLI exit 0 requires root QA and Supervisor acceptance; a stopped or failed project exits nonzero.

Worker Python has local environment permissions, not a filesystem sandbox. Install project-specific verification dependencies in the worker environment. QA is instructed not to edit outputs; declared output changes during QA prevent acceptance. Evidence references prove which reads or executions happened; the models still judge their relevance and sufficiency.

CrewAI uses native tool calls. For Qwen3-Coder, add these flags to your existing vLLM command, retaining its host, port, and memory settings:

```bash
--enable-auto-tool-choice --tool-call-parser qwen3_coder
```

See [vLLM tool calling](https://docs.vllm.ai/en/latest/features/tool_calling/). Other models require their appropriate parser. Shared servers can serve concurrent requests; actual throughput depends on their model and serving capacity.

Tests:

```bash
python -m unittest discover -v
/tmp/mad-crewai-env/bin/python -m unittest discover -v
```

Model requests time out after 120 seconds without hidden client or CrewAI retries. Invalid structured responses get up to three audited correction attempts. Tool activity appears in the console; failed reads are recorded in the event audit.
