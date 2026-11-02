from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys


class Runtime:
    def __init__(self, task, workspace, server, model, workers=2):
        if type(workers) is not int or workers < 1:
            raise ValueError('workers must be a positive integer.')
        self.task = task
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.server, self.model, self.workers = server, model, workers
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        self.audit = self.workspace / '.mad/audit' / run_id
        self.audit.mkdir(parents=True)

    def record(self, event, **data):
        entry = dict(time=datetime.now(timezone.utc).isoformat(), event=event, **data)
        with (self.audit / 'events.jsonl').open('a', encoding='utf-8') as output:
            output.write(json.dumps(entry, ensure_ascii=False) + '\n')
        with (self.audit / 'events.md').open('a', encoding='utf-8') as output:
            output.write(f'## {event} — {entry["time"]}\n\n')
            for key, value in data.items():
                value = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                fence = '```'
                while fence in value:
                    fence += '`'
                output.write(f'### {key}\n\n{fence}\n{value}\n{fence}\n\n')

    def execute(self, code):
        """Run worker code in the workspace; this is not a filesystem sandbox."""
        self.record('execution_started', code=code)
        process = subprocess.Popen([sys.executable, '-u', '-c', code], cwd=self.workspace,
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
        result = dict(returncode=process.returncode, timed_out=timed_out, stdout=stdout, stderr=stderr)
        self.record('execution_finished', **result)
        return json.dumps({**result, 'stdout': stdout[:12000], 'stderr': stderr[:12000],
                           'truncated': len(stdout) > 12000 or len(stderr) > 12000})

    def crew(self):
        # Keep CrewAI optional for the existing MAD implementation.
        os.environ.setdefault('OTEL_SDK_DISABLED', 'true')
        os.environ.setdefault('CREWAI_TELEMETRY_DISABLED', 'true')
        from crewai import Agent, Crew, LLM, Process, Task
        from crewai.tools import tool

        @tool('workspace_python')
        def workspace_python(code: str) -> str:
            """Execute Python in the workspace to inspect, create, edit, or verify files. Returns actual stdout, stderr, and exit status. Has local environment permissions, not a filesystem sandbox."""
            return self.execute(code)

        workspace_python.cache_function = lambda arguments, result: False
        folder = Path(__file__).parent
        llm = LLM(model=self.model, custom_openai=True, base_url=self.server,
                  api_key=os.environ.get('MAD_API_KEY', 'local'), temperature=0.2,
                  max_tokens=4096, timeout=120)
        supervisor = Agent(role='Supervisor', goal='Coordinate the crew and review the delivered project.',
                           backstory=(folder / 'supervisor.md').read_text(), llm=llm,
                           allow_delegation=True, max_iter=30, max_retry_limit=2, verbose=True)
        workers = [Agent(role=f'Worker-{number + 1}', goal='Complete the work delegated by Supervisor.',
                         backstory=(folder / 'worker.md').read_text(), llm=llm,
                         tools=[workspace_python], allow_delegation=False,
                         max_iter=30, max_retry_limit=2, verbose=True)
                   for number in range(self.workers)]
        task = Task(description=self.task,
                    expected_output='A final review stating accepted or not accepted, delivered file paths, '
                                    'checks actually performed, their results, and any limitations. '
                                    'The actual deliverables must be saved in the workspace.')
        return Crew(agents=workers, tasks=[task], manager_agent=supervisor,
                    process=Process.hierarchical, memory=False, cache=False,
                    verbose=True, tracing=False, share_crew=False,
                    output_log_file=str(self.audit / 'crew.json'))

    def run(self):
        self.record('run_started', task=self.task, workspace=str(self.workspace),
                    server=self.server, model=self.model, workers=self.workers)
        try:
            result = self.crew().kickoff()
            self.record('run_finished', result=result.raw)
            return result.raw
        except BaseException as error:
            self.record('run_failed', error_type=type(error).__name__, error=str(error))
            raise
