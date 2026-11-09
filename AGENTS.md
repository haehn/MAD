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

Keep Python focused on project parsing, contract state and validation, generic tools, CrewAI task assembly, and audit storage.

For example, reviewing actual results belongs in `Supervisor.md`; enforcing that only the Supervisor can finish the project belongs in Python.

Before adding Python complexity, ask whether the behavior belongs in an existing Markdown template.

## Current structure

- `mad/contracts.py`: plain Contract data and Pydantic Plan, Delivery, QC, acceptance, and completion models.
- `mad/runtime.py`: existing project parsing, `Runtime`, direct CrewAI Agent/Task/Crew assembly, generic execution, and audit records.
- `mad/ui.py`: colored console presentation and readable Markdown log formatting.
- `mad/instructions.md`: shared guidance loaded for every configured agent.
- `mad_agents/`: role templates and the contract guide loaded for Supervisor.
- `mad_skills/`: retained reference material; skills are not automatically loaded.
- `mad_server/`: model-serving scripts with a separate environment.
- `go_mad.py`: command-line entry point.
- `tests/`: project parsing, UI, and native CrewAI lifecycle tests.
- `_LEGACY/`: reference implementations; do not restore their infrastructure.

## Preserve the coordination model

- The project Markdown defines the objective and available roles. The first role is Supervisor. Preserve role templates, model suffixes, bare names, and direct Python configuration.
- Supervisor divides the project into Contracts: owner (configured worker name), input, task, output, acceptance criteria, and dependencies. Supervisor owns acceptance and the final project decision.
- Workers execute native CrewAI Tasks and return Deliveries. Output paths name files; an empty output list means the substantive result is delivered inline.
- Every Delivery goes immediately to a separate QC agent through the worker Task's guardrail. QC returns PASS or FAIL with findings. FAIL returns the contract to the worker through CrewAI's correction retries.
- Contract states are OPEN → DELIVERED → QC → ACCEPTED; QC failure returns to OPEN. Supervisor accepts QC-passed contracts before dependents start.
- Use native async Tasks for independent work, native context for dependencies, and synchronous Supervisor checkpoints to join parallel tasks. Do not recreate schedulers, actor queues, message buses, registries, or persistence layers.
- Keep role judgment and verification guidance in Markdown. Python enforces declared file paths, state, and authority. Agent claims do not prove observable behavior was tested.
- Generic Python tools use the workspace as their working directory and are not a filesystem sandbox.
- Keep `.mad/audit` separate from deliverables. Preserve readable Markdown, full JSONL records, actual execution results, and detailed CrewAI diagnostics.

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
