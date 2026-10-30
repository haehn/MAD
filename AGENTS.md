# MAD Development Rules

MAD means **Multi Agent Driver**: agents with roles and skills communicate and perform tasks, while a Supervisor keeps the project on track.

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

Put roles, responsibilities, planning and review guidance, judgment, and domain knowledge in Markdown templates and skills.

Keep Python focused on mechanics: parsing, state transitions, scheduling, messaging, model calls, tools, schemas, evidence, retries, filesystem safety, and audit storage.

For example, skepticism about unsupported claims belongs in `Supervisor.md`; enforcing that only the Supervisor can accept a task belongs in Python.

Before adding Python complexity, ask whether the behavior belongs in an existing Markdown template.

## Current structure

- `mad/agent.py`: `Agent`, `Skill`, model interaction, and action schemas/parsing.
- `mad/project.py`: `Project`, `Task`, and `ProjectState`; project parsing and task-state enforcement.
- `mad/messaging.py`: `Message` and `MessageHub`; message transport.
- `mad/runtime.py`: `Runtime`, the orchestration entry point; scheduling and action dispatch.
- `mad/workspace.py`: generic workspace tools, path safety, evidence, and audit storage.
- `mad/ui.py`: presentation and logging output through simple functions.
- `mad_agents/`: role templates and shared turn instructions.
- `mad_skills/`: reusable Markdown guidance.
- `mad_server/`: model-serving launch scripts.
- `go_mad.py`: command-line entry point.

Extend these files where the responsibility fits. Do not recreate separate model-client, protocol, skill, task, project-state, or reporter layers without a demonstrated need.

## Preserve the coordination model

- An agent performs work; its role comes from a Markdown template; skills provide reusable guidance; tools execute capabilities.
- The Supervisor is an ordinary agent using the same work loop, with authority over task-plan mutation and acceptance.
- Workers submit successful work as `running → review`. Only the Supervisor can accept it as `review → completed`.
- Acceptance requires evidence. Agent claims alone do not prove that work exists or meets its criteria.
- Keep task state and authority enforcement in `ProjectState`, not in presentation or messaging.
- Keep peer conversations bounded; acknowledgements must not create endless reply loops.
- Keep `.mad/audit` separate from deliverables and inaccessible through agent workspace tools.
- Preserve the current project Markdown format and agent/template/skill concepts.

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
