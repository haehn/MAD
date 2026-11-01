# MAD — Multi Agent Driver

Agents with Markdown-defined roles work together on a project. A Supervisor coordinates tasks and reviews results.

Start an OpenAI-compatible model server (see `mad_server`), then run an example:

```bash
# One coder builds a small solar system application
./go_mad.py examples/solar_simple.md

# Several agents collaborate on the same project
./go_mad.py examples/solar.md
```

Project files define the objective, workspace, optional success criteria, and agents. The first agent coordinates the team. Shared guidance lives in `mad/instructions.md`. Deliverables go in the workspace; readable Markdown logs and full JSONL records go in `.mad/audit` inside it. Handoffs reference saved workspace files with the message `files` field; recipients read those files before continuing.

Use `--server http://localhost:8012/v1` for another model endpoint, and `--model` or `--workspace` to override project settings.

![MAD screenshot](shot.png)
