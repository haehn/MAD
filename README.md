# MAD

**MAD — Multi Agent Driver**

MAD is a lightweight framework for running projects with coordinated AI agents.

The idea is simple:

> Describe a project in one readable Markdown file and let MAD coordinate the agents, skills, tools, and models needed to complete it.

## Example

```md
Create a gamified collaboration platform with a minimal user interface.

WORKSPACE:
./test-project

SUCCESS:
- Users can complete tasks and earn points.
- Admins approve progress.
- A leaderboard ranks users.

AGENTS:
- Supervisor who supervises the project (Supervisor.md:Qwen/Qwen3-8B)
- Backend Coder who implements the API (Coder.md:Qwen/Qwen3-8B)
- Frontend Coder who implements the UI (Coder.md:Qwen/Qwen3-8B)
- Quality Control who verifies the result (SoftwareTester.md:Qwen/Qwen3-8B)
```

Run:

```bash
python go_mad.py examples/webapp.md
```

## Structure

```text
MAD/
├── mad/          # orchestration
├── madly/        # model serving / GPUs / SSH
├── mad_agents/   # reusable agent roles
├── mad_skills/   # reusable capabilities
├── examples/
└── go_mad.py
```

## Concepts

```text
Agent = who performs the work
Skill = what the agent knows how to do
Tool  = what the agent can execute
Model = the intelligence backing the agent
```

MAD is designed to support software development, research, analysis, writing, and other multi-agent projects.

## Architecture and tests

The Supervisor reviews recorded evidence against task criteria before accepting
work. A separate final review assesses the current combined result against the
project's success criteria. Evidence may be substantive text, source material,
files, calculations, observations, or tool results, depending on the project.

See [ARCHITECTURE.md](ARCHITECTURE.md) for component boundaries and the Supervisor review protocol.

```bash
python -m unittest discover -s tests -v
```
