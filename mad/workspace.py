"""Generic workspace operations and passive audit files."""
import functools
import http.server
import json
from pathlib import Path
import re
import shlex
import socketserver
import subprocess
import threading
import time
from urllib.parse import quote


class Workspace:
    IGNORED_NAMES = {'.git', '.mad', 'node_modules', '__pycache__', '.venv', 'venv',
                     '.pytest_cache', '.mypy_cache'}

    def __init__(self, path):
        self.root = Path(path).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.audit = self.root / '.mad' / 'audit'
        self.audit.mkdir(parents=True, exist_ok=True)
        self.servers, self.evidence = {}, {}
        self.run_id, self.sequence = str(time.time_ns()), 0

    def path(self, value='.'):
        value = value.strip()
        while value.startswith('./'):
            value = value[2:]
        if Path(value).is_absolute():
            raise ValueError('Use workspace-relative paths, such as "." or "main.js".')
        if value == self.root.name or value.startswith(self.root.name + '/'):
            raise ValueError('Do not include the workspace name. Use "." or a workspace-relative filename.')
        path = (self.root / value).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError('Path escapes the workspace.')
        internal = self.root / '.mad'
        if path == internal or internal in path.parents:
            raise ValueError('Agents cannot access .mad audit files as deliverables.')
        return path

    def list_files(self, path='.'):
        target = self.path(path)
        if not target.exists():
            raise FileNotFoundError(path)
        if target.is_file():
            return str(target.relative_to(self.root))
        return '\n'.join(str(item.relative_to(self.root)) + ('/' if item.is_dir() else '')
                         for item in sorted(target.iterdir()) if item.name not in self.IGNORED_NAMES) or '(directory is empty)'

    def read(self, path):
        return self.path(path).read_text(encoding='utf-8')

    def write(self, path, content, append=False):
        target = self.path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('a' if append else 'w', encoding='utf-8') as output:
            output.write(content)
        return f'Wrote {len(content)} characters to {target.relative_to(self.root)}'

    def run(self, command):
        arguments = shlex.split(command)
        if not arguments:
            raise ValueError('Empty command.')
        result = subprocess.run(arguments, cwd=self.root, capture_output=True, text=True, timeout=120)
        text = f'Command: {command}\nReturn code: {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}'
        return {'success': result.returncode == 0, 'result': text[:20000]}

    def serve(self, command, name='default'):
        if name in self.servers and self.servers[name].poll() is None:
            return f'Server {name} is already running.'
        arguments = shlex.split(command)
        if not arguments:
            raise ValueError('Empty command.')
        log = self.audit / ('server-' + re.sub(r'[^A-Za-z0-9_.-]', '_', name) + '.log')
        with log.open('a', encoding='utf-8') as output:
            self.servers[name] = subprocess.Popen(arguments, cwd=self.root, stdout=output,
                                                  stderr=subprocess.STDOUT, start_new_session=True)
        return f'Started {name}, PID {self.servers[name].pid}.'

    def stop_server(self, name='default'):
        process = self.servers.pop(name, None)
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        return f'Stopped {name}.'

    def stop_servers(self):
        for name in list(self.servers):
            self.stop_server(name)

    def browser_check(self, path='index.html', wait_ms=1500):
        from playwright.sync_api import sync_playwright
        target = self.path(path)
        if not target.is_file():
            raise FileNotFoundError(path)
        class Handler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

        handler = functools.partial(Handler, directory=str(self.root))
        server = socketserver.TCPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        errors, warnings = [], []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('requestfailed', lambda request: errors.append(f'{request.url}: {request.failure}'))
                    page.on('console', lambda message: (errors if message.type == 'error' else warnings).append(message.text)
                            if message.type in {'error', 'warning'} else None)
                    url = f'http://127.0.0.1:{server.server_address[1]}/' + quote(target.relative_to(self.root).as_posix())
                    response = page.goto(url, wait_until='load', timeout=15000)
                    page.wait_for_timeout(wait_ms)
                    status = response.status if response else None
                    if status is None or status >= 400:
                        errors.append(f'HTTP status: {status}')
                    return {'success': not errors, 'result': json.dumps({
                        'url': url, 'status': status, 'title': page.title(), 'errors': errors,
                        'warnings': warnings, 'scope': 'Page loading and runtime errors only; behavior is not verified.',
                    })}
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def execute(self, call, reads, review=False):
        tool = call['tool']
        path = call.get('path', '')
        try:
            if review and tool in {'write', 'append'}:
                raise ValueError('Assign corrective work instead of writing during review.')
            target = self.path(path)
            if tool == 'write' and target.exists() and target not in reads:
                raise ValueError('Read the existing file before overwriting it.')
            if tool == 'list':
                result = self.list_files(path or '.')
            elif tool == 'read':
                result = self.read(path)
                reads.add(target)
            elif tool in {'write', 'append'}:
                result = self.write(path, call.get('content', ''), append=tool == 'append')
                reads.add(target)
            elif tool == 'run':
                return self.run(call.get('command', ''))
            elif tool == 'serve':
                result = self.serve(call.get('command', ''), call.get('name') or 'default')
            elif tool == 'stop_server':
                result = self.stop_server(call.get('name') or 'default')
            elif tool == 'browser_check':
                return self.browser_check(path or 'index.html', call.get('wait_ms', 1500))
            else:
                raise ValueError(f'Unknown tool: {tool}')
            return {'success': True, 'result': result}
        except Exception as error:
            return {'success': False, 'result': str(error)}

    def require_artifacts(self, paths):
        for path in paths:
            if not self.path(path).is_file():
                raise ValueError(f'Required artifact is missing: {path}. Create the file with a workspace tool, or report blocked.')

    def record(self, actor, action, result, task_id=None):
        self.sequence += 1
        record = dict(id=f'E{self.sequence}', run_id=self.run_id, actor=actor,
                      task_id=task_id, request=action, **result)
        with (self.audit / 'events.jsonl').open('a', encoding='utf-8') as output:
            output.write(json.dumps(record, ensure_ascii=False) + '\n')
        if action.get('action') in {'tool', 'result'}:
            self.evidence[record['id']] = record
        return record

    def task_report(self, task):
        name = re.sub(r'[^A-Za-z0-9_.-]', '_', task.id)
        path = self.audit / f'{name}.md'
        lines = [f'# {task.id}: {task.title}', '', f'Agent: {task.owner}',
                 f'Status: {task.status}', '', '## Assignment', '',
                 task.description or '(No description supplied.)', '', '### Acceptance criteria', '']
        lines.extend(f'- {item}' for item in task.acceptance)
        if not task.acceptance:
            lines.append('(None specified.)')
        lines.extend(['', '### Required deliverables', ''])
        lines.extend(f'- `{item}`' for item in task.artifacts)
        if not task.artifacts:
            lines.append('Inline output; no required files.')
        records = [record for record in self.evidence.values()
                   if record['task_id'] == task.id and record['request'].get('action') == 'tool']
        lines.extend(['', '## Work performed', ''])
        for record in records:
            call = record['request']
            target = call.get('command') or call.get('path') or call.get('name') or '.'
            status = 'OK' if record['success'] else 'FAILED'
            lines.append(f"- {record['id']} — {call['tool']} {target}: {status}")
        if not records:
            lines.append('No workspace operations recorded.')
        lines.extend(['', '## Verification and failures', ''])
        checks = [record for record in records if not record['success']
                  or record['request']['tool'] in {'run', 'browser_check'}]
        for record in checks:
            lines.extend([f"### {record['id']}: {record['request']['tool']}", ''])
            output = record.get('result', record.get('error', ''))
            if not isinstance(output, str):
                output = json.dumps(output, ensure_ascii=False, indent=2)
            lines.extend('    ' + line for line in output.splitlines())
            lines.append('')
        if not checks:
            lines.append('No command or browser checks, or failed tools, recorded. This does not establish correctness.')
        lines.extend(['', '## Submitted result', '', task.result.get('summary') or '(No summary.)',
                      '', task.result.get('output') or '(No substantive output.)', '',
                      '## Supervisor decision', '', f'Current task status: {task.status}', '',
                      task.feedback or 'Awaiting Supervisor review.', '',
                      'Full action history: [events.jsonl](events.jsonl). Evidence IDs refer to this run.', ''])
        path.write_text('\n'.join(lines), encoding='utf-8')
        return path
