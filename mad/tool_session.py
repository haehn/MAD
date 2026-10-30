"""Per-task workspace policy and evidence, independent of model interaction."""
import asyncio
from pathlib import Path


class ToolSession:
    def __init__(self, workspace, reporter, prefix="tool", allowed_tools=None):
        self.workspace = workspace
        self.reporter = reporter
        self.read_paths = set()
        self.changed_files = set()
        self.browser_checks = []
        self.command_results = []
        self.records = []
        self.prefix = prefix
        self.allowed_tools = allowed_tools

    async def execute(self, calls):
        results = []
        for call in calls:
            original_request = dict(call)
            tool = call['tool']
            path = call.get('path', '').strip()
            while path.startswith('./'):
                path = path[2:]
            call = {**call, 'path': path}
            error_code = None
            try:
                if self.allowed_tools is not None and tool not in self.allowed_tools:
                    raise ValueError("Review tools cannot write or append deliverables; assign corrective work instead.")
                if Path(path).is_absolute():
                    raise ValueError('Workspace paths must be relative.')
                if path == self.workspace.root.name or path.startswith(self.workspace.root.name + '/'):
                    error_code = 'workspace_name_in_path'
                    raise ValueError('Do not include the workspace directory name in tool paths. '
                                     'Use paths relative to the workspace, such as "." or "main.js".')
                resolved = self.workspace._resolve_agent_path(path)
                canonical = str(resolved.relative_to(self.workspace.root))
                if tool == 'write' and resolved.exists() and canonical not in self.read_paths:
                    raise ValueError(f'{path} exists. READ it before overwriting it.')
                outcome = await asyncio.to_thread(self.workspace.execute_tool_result, **call)
                text = outcome.output
                success = outcome.success
                if tool in {'read', 'write', 'append'}:
                    self.read_paths.add(canonical)
                if tool in {'write', 'append'}:
                    self.changed_files.add(canonical)
                if tool == 'browser_check':
                    self.browser_checks.append(text)
                if tool == 'run':
                    self.command_results.append(text)
                target = path or call.get('command') or call.get('name', '')
                self.reporter.print(f'[dim]    {tool} {target}[/dim]')
                results.append({'tool': tool, 'success': success, 'result': text})
            except Exception as error:
                self.reporter.print(f'[yellow]    {tool} failed: {error}[/yellow]')
                result = {'tool': tool, 'success': False, 'result': str(error)}
                if error_code:
                    result['error_code'] = error_code
                results.append(result)
            evidence_id = f'{self.prefix}_{len(self.records) + 1}'
            results[-1]['evidence_id'] = evidence_id
            self.records.append({
                'id': evidence_id, 'request': original_request,
                'normalized_request': dict(call), **results[-1],
            })
        return results
