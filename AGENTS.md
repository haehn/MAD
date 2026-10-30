# MAD Development Rules

MAD = Multi Agent Driver.

MAD is a lightweight, model-agnostic multi-agent orchestration layer driven by Markdown.

Keep it small. Do not over-engineer.

## Core distinction

**Markdown defines agent intelligence and behavior.**

Put in Markdown/templates/skills:
- role and responsibilities
- judgment and decision-making
- planning/review philosophy
- how agents should evaluate evidence
- domain-specific guidance

**Python defines mechanics and enforcement.**

Put in Python:
- parsing
- task state and transitions
- scheduling
- messaging
- model calls
- tools
- evidence collection
- schema validation
- filesystem safety
- retries
- audit storage

Example:
- "Be skeptical of unsupported completion claims" → `Supervisor.md`
- "Only Supervisor may move review → completed" → Python

## Architecture rules

- Workers perform work; Supervisor owns the task graph and acceptance.
- Worker success moves `running → review`.
- Only Supervisor moves `review → completed`.
- Use real evidence instead of trusting agent claims.
- Keep tools generic and domain-independent.
- Do not hard-code Three.js, coding, or other project-specific behavior into the core.
- Agent = who works.
- Skill = reusable knowledge.
- Tool = executable capability.
- Model = intelligence.

## Engineering rules

- Prefer the smallest fix.
- Do not add abstractions for hypothetical future needs.
- Do not refactor unrelated code while fixing a bug.
- Prefer existing files over new managers/services/registries/factories.
- Add a regression test for demonstrated bugs.
- Preserve the Markdown project format, templates, and skill semantics.
- Local/open models are first-class; do not design MAD around Codex.

Before adding complexity, ask:
**Could this behavior live in Markdown instead of Python?**
