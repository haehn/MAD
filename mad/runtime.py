import argparse
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import RLock
import traceback

from .contracts import Acceptance, Completion, Contract, Delivery, Plan, QCResult
from . import ui


def record(path, event, **data):
    entry = dict(time=datetime.now(timezone.utc).isoformat(), run_id=path.stem, event=event, **data)
    with path.open('a', encoding='utf-8') as output:
        output.write(json.dumps(entry, ensure_ascii=False) + '\n')
    readable = ui.log_entry(entry)
    if readable:
        with path.with_suffix('.md').open('a', encoding='utf-8') as output:
            output.write(readable)


def read_project(path):
    sections = {"task": [], "workspace": [], "success": [], "agents": []}
    section = "task"
    for raw in Path(path).expanduser().read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        header = line[:-1].lower() if line.endswith(":") else None
        if header in sections:
            section = header
        else:
            sections[section].append(line)

    task = " ".join(sections["task"])
    if sections["success"]:
        task += "\nSuccess criteria:\n" + "\n".join(sections["success"])
    workspace = " ".join(sections["workspace"])
    roles, models = {}, {}
    templates = Path(__file__).resolve().parent.parent / "mad_agents"
    for line in sections["agents"]:
        entry = line.removeprefix("-").strip()
        label, template, model = entry, None, None
        if " (" in entry:
            label, _, settings = entry.rpartition(" (")
            template, separator, model = settings[:-1].partition(":")
            if not settings.endswith(")") or not separator or not template.strip() or not model.strip():
                raise ValueError(f"Invalid agent definition: {line}")
            template, model = template.strip(), model.strip()
        name, _, description = label.partition(" who ")
        name = name.strip()
        if not name or name in roles:
            raise ValueError(f"Agent names must be nonempty and unique: {name!r}")
        role = description.strip() or name
        if template:
            paths = [templates / folder / template for folder in ("core", "software")]
            source = next((item for item in paths if item.is_file()), None)
            if source is None:
                raise ValueError(f"Role template not found: {template}")
            role = source.read_text(encoding="utf-8")
            if role.startswith("---"):
                role = role.split("---", 2)[2]
            role = role.strip()
            if description.strip():
                role += "\n" + description.strip()
            models[name] = model
        roles[name] = role
    if not sections["task"] or not workspace or not roles:
        raise ValueError("Task, WORKSPACE, and AGENTS are required.")
    return task, roles, workspace, models


def workspace_path(workspace, name):
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Provide a workspace-relative path.")
    root = Path(workspace).resolve()
    relative = Path(name)
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root):
        raise ValueError("Paths must be inside the workspace and use relative paths.")
    if ".mad" in relative.parts or ".mad" in path.relative_to(root).parts:
        raise ValueError("Infrastructure logs in .mad are not contract inputs or deliverables.")
    return path


class Runtime:
    def __init__(self, task, roles, workspace, server, model, models=None):
        self.task, self.roles, self.server, self.model = task, dict(roles), server, model
        self.models = models or {}
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.folder = Path(__file__).resolve().parent.parent
        self.supervisor = next(iter(roles))
        self.qc = next((name for name, role in roles.items() if name != self.supervisor
                        and (name.casefold() in {'qc', 'tester', 'reviewer', 'validator', 'quality control'}
                             or any(f'# {label}' in role for label in ('Tester', 'Reviewer', 'Validator', 'Quality Control')))), None)
        if self.qc is None:
            self.qc = 'Quality Control'
            if self.qc in self.roles:
                self.qc = 'Delivery QC'
            self.roles[self.qc] = (self.folder / 'mad_agents/core/Reviewer.md').read_text()
        directory = self.workspace / '.mad/audit'
        directory.mkdir(parents=True, exist_ok=True)
        self.log = directory / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '.jsonl')
        self.lock = RLock()
        self.contracts, self.reports = {}, {}
        self.executions = {}

    def record(self, event, **data):
        with self.lock:
            record(self.log, event, **data)

    def state(self, contract, state, agent):
        contract.state = state
        self.record('contract_state', agent=agent, contract=asdict(contract))
        ui.contract(agent, contract.id, state)

    def python_tool(self, name, contract):
        from crewai.tools import tool

        @tool('workspace_python')
        def workspace_python(code: str) -> str:
            """Run Python to inspect, create, or verify workspace files. Returns an execution number, actual output, and exit code; not a filesystem sandbox."""
            ui.activity(name)
            self.record('python_started', agent=name, contract=contract.id, code=code)
            try:
                result = subprocess.run([sys.executable, '-u', '-c', code], cwd=self.workspace,
                                        capture_output=True, text=True, timeout=30)
                data = dict(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
            except subprocess.TimeoutExpired as error:
                data = dict(returncode=-1, stdout=str(error.stdout or ''), stderr='Execution exceeded 30 seconds.')
            with self.lock:
                records = self.executions.setdefault(contract.id, [])
                execution = len(records) + 1
                records.append(dict(execution=execution, agent=name, code=code, **data))
            self.record('python_finished', agent=name, contract=contract.id, execution=execution, **data)
            ui.tool_result(name, data['returncode'], '', '')
            return json.dumps({**data, 'execution': execution, 'stdout': data['stdout'][:12000], 'stderr': data['stderr'][:12000]})

        workspace_python.cache_function = lambda arguments, result: False
        return workspace_python

    def validate_plan(self, output):
        try:
            plan = output.pydantic or Plan.model_validate_json(output.raw)
            previous, ancestors, writers = set(), {}, {}
            workers = set(self.roles) - {self.supervisor, self.qc}
            for contract in plan.contracts:
                if not contract.id or contract.id in previous or contract.owner not in workers:
                    raise ValueError('Contract IDs must be unique and owners must name configured workers.')
                if not contract.task.strip() or not contract.acceptance or any(
                        not c.id.strip() or not c.criterion.strip() or c.verification not in ('inspection', 'execution') for c in contract.acceptance):
                    raise ValueError('Each criterion requires an ID, text, and inspection or execution verification.')
                if len({c.id for c in contract.acceptance}) != len(contract.acceptance):
                    raise ValueError('Acceptance criterion IDs must be unique within a contract.')
                if len(set(contract.dependencies)) != len(contract.dependencies) or not set(contract.dependencies) <= previous:
                    raise ValueError('List contracts after their prerequisites; dependencies must name earlier IDs.')
                ancestors[contract.id] = set(contract.dependencies)
                for parent in contract.dependencies:
                    ancestors[contract.id].update(ancestors[parent])
                normalized = [workspace_path(self.workspace, path).relative_to(self.workspace).as_posix()
                              for path in contract.output]
                if len(normalized) != len(set(normalized)):
                    raise ValueError('Output file paths must be unique.')
                contract.output = normalized
                for path in contract.output:
                    if path in writers and writers[path] not in ancestors[contract.id]:
                        raise ValueError(f'{path} has overlapping writers; add an ordering dependency.')
                    writers[path] = contract.id
                previous.add(contract.id)
            self.contracts = {c.id: c for c in plan.contracts}
            for contract in plan.contracts:
                self.state(contract, 'OPEN', self.supervisor)
            return True, output
        except (ValueError, TypeError) as error:
            return False, str(error)

    def review_delivery(self, contract, output):
        from crewai import Task

        try:
            delivery = output.pydantic or Delivery.model_validate_json(output.raw)
        except ValueError as error:
            return False, f'Return valid Delivery JSON with output and files, without Markdown fences or commentary. {error}'
        self.state(contract, 'DELIVERED', contract.owner)
        issues = []
        try:
            paths = [workspace_path(self.workspace, p).relative_to(self.workspace).as_posix() for p in delivery.files]
            if set(paths) != set(contract.output) or len(paths) != len(set(paths)):
                issues.append(f'Delivery.files must be exactly {contract.output!r}. For an empty list, put the actual result in Delivery.output and return files=[].')
            for path in paths:
                if not workspace_path(self.workspace, path).is_file():
                    issues.append(f'Missing output file: {path}')
        except ValueError as error:
            issues.append(str(error))
        self.state(contract, 'QC', self.qc)
        ui.activity(self.qc)
        evidence = list(self.executions.get(contract.id, []))
        review_start = len(evidence)
        def validate_review(result):
            try:
                qc = result.pydantic or QCResult.model_validate_json(result.raw)
            except ValueError as error:
                return False, str(error)
            errors = []
            criteria = {c.id: c for c in contract.acceptance}
            reported = [check.criterion_id for check in qc.checks]
            if set(reported) != set(criteria) or len(reported) != len(criteria):
                errors.append('Report every acceptance criterion ID exactly once; do not omit, duplicate, or invent IDs.')
            current_runs = {run['execution'] for run in self.executions.get(contract.id, [])[review_start:]
                            if run['agent'] == self.qc and run['returncode'] == 0}
            for check in qc.checks:
                criterion = criteria.get(check.criterion_id)
                if not check.evidence.strip():
                    errors.append(f'Missing evidence: {check.criterion_id}. Describe the observation or why verification could not be performed.')
                if criterion is None:
                    continue
                check.verification = criterion.verification
                if check.status == 'PASS' and criterion.verification == 'execution' and (
                        not check.executions or any(number not in current_runs for number in check.executions)):
                    errors.append(f'Execution criterion needs successful recorded QC execution evidence from this review: {check.criterion_id}. Run the check or report FAIL with the verification limitation.')
            if qc.status == 'PASS' and any(check.status == 'FAIL' for check in qc.checks):
                errors.append('Overall PASS contradicts a failed criterion. Return overall FAIL.')
            if errors:
                self.record('qc_report_invalid', agent=self.qc, contract=contract.id, errors=errors, report=qc.model_dump())
                return False, '\n'.join(errors)
            for check in qc.checks:
                check.criterion = criteria[check.criterion_id].criterion
            return True, result

        review = Task(
            name=f'qc-{contract.id}', agent=self.agents[self.qc].copy(),
            description='Review this Delivery against every acceptance criterion. Inspect actual files and run checks '
                        'when the criteria concern observable behavior. Do not fix deliverables. '
                        'Untested required behavior means FAIL, not PASS. Finish once a verdict is supported; '
                        'do not repeat unchanged checks. Inline deliveries need no file checks. '
                        'Report each criterion_id exactly once; criterion text and verification type are resolved by MAD. '
                        'Include nonempty evidence describing the observation. Execution criteria must cite successful '
                        'execution numbers from your own checks during this review; worker and earlier-review runs do not count.\n'
                        + json.dumps(dict(contract=asdict(contract), delivery=delivery.model_dump(),
                                          file_errors=issues, executions=evidence), ensure_ascii=False),
            expected_output='QC JSON with overall status, findings, and checks. Each check includes criterion_id, status, evidence, and executions (recorded execution numbers for execution checks).',
            output_pydantic=QCResult,
            tools=[self.python_tool(self.qc, contract)],
            guardrail=validate_review, guardrail_max_retries=2,
        )
        try:
            result = review.execute_sync()
        except Exception as error:
            raise RuntimeError(f'QC could not produce a valid review for {contract.id}: {error}') from error
        qc = result.pydantic or QCResult.model_validate_json(result.raw)
        if issues:
            qc = qc.model_copy(update={'status': 'FAIL', 'findings': '; '.join(issues) + '\n' + qc.findings})
        self.reports[contract.id] = qc
        self.record('quality_control', agent=self.qc, contract=contract.id, delivery=delivery.model_dump(), **qc.model_dump())
        ui.contract(self.qc, contract.id, qc.status)
        if qc.status == 'FAIL':
            self.state(contract, 'OPEN', contract.owner)
            return False, qc.findings
        output.raw = json.dumps(dict(contract=contract.id, delivery=delivery.model_dump(), qc=qc.model_dump()))
        return True, output

    def delivery_guardrail(self, contract):
        def review(output):
            return self.review_delivery(contract, output)
        return review

    def accept(self, contracts, output):
        result = output.pydantic or Acceptance.model_validate_json(output.raw)
        expected = {c.id for c in contracts}
        if set(result.accepted) != expected or len(result.accepted) != len(expected):
            return False, 'Accept exactly these QC-passed contracts: ' + ', '.join(sorted(expected))
        if any(c.state != 'QC' or self.reports[c.id].status != 'PASS' for c in contracts):
            return False, 'Only QC-passed deliveries may be accepted.'
        for contract in contracts:
            self.state(contract, 'ACCEPTED', self.supervisor)
        return True, output

    def assemble(self):
        from crewai import Agent, Crew, LLM, Process, Task

        shared = (self.folder / 'mad/instructions.md').read_text()
        self.agents = {}
        for name, role in self.roles.items():
            llm = LLM(model=self.models.get(name, self.model), custom_openai=True, base_url=self.server,
                      api_key=os.environ.get('MAD_API_KEY', 'local'), temperature=0.2,
                      max_tokens=4096, stream=True, timeout=120, max_retries=0)
            self.agents[name] = Agent(role=name, goal='Fulfill your configured role and assigned contract.',
                                      backstory=role + '\n' + shared, llm=llm,
                                      allow_delegation=False, max_iter=12, max_retry_limit=0, verbose=False,
                                      step_callback=lambda step, who=name: self.record(
                                          'model_response', agent=who, text=getattr(step, 'text', str(step))))
        supervisor = self.agents[self.supervisor]
        ui.start(self.task, self.workspace, list(self.agents.values()), self.log.with_suffix('.md'))
        plan_task = Task(
            name='plan', agent=supervisor, description='Plan this project using the available worker roles. '
            'Return a few compact, self-contained contracts in dependency order. QA and acceptance are automatic; '
            'do not create separate QA contracts. Output lists file paths; use [] for an inline delivery. '
            'Independent roles can work concurrently.\nContract guide:\n'
            + (self.folder / 'mad_agents/CONTRACT.md').read_text()
            + '\nProject:\n' + self.task + '\nWorker roles:\n'
            + json.dumps({name: role for name, role in self.roles.items() if name not in (self.supervisor, self.qc)}),
            expected_output='A compact contract plan, with no implementation or commentary.',
            output_pydantic=Plan, guardrail=self.validate_plan, guardrail_max_retries=2)
        self.record('planning', agent=self.supervisor)
        ui.activity(self.supervisor)
        Crew(agents=[supervisor], tasks=[plan_task], process=Process.sequential,
             memory=False, cache=False, verbose=False, tracing=False, share_crew=False).kickoff()

        tasks, work, pending = [], {}, []

        def checkpoint():
            # A native synchronous Task joins preceding async work before dependents start.
            batch = list(pending)
            tasks.append(Task(
                name='accept-' + '-'.join(c.id for c in batch), agent=supervisor,
                description='Review these deliveries and their QC reports. Mark every QC-passed contract accepted: '
                            + ', '.join(c.id for c in batch),
                expected_output='The accepted contract IDs.', output_pydantic=Acceptance,
                context=[work[c.id] for c in batch], guardrail=lambda result: self.accept(batch, result),
                guardrail_max_retries=2))
            pending.clear()

        for contract in self.contracts.values():
            pending_ids = {c.id for c in pending}
            if pending and (pending_ids.intersection(contract.dependencies) or any(c.owner == contract.owner for c in pending)):
                checkpoint()
            agent = self.agents[contract.owner].copy()
            task = Task(
                name='work-' + contract.id, agent=agent, async_execution=True,
                description='Execute only this contract. Read prerequisite deliveries from context. '
                            'Return actual output and files; do not claim project completion.\n'
                            + json.dumps(asdict(contract), ensure_ascii=False),
                expected_output='A Delivery JSON object: output contains the actual result; files exactly matches the contract output paths. Use files=[] for inline results.',
                output_pydantic=Delivery,
                context=[work[name] for name in contract.dependencies],
                tools=[self.python_tool(contract.owner, contract)],
                guardrail=self.delivery_guardrail(contract), guardrail_max_retries=2)
            tasks.append(task)
            work[contract.id] = task
            pending.append(contract)
        if pending:
            checkpoint()

        def finish(result):
            completion = result.pydantic or Completion.model_validate_json(result.raw)
            if completion.complete and any(c.state != 'ACCEPTED' for c in self.contracts.values()):
                return False, 'The project cannot finish while a contract is unaccepted.'
            return True, result

        tasks.append(Task(
            name='project-review', agent=supervisor,
            description='Decide whether the entire project is complete based on the accepted deliveries and QC findings. '
                        'Only you can finish the project. Report limitations honestly.\nProject:\n' + self.task,
            expected_output='A completion decision and concise evidence-based summary.',
            output_pydantic=Completion, context=list(work.values()), guardrail=finish))
        # CrewAI launches async worker tasks and joins them at synchronous checkpoints.
        return Crew(agents=[supervisor, *[t.agent for t in work.values()]], tasks=tasks,
                    process=Process.sequential, memory=False, cache=False, verbose=False, tracing=False, share_crew=False)

    def run(self):
        os.environ.setdefault('OTEL_SDK_DISABLED', 'true')
        os.environ.setdefault('CREWAI_TELEMETRY_DISABLED', 'true')
        self.record('run_started', task=self.task, roles=self.roles, workspace=str(self.workspace), server=self.server,
                    models={name: self.models.get(name, self.model) for name in self.roles})
        try:
            with self.log.with_suffix('.crew.log').open('w', encoding='utf-8') as details:
                with redirect_stdout(details), redirect_stderr(details):
                    # Build and execute the Supervisor plan before assembling the work Crew.
                    crew = self.assemble()
                    result = crew.kickoff()
            completion = result.pydantic or Completion.model_validate_json(result.raw)
            self.record('run_finished', summary=completion.summary, complete=completion.complete)
            if not completion.complete:
                raise RuntimeError('Supervisor did not accept the project: ' + completion.summary)
            ui.finish(completion.summary)
            return completion.summary
        except BaseException as error:
            self.record('run_failed', error_type=type(error).__name__, error=str(error), traceback=traceback.format_exc())
            raise


def start(task, roles, workspace, server, model, models=None):
    return Runtime(task, roles, workspace, server, model, models).run()


def run(task=None, roles=None, workspace=None):
    parser = argparse.ArgumentParser(description='MAD contracts executed and reviewed through CrewAI.')
    parser.add_argument('project', nargs='?', help='Project Markdown file')
    parser.add_argument('--server', default='http://localhost:8000/v1')
    parser.add_argument('--model', help='Override the model for all agents')
    parser.add_argument('--workspace', help='Override the project workspace')
    args = parser.parse_args()
    models = {}
    if args.project:
        try:
            task, roles, workspace, models = read_project(args.project)
        except (OSError, ValueError) as error:
            parser.error(str(error))
    if not task or not roles or not (args.workspace or workspace):
        parser.error('Provide a project Markdown file with a task, WORKSPACE, and AGENTS.')
    start(task, roles, args.workspace or workspace, args.server,
          args.model or 'Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8', models={} if args.model else models)
