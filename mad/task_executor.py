"""Worker execution loop; completion is a claim submitted for Supervisor review."""
import json
import time
from mad.agent import MAD_ROOT
from mad.message import Message
from mad.protocol import TASK_DECISION_SCHEMA, TOOL_STEP_SCHEMA, FINAL_STEP_SCHEMA
from mad.tool_session import ToolSession


class TaskExecutor:
    def __init__(self, agent, project, hub, workspace, model, router, reporter):
        self.agent = agent
        self.project = project
        self.hub = hub
        self.workspace = workspace
        self.model = model
        self.router = router
        self.reporter = reporter

    async def execute(self, message):
        task = message.metadata['task']
        start_time = time.perf_counter()
        self.reporter.print(f'[green]▶[/green] [bold]{self.agent.name}[/bold] [dim]{task.id} — {task.title}[/dim]')
        acceptance = '\n'.join((f'- {item}' for item in task.acceptance))
        agent_list = '\n'.join((f'- {agent.name}' for agent in self.project.agents))
        instructions = (MAD_ROOT / 'mad_agents' / 'TASK.md').read_text(encoding='utf-8').strip()
        prompt = f"""
Execute the assigned task.

ID:
{task.id}

Title:
{task.title}

Description:
{task.description}

Acceptance criteria:
{acceptance}

Supervisor feedback from previous review:
{message.metadata.get('review_feedback') or '(none)'}

Workspace:
{self.workspace.root}

WORKSPACE TOOLS

Use paths relative to the workspace.
The workspace already exists. An empty workspace is valid.
To inspect the workspace root, use {{"tool":"list","path":"."}}.
Do not include the workspace directory name in tool paths.

{instructions}

Available agents:

{agent_list}

Use exact agent names.
""".strip()
        conversation = [{'role': 'system', 'content': self.agent.build_system_prompt(self.project)}, {'role': 'user', 'content': prompt}]
        tools = ToolSession(self.workspace, self.reporter)
        unresolved_tool_failure = False
        path_retry_pending = False
        path_retry_used = False
        max_steps = 100
        final_result = None
        for step in range(1, max_steps + 1):
            if path_retry_pending:
                path_retry_pending = False
                path_retry_used = True
                conversation.append({
                    'role': 'user',
                    'content': (
                        'The workspace-name rejection is a recoverable path-format error. '
                        'The workspace already exists, and an empty workspace is valid. '
                        'Make one corrected tool attempt before reporting blocked. '
                        'To inspect the root, use {"tool":"list","path":"."}. '
                        'For other paths, omit the workspace directory name.'
                    ),
                })
                state = 'tool'
            else:
                decision, _ = await self.model.structured_chat(conversation + [{'role': 'user', 'content': "Choose the next action. Return state='tool' if workspace work remains. Return state='final' only if the task is completed or genuinely blocked."}], schema=TASK_DECISION_SCHEMA, max_tokens=256)
                state = decision['state']
            if state == 'final' and unresolved_tool_failure:
                conversation.append({'role': 'user', 'content': 'The latest workspace step contained a failed operation. Resolve or investigate that failure before claiming completion. If it cannot be resolved, report blocked.'})
            if state == 'tool':
                tool_result, tool_response = await self.model.structured_chat(conversation + [{'role': 'user', 'content': 'Request only the workspace operations needed next. Do not invent results.'}], schema=TOOL_STEP_SCHEMA, max_tokens=8192)
                await self.router.route(tool_result.get('messages', []), task_id=task.id)
                tool_calls = tool_result.get('tool_calls', [])
                if not tool_calls:
                    unresolved_tool_failure = True
                    conversation.append({'role': 'user', 'content': 'You selected a tool step but requested no operations.'})
                    continue
                self.reporter.print(f'[dim]  {self.agent.name}: workspace step {step}[/dim]')
                results = await tools.execute(tool_calls)
                step_failed = any(not result["success"] for result in results)
                unresolved_tool_failure = step_failed
                path_retry_pending = not path_retry_used and any(
                    result.get('error_code') == 'workspace_name_in_path'
                    for result in results
                )
                conversation.append({'role': 'assistant', 'content': tool_response})
                conversation.append({'role': 'user', 'content': 'WORKSPACE TOOL RESULTS:\n\n' + json.dumps(results, indent=2) + '\n\nTreat these results as ground truth and continue the task.'})
                continue
            final_data, _ = await self.model.structured_chat(conversation + [{'role': 'user', 'content': 'Return your final task result. Do not request tools. The Supervisor will independently review your completion claim.'}], schema=FINAL_STEP_SCHEMA, max_tokens=4096)
            if unresolved_tool_failure and final_data['status'] == 'completed':
                conversation.append({'role': 'user', 'content': 'A tool failure is unresolved. Investigate it or report blocked.'})
                continue
            await self.router.route(final_data.get('messages', []), task_id=task.id)
            final_result = {'status': final_data['status'], 'summary': final_data['summary'], 'output': final_data['output']}
            break
        if final_result is None:
            final_result = {'status': 'blocked', 'summary': f'Maximum workspace steps ({max_steps}) reached.', 'output': 'Execution did not converge within the workspace tool-step limit.'}
        elapsed = time.perf_counter() - start_time
        proposed_status = final_result['status']
        if proposed_status == 'completed':
            self.reporter.print(f'[bold yellow]◇[/bold yellow] [bold]{self.agent.name}[/bold] [dim]{task.id} submitted for review in {elapsed:.1f}s[/dim]')
        else:
            self.reporter.print(f'[bold red]✗[/bold red] [bold]{self.agent.name}[/bold] [dim]{task.id} blocked after {elapsed:.1f}s[/dim]')
        await self.hub.send(Message(sender=self.agent.name, recipient='__runtime__', type='TASK_RESULT', task_id=task.id, content=final_result['summary'], metadata={'status': proposed_status, 'elapsed': elapsed, 'output': final_result['output'], 'changed_files': sorted(tools.changed_files), 'browser_checks': tools.browser_checks, 'command_results': tools.command_results, 'tool_records': tools.records}))
