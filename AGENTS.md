# MAD Development Rules

MAD means **Multi Agent Driver**: agents with roles communicate and perform work, while a Supervisor keeps the project on track.

## Simple is better

- Less is more. Write the smallest clear solution that meets the requirement.
- Prefer compact, readable, boring Python. Avoid clever tricks, code golf, and dense one-liners.
- Remove unnecessary indirection. A direct function call is often enough.
- Keep related code together. Multiple related classes in one file are fine; do not create a file for every class.
- Add an abstraction only when it removes real duplication or isolates a clear responsibility. Keep boundaries that already earn their place.
- Do not add managers, services, registries, factories, wrappers, or configuration for hypothetical future needs.
- Fewer files and functions are useful when they reduce conceptual overhead. Do not merge unrelated responsibilities just to reduce counts.
- Keep comments and documentation concise. Explain reasons and constraints rather than narrating obvious code.
- Do not add features or redesign the product during cleanup.

## Markdown guides; Python enforces

Put roles, responsibilities, planning and review guidance, judgment, and domain knowledge in Markdown. Shared agent guidance lives in `mad/instructions.md`; role templates live in `mad_agents/`.

Keep Python focused on mechanics: project parsing, async actors, messaging, model calls, action execution, and audit storage.

For example, reviewing actual results belongs in `Supervisor.md`; enforcing that only the Supervisor can finish the project belongs in Python.

Before adding Python complexity, ask whether the behavior belongs in an existing Markdown template.

## Current structure

- `mad/agents.py`: `Agent`, `Intelligence`, `Message`, and `Bus`; generic containers, model calls, and inbox transport.
- `mad/runtime.py`: project parsing, persistent async agent loops, action dispatch, CLI options, and audit records.
- `mad/ui.py`: console presentation and readable Markdown log formatting.
- `mad/instructions.md`: shared instructions loaded for every agent at startup.
- `mad_agents/`: Markdown role templates referenced by project files.
- `mad_skills/`: retained Markdown reference material; the runtime does not automatically load skills.
- `mad_server/`: model-serving launch scripts with a separate environment.
- `go_mad.py`: command-line entry point.
- `tests/`: regression tests for the current implementation.

Keep this layout small. Do not restore the old task graph, ProjectState, separate workspace tools, or protocol layers without a demonstrated need.

## Preserve the coordination model

- `Agent` contains only its name, intelligence link, and messaging link.
- Each agent runs as a persistent async actor with its own inbox and conversation. Different agents can work in parallel and consume messages between model calls.
- The first agent is the Supervisor. It coordinates through ordinary messages and is the only agent allowed to finish the project.
- Workers message only the Supervisor; the runtime enforces this routing rule. The Supervisor relays information and decides whether to dispatch independent work in parallel or await prerequisite deliveries. There is no Python task graph or task acceptance state machine.
- Agents send messages, execute Python, wait for messages, or finish through the existing JSON action protocol. Sending a message ends the turn and waits for new input by default; `"wait": false` explicitly continues the turn for more messages or independent work.
- Python execution uses the local environment and workspace as its working directory; it is not a filesystem sandbox.
- Handoffs use saved workspace files referenced by the message `files` field, including designs and review reports. The runtime validates references; recipients read files through Python. Questions and status messages can remain plain text.
- Keep `.mad/audit` separate from deliverables. Store complete JSONL events and human-readable Markdown logs.
- Preserve the project Markdown format and role template loading. Bare agent names and direct Python configuration are also supported.

## Keep MAD generic

MAD supports any kind of project, including research, writing, and software. Three.js is only an example. Keep domain-specific instructions in templates, skills, or project input; keep workspace tools generic.

Local and open models are first-class. Do not design around Codex, a single model vendor, or hypothetical enterprise backends.

## Make focused, verifiable changes

Follow the [Karpathy guidelines](mad_skills/external/karpathy/SKILL.md) when changing this repository.

- Inspect the relevant implementation before editing. State material assumptions and tradeoffs.
- Prefer the smallest fix. Do not refactor unrelated code while fixing a bug.
- Preserve behavior and passing tests during refactoring.
- Add a regression test for demonstrated bugs. Test meaningful behavior rather than mirroring implementation details.
- Run relevant checks; run the full test suite after refactoring. Report what was verified and any behavior changes.
- Do not modify skill contents unless the task requires it.
- Do not recreate removed README files or describe MAD as a separate prototype or comparison implementation.

When in doubt, choose the simpler implementation that is easy to read, explain, and test.
