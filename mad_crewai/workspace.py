from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from threading import RLock

from mad.ui import code_block


class Workspace:
    """Generic execution, artifact checks, and local audit storage."""

    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self.path.mkdir(parents=True, exist_ok=True)
        self.run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        self.audit = self.path / '.mad/audit' / self.run_id
        self.contracts = self.path / '.mad/contracts' / self.run_id
        self.audit.mkdir(parents=True)
        self.contracts.mkdir(parents=True)
        self.evidence = {}
        self.lock = RLock()

    def record(self, event, **data):
        entry = dict(time=datetime.now(timezone.utc).isoformat(), event=event, **data)
        with self.lock:
            with (self.audit / 'events.jsonl').open('a', encoding='utf-8') as output:
                output.write(json.dumps(entry, ensure_ascii=False) + '\n')
            with (self.audit / 'events.md').open('a', encoding='utf-8') as output:
                output.write(f'## {event} — {entry["time"]}\n\n')
                for key, value in data.items():
                    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
                    output.write(f'### {key}\n\n{code_block(text)}\n\n')

    def file(self, name):
        if not isinstance(name, str) or not name.strip() or Path(name).is_absolute():
            raise ValueError('Use paths relative to the workspace.')
        path = (self.path / name).resolve()
        if not path.is_relative_to(self.path) or '.mad' in path.relative_to(self.path).parts:
            raise ValueError('Paths must stay inside the workspace; .mad is reserved for infrastructure.')
        return path

    def save(self, contract):
        path = self.contracts / f'{contract.id}-r{contract.revision}.md'
        path.write_text(contract.markdown(), encoding='utf-8')
        self.record('contract_saved', contract=contract.id, revision=contract.revision,
                    state=contract.state, path=str(path), definition=contract.definition(),
                    delivery=contract.delivery, qa=contract.report, error=contract.error)

    def delivery(self, value, contract):
        from .contract import payload
        result = payload(value)
        if set(result['files']) != set(contract.outputs['files']) or set(result['data']) != set(contract.outputs['data']):
            raise ValueError('Delivery must match the declared OUTPUT file paths and data field names.')
        for name in result['files']:
            if not self.file(name).is_file():
                raise ValueError(f'Declared output file is missing: {name}')
        return result

    def observe(self, kind, agent, contract, phase, **data):
        with self.lock:
            entry = dict(id=f'E{len(self.evidence) + 1}', kind=kind, agent=agent.name,
                         role=agent.role, contract=contract.id, revision=contract.revision,
                         phase=phase, **data)
            self.evidence[entry['id']] = entry
            self.record('evidence', **entry)
        return entry

    def execute(self, code, agent, contract, phase):
        self.record('execution_started', agent=agent.name, role=agent.role,
                    contract=contract.id, revision=contract.revision, phase=phase, code=code)
        process = subprocess.Popen([sys.executable, '-u', '-c', code], cwd=self.path,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True)
        timed_out = False
        try:
            try:
                stdout, stderr = process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
        entry = self.observe('execution', agent, contract, phase, code=code,
                             returncode=process.returncode, timed_out=timed_out, stdout=stdout, stderr=stderr)
        return self.preview(entry)

    @staticmethod
    def preview(entry):
        result = dict(entry)
        result['truncated'] = False
        for key in ('code', 'stdout', 'stderr', 'content'):
            if key in result and len(result[key]) > 12000:
                result[key] = result[key][:12000]
                result['truncated'] = True
        return result

    def tools(self, agent, contract, phase):
        from crewai.tools import tool

        @tool('read_file')
        def read_file(path: str, offset: int = 0) -> str:
            """Read a relative workspace file with recorded evidence. Use offsets for long files."""
            if type(offset) is not int or offset < 0:
                raise ValueError('offset must be a nonnegative character position.')
            content = self.file(path).read_text(encoding='utf-8')
            entry = self.observe('inspection', agent, contract, phase, path=path,
                                 offset=offset, content=content[offset:offset + 12000],
                                 total_characters=len(content), truncated=offset + 12000 < len(content))
            return json.dumps(entry)

        @tool('workspace_python')
        def workspace_python(code: str) -> str:
            """Run Python in the workspace; returns evidence ID, stdout, stderr, and exit status. Not a filesystem sandbox."""
            return json.dumps(self.execute(code, agent, contract, phase))

        for item in (read_file, workspace_python):
            item.cache_function = lambda arguments, result: False
        return [read_file] if phase == 'supervisor' else [read_file, workspace_python]

    def evidence_for(self, contract):
        with self.lock:
            return [self.preview(e) for e in self.evidence.values()
                    if e['contract'] == contract.id and e['revision'] == contract.revision]

    def validate_qa(self, value, contract):
        if not isinstance(value, dict) or set(value) != {'checks', 'summary'} or not isinstance(value['summary'], str):
            raise ValueError('QA returns checks and a summary.')
        checks = value['checks']
        if not isinstance(checks, list) or len(checks) != len(contract.acceptance):
            raise ValueError('QA must check every ACCEPTANCE condition exactly once.')
        seen = set()
        for check in checks:
            if not isinstance(check, dict) or set(check) != {'criterion', 'status', 'method', 'evidence', 'details'}:
                raise ValueError('Each QA check needs criterion, status, method, evidence, and details.')
            number = check['criterion']
            if type(number) is not int or not 1 <= number <= len(checks) or number in seen:
                raise ValueError('criterion numbers must uniquely cover ACCEPTANCE, starting at 1.')
            seen.add(number)
            if check['status'] not in ('pass', 'fail', 'unverified') or check['method'] not in ('execution', 'inspection', 'none'):
                raise ValueError('Unknown QA status or method.')
            if not isinstance(check['details'], str) or not check['details'].strip() or not isinstance(check['evidence'], list):
                raise ValueError('QA needs observed details and a list of evidence IDs.')
            references = []
            for name in check['evidence']:
                item = self.evidence.get(name) if isinstance(name, str) else None
                if item is None or item['contract'] != contract.id or item['revision'] != contract.revision:
                    raise ValueError('QA evidence must reference actual runs or reads for this contract revision.')
                if item['phase'] != 'qa' or item['agent'] != contract.qa:
                    raise ValueError('Passing and failing QA observations must cite the assigned QA agent, not worker claims.')
                references.append(item)
            if check['status'] == 'pass':
                if not references or check['method'] == 'none':
                    raise ValueError('A passing check requires actual evidence.')
                if check['method'] == 'execution':
                    runs = [r for r in references if r['kind'] == 'execution']
                    if not runs or any(r['returncode'] != 0 or r['timed_out'] for r in runs):
                        raise ValueError('Passing execution checks require successful recorded executions.')
            if check['method'] == 'none' and check['status'] != 'unverified':
                raise ValueError('A check without a method must be unverified.')
        return value
