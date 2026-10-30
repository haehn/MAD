# MAD architecture

MAD means **Multi Agent Driver**. `Runtime` remains the orchestration entry point.
The CLI and project Markdown format (`WORKSPACE:`, optional `SUCCESS:`, and
`AGENTS:`) are unchanged. Agent/template/skill concepts and skill contents are unchanged. Supervisor
planning and review guidance lives in `mad_agents/core/Supervisor.md`; shared task
guidance lives in `mad_agents/TASK.md`. Python assembles current context and
enforces the protocol, rather than embedding that guidance in its source.

## Responsibilities

| Responsibility | Module / class |
| --- | --- |
| Project specification and Markdown parsing | `project.py` / `Project`, `project_parser.py` / `ProjectParser` |
| Agent definitions, templates, and skills | `agent.py` / `Agent`, `skill.py` / `Skill` |
| Task graph, validation, transitions, atomic decisions | `project_state.py` / `ProjectState`, `task.py` / `Task` |
| Composition, scheduling, and lifecycle | `runtime.py` / `Runtime` |
| Agent inbox dispatch and failure reporting | `agent_runtime.py` / `AgentRuntime` |
| Worker model/tool loop | `task_executor.py` / `TaskExecutor` |
| Model invocation, retries, and JSON recovery | `model_session.py` / `ModelSession` |
| HTTP model adapter | `llm.py` / `LLM` |
| Workspace files, commands, processes, browser checks | `workspace.py` / `Workspace` |
| Per-task tool policy and evidence tracking | `tool_session.py` / `ToolSession` |
| Structured workspace outcomes | `tool_result.py` / `ToolResult` |
| Inbox transport and peer routing | `message_hub.py` / `MessageHub`, `message_router.py` / `MessageRouter` |
| Supervisor planning and review model protocol | `supervisor_protocol.py` / `SupervisorProtocol` |
| Review evidence assembly and submission | `review_service.py` / `ReviewService` |
| Internal task reports | `audit_store.py` / `AuditStore` |
| Presentation | `reporter.py` / `Reporter` |
| Shared wire schemas and JSON parsing | `protocol.py` |

Class names correspond to module filenames in snake case. `mad.parser` remains
an import compatibility shim for `ProjectParser`.

## Execution and acceptance

1. `Runtime` identifies exactly one Supervisor and creates each agent inbox,
   model backend, and `AgentRuntime`.
2. `SupervisorProtocol` requests an initial plan. `ProjectState` validates it
   before installing it, including owners, IDs, dependencies, and cycles.
3. `Runtime` dispatches pending tasks whose dependencies have been accepted.
   Workers receive task copies; task status is the source of truth for running
   and completed task sets.
4. `TaskExecutor` runs generic tools through a per-task `ToolSession`. The
   session enforces read-before-overwrite and collects file, command, and browser
   evidence. Structured outcomes carry command/browser success independently of
   their display text. Tool failures remain visible to the worker and Supervisor.
5. A successful worker claim moves a running task to **review**. A blocked
   result moves it to **blocked**. Only its assigned worker may submit a result.
6. `ReviewService` retains worker submissions and complete tool requests/results
   with evidence IDs, obtains a current workspace listing, and sends the evidence
   to the Supervisor. Full records stay under `.mad/audit`; large model-context
   excerpts are explicitly marked. Listing and file tools exclude `.mad`.
7. The Supervisor can inspect artifacts and run suitable checks during review.
   Direct `write`/`append` tools are unavailable in review; correction is assigned
   as work. No particular tool is required for inline conceptual deliverables.
8. Only Supervisor responses may change the plan. Actions are applied to a copy
   and committed after validation. `accept` requires a task in review plus one
   assessment for each exact acceptance criterion: `criterion`, a substantive
   `reason`, and nonempty `evidence` references to supplied IDs. Tasks without
   acceptance criteria use their description or title. Missing assessments,
   unknown evidence IDs, and duplicate/missing criteria reject the transaction.
   New tasks start pending; successful worker claims never complete tasks.
   Malformed Supervisor decisions are returned once to the Supervisor with the precise
   validation error and current evidence. A second invalid decision stops the run
   without accepting work; protocol mistakes do not silently rerun workers.
9. Review feedback accompanies redispatched work, so reopened tasks retain the
   correction context. Dependencies unlock after task acceptance.
10. Once tasks are accepted/cancelled, the runtime requests a separate final
    project review against `SUCCESS` (or the objective if absent). Earlier task
    acceptance is insufficient. File-based projects require each final assessment
    to cite fresh successful inspection evidence from this review. A directory
    listing alone is insufficient. Inline outputs can be assessed directly.
11. A final review can reopen/revise/add work; any graph change requires another
    final review after the work settles. `Runtime.project_accepted` becomes true
    only after a valid explicit final approval. Blocked/deadlocked runs may end
    without approval; ending a run is not equivalent to project acceptance.

Task Markdown reports retain the submitted status. `<task>-review.json` records
current Supervisor decisions and full evidence; corresponding `.jsonl` files retain
history across corrective attempts. `project-review.json` records final approval
or rejection. Audit records are distinct from actual deliverables.

These checks enforce review structure, evidence provenance, and explicit
acceptance. Whether evidence actually establishes a claim still requires the
Supervisor's judgment. They do not make a successful command, browser load, or
citation intrinsically sufficient. Projects supply the substantive criteria;
the runtime contains no domain-specific correctness rules.

Worker failures are sent to the runtime as blocked task results. Supervisor
planning/review failures propagate to the caller, so the runtime does not wait
forever for a response. Shutdown cancels inbox loops and stops workspace servers.

## Model serving and presentation

`LLM` preserves the existing OpenAI-compatible HTTP request format, JSON schema
requests, timeout, and thinking-display setting. The deployment scripts under
`madly/` remain separate from execution and are unchanged. `Runtime` accepts a
`model_factory(agent)` returning an object with `chat(...)`, allowing deterministic tests
without running a server. `Reporter` and `Workspace` can also be injected.

Workspace tools remain domain independent. Project-specific technologies belong
in project descriptions, templates, skills, or deliverables.

## Verification

```bash
python -m unittest discover -s tests -v
python -m compileall -q mad go_mad.py
```

Tests cover every example's Markdown parsing, template and skill loading, graph
validation and rollback, Supervisor authority and acceptance, dependency gating,
peer routing, tool policy and evidence, model retries and HTTP requests, injected
presentation, disconnected writing contributions, inline research, correction
feedback, final inspection, and complete fake-model project runs. The deterministic
suite needs no running model server or browser. A live writing-only smoke run against the local model server produced and accepted
a substantive note, then hit the smoke test's 180-second timeout during final
project review. It remained unapproved; this does not establish live end-to-end
reliability. Cancellation during final review is covered by a regression test.

The solar example has its own optional `threejs-solar/verify.py` for its visual and
interaction criteria. Those checks are local to that example; the runtime has no
knowledge of its implementation, animation, or expected scene.
