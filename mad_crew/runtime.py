import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import sys
import traceback

from mad.agents import Agent, Bus, Intelligence, ResponseTooLong
from mad import ui
from .contract import Contract, payload, workspace_path


class Runtime:
    def __init__(self, task, workspace, server, model, workers=2, timeout=240):
        if type(workers) is not int or workers < 1:
            raise ValueError('workers must be a positive limit.')
        self.task = task
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.server, self.model = server, model
        self.limit, self.timeout = workers, timeout
        self.bus = Bus()
        self.bus.register('Supervisor')
        self.supervisor = Agent('Supervisor', Intelligence(server, model), self.bus)
        self.workers, self.busy, self.packages = {}, {}, {}
        self.run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        self.log = self.workspace / '.mad/audit' / (self.run_id + '.jsonl')
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.contracts = self.workspace / '.mad/contracts' / self.run_id
        self.contracts.mkdir(parents=True, exist_ok=True)
        folder = Path(__file__).parent
        self.worker_instructions = (folder / 'worker.md').read_text()
        self.history = [{'role': 'system', 'content': (folder / 'supervisor.md').read_text() + '\nProject:\n' + task}]

    def record(self, event, **data):
        entry = dict(time=datetime.now(timezone.utc).isoformat(), run_id=self.run_id, event=event, **data)
        with self.log.open('a', encoding='utf-8') as output:
            output.write(json.dumps(entry, ensure_ascii=False) + '\n')
        readable = ui.log_entry(entry)
        if readable:
            with self.log.with_suffix('.md').open('a', encoding='utf-8') as output:
                output.write(readable)

    def path(self, name):
        return workspace_path(self.workspace, name)

    def new_contract(self, value):
        contract = Contract.from_dict(value)
        contract.inputs['data'].pop('dependencies', None)
        for section in (contract.inputs, contract.outputs):
            section['files'] = [self.path(name).relative_to(self.workspace).as_posix()
                                for name in section['files']]
            if len(set(section['files'])) != len(section['files']):
                raise ValueError('Duplicate normalized file references.')
        return contract

    def ancestors(self, packages):
        result = {}
        remaining = set(packages)
        while remaining:
            ready = [name for name in remaining if set(packages[name]['depends']) <= result.keys()]
            if not ready:
                raise ValueError('Plan contains a dependency cycle or unknown package.')
            for name in ready:
                parents = set(packages[name]['depends'])
                result[name] = parents | set().union(*(result[parent] for parent in parents))
                remaining.remove(name)
        return result

    def validate_plan(self, packages):
        ancestors = self.ancestors(packages)
        for name, package in packages.items():
            supplied = set().union(*(set(packages[parent]['contract'].outputs['files']) for parent in ancestors[name]))
            for source in package['contract'].inputs['files']:
                if not self.path(source).exists() and source not in supplied:
                    raise ValueError(f'{name}: input {source} does not exist and is not supplied by a prerequisite.')
            for other, candidate in packages.items():
                overlap = set(package['contract'].outputs['files']) & set(candidate['contract'].outputs['files'])
                if name != other and overlap and other not in ancestors[name] and name not in ancestors[other]:
                    raise ValueError(f'{name} and {other} write the same files without a dependency: {sorted(overlap)}')

    def install_plan(self, definitions, workers):
        if self.packages:
            raise ValueError('The plan is already installed; revise rejected contracts instead.')
        if type(workers) is not int or not 1 <= workers <= self.limit:
            raise ValueError(f'Choose between 1 and {self.limit} workers.')
        if not isinstance(definitions, list) or not definitions:
            raise ValueError('Plan must contain at least one package.')
        packages = {}
        for definition in definitions:
            if not isinstance(definition, dict) or set(definition) != {'id', 'depends', 'contract'}:
                raise ValueError('Each package contains id, depends, and contract.')
            name, dependencies = definition['id'], definition['depends']
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', name) or name in packages:
                raise ValueError('Package IDs must be unique letters, digits, underscores, or hyphens.')
            if not isinstance(dependencies, list) or any(not isinstance(d, str) for d in dependencies):
                raise ValueError('depends must list package IDs.')
            if len(set(dependencies)) != len(dependencies):
                raise ValueError('Dependency references must be unique.')
            packages[name] = dict(depends=dependencies, contract=self.new_contract(definition['contract']),
                                  revision=1, state='pending', worker=None, result=None, issued=None)
        self.validate_plan(packages)
        self.packages = packages
        for number in range(workers):
            name = f'Worker-{number + 1}'
            self.bus.register(name)
            self.workers[name] = Agent(name, Intelligence(self.server, self.model), self.bus)
        for name in packages:
            self.save_contract(name)
        self.save_plan()
        self.record('plan_created', workers=workers, plan=self.plan())

    def plan(self):
        return {name: {**package, 'contract': package['contract'].to_dict()} for name, package in self.packages.items()}

    def status(self):
        return {name: dict(depends=p['depends'], state=p['state'], revision=p['revision'], worker=p['worker'],
                           output=p['result']['output'] if p['result'] else None)
                for name, p in self.packages.items()}

    def save_plan(self):
        path = self.workspace / '.mad' / f'plan-{self.run_id}.json'
        path.write_text(json.dumps(self.plan(), ensure_ascii=False, indent=2), encoding='utf-8')

    def save_contract(self, name, contract=None):
        package = self.packages[name]
        path = self.contracts / f'{name}-r{package["revision"]}.md'
        path.write_text((contract or package['contract']).markdown(), encoding='utf-8')
        return path.relative_to(self.workspace).as_posix()

    def resolved_contract(self, name):
        package = self.packages[name]
        source = package['contract']
        inputs = {'files': list(source.inputs['files']), 'data': dict(source.inputs['data'])}
        if package['depends']:
            inputs['data']['dependencies'] = {parent: self.packages[parent]['result']['output']
                                               for parent in package['depends']}
            for result in inputs['data']['dependencies'].values():
                inputs['files'].extend(path for path in result['files'] if path not in inputs['files'])
        for path in inputs['files']:
            if not self.path(path).exists():
                raise ValueError(f'Input no longer exists: {path}')
        return Contract(source.setting, inputs, source.task, source.outputs, source.acceptance)

    async def dispatch(self):
        for name, package in self.packages.items():
            if package['state'] != 'pending' or any(self.packages[p]['state'] != 'accepted' for p in package['depends']):
                continue
            worker = next((worker for worker in self.workers if worker not in self.busy), None)
            if worker is None:
                break
            contract = self.resolved_contract(name)
            path = self.save_contract(name, contract)
            contract = Contract.from_markdown((self.workspace / path).read_text(encoding='utf-8'))
            package.update(state='running', worker=worker, issued=contract.to_dict())
            self.busy[worker] = (name, package['revision'])
            packet = dict(type='contract', package=name, revision=package['revision'], path=path, contract=contract.to_dict())
            self.record('contract_sent', agent='Supervisor', recipient=worker, **packet)
            ui.message('Supervisor', worker, f'{name}, revision {package["revision"]}: {path}')
            await self.bus.send('Supervisor', worker, json.dumps(packet))
        self.save_plan()

    async def ask(self, agent, history):
        for attempt in range(3):
            self.record('model_request', agent=agent.name, model=agent.intelligence.model, messages=history)
            ui.activity(agent.name)
            try:
                text = await agent.intelligence.reply(history, json_mode=True)
            except ResponseTooLong as error:
                self.record('response_truncated', agent=agent.name, error=str(error), details=getattr(error, '__notes__', []))
                if attempt == 2:
                    raise
                history.append({'role': 'user', 'content': 'Response was truncated and not executed. Return one shorter complete action.'})
                continue
            self.record('model_response', agent=agent.name, text=text)
            history.append({'role': 'assistant', 'content': text})
            history[1:] = history[1:][-12:]
            result = json.loads(text)
            if not isinstance(result, dict):
                raise ValueError('Return one JSON object.')
            return result

    def inspect(self, name, offset=0):
        if type(offset) is not int or offset < 0:
            raise ValueError('offset must be a nonnegative integer.')
        path = self.path(name)
        result = {'path': name, 'bytes': path.stat().st_size}
        try:
            text = path.read_text(encoding='utf-8')
            result.update(text=text[offset:offset + 12000], offset=offset,
                          characters=len(text), truncated=offset + 12000 < len(text))
        except UnicodeError:
            result['binary'] = True
        return result

    async def python(self, agent, name, revision, code, run=1):
        if not isinstance(code, str) or not code.strip():
            raise ValueError('python must contain source code.')
        self.record('python_started', agent=agent.name, package=name, revision=revision, run=run, code=code)
        process = await asyncio.create_subprocess_exec(sys.executable, '-c', code, cwd=self.workspace,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
        communication = asyncio.create_task(process.communicate())
        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(asyncio.shield(communication), 30)
        except (TimeoutError, asyncio.CancelledError) as error:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            stdout, stderr = await communication
            if isinstance(error, asyncio.CancelledError):
                self.record('python_cancelled', agent=agent.name, package=name, revision=revision)
                raise
            timed_out = True
        result = dict(run=run, code=code, returncode=process.returncode, stdout=stdout.decode(errors='replace'),
                      stderr=stderr.decode(errors='replace'), timed_out=timed_out)
        self.record('python_finished', agent=agent.name, package=name, revision=revision, **result)
        ui.tool_result(agent.name, result['returncode'], result['stdout'], result['stderr'])
        return result

    def output(self, value, contract):
        result = payload(value)
        result['files'] = [self.path(path).relative_to(self.workspace).as_posix() for path in result['files']]
        if (len(set(result['files'])) != len(result['files'])
                or set(result['files']) != set(contract.outputs['files'])
                or set(result['data']) != set(contract.outputs['data'])):
            raise ValueError('Return the exact OUTPUT file paths and inline data fields requested by the contract.')
        for path in result['files']:
            if not self.path(path).is_file():
                raise ValueError(f'Output file does not exist: {path}')
        return result

    def checks(self, value, contract, executions):
        if not isinstance(value, list) or len(value) != len(contract.acceptance):
            raise ValueError('Report one check for every ACCEPTANCE condition, using criterion numbers starting at 1.')
        result = []
        seen = set()
        for check in value:
            required = {'criterion', 'method', 'result', 'details'}
            if not isinstance(check, dict) or not required <= set(check) <= required | {'runs'}:
                raise ValueError('A check contains criterion, method, result, details, and optional runs.')
            number = check['criterion']
            if type(number) is not int or not 1 <= number <= len(contract.acceptance) or number in seen:
                raise ValueError('Check criterion numbers must cover every ACCEPTANCE condition exactly once.')
            if check['method'] not in ('execution', 'inspection', 'none') or check['result'] not in ('pass', 'fail', 'unverified'):
                raise ValueError('Check method is execution, inspection, or none; result is pass, fail, or unverified.')
            if not isinstance(check['details'], str) or not check['details'].strip():
                raise ValueError('Describe the observed result or verification limitation.')
            runs = check.get('runs', [])
            if not isinstance(runs, list) or any(type(n) is not int or not 1 <= n <= len(executions) for n in runs):
                raise ValueError('Check runs must reference actual Python run numbers from this contract revision.')
            if check['method'] == 'execution' and not runs:
                raise ValueError('Execution checks must reference recorded Python runs.')
            if check['method'] == 'none' and (check['result'] != 'unverified' or runs):
                raise ValueError('A check with no method must be unverified and have no runs.')
            if check['method'] == 'execution' and check['result'] == 'pass':
                if any(executions[n - 1]['returncode'] != 0 or executions[n - 1]['timed_out'] for n in runs):
                    raise ValueError('A passing execution check cannot cite failed or timed-out runs.')
            result.append({**check, 'runs': list(runs)})
            seen.add(number)
        return sorted(result, key=lambda check: check['criterion'])

    async def perform(self, agent, packet, executions):
        contract = Contract.from_dict(packet['contract'])
        history = [{'role': 'system', 'content': self.worker_instructions + '\nCurrent contract:\n'
                    + json.dumps(packet, ensure_ascii=False)},
                   {'role': 'user', 'content': 'Execute the current contract.'}]
        for _ in range(40):
            try:
                action = await self.ask(agent, history)
                if set(action) == {'python'}:
                    execution = await self.python(agent, packet['package'], packet['revision'], action['python'], len(executions) + 1)
                    executions.append(execution)
                    feedback = {**execution, 'stdout': execution['stdout'][:12000], 'stderr': execution['stderr'][:12000]}
                    feedback['truncated'] = len(execution['stdout']) > 12000 or len(execution['stderr']) > 12000
                elif set(action) == {'question'}:
                    question = payload(action['question'])
                    for path in question['files']:
                        if not self.path(path).is_file():
                            raise ValueError(f'Question file does not exist: {path}')
                    event = dict(type='question', package=packet['package'], revision=packet['revision'], question=question)
                    self.record('worker_question', agent=agent.name, **event)
                    await self.bus.send(agent.name, 'Supervisor', json.dumps(event))
                    self.record('agent_waiting', agent=agent.name)
                    reply = await self.bus.receive(agent.name)
                    feedback = json.loads(reply.text)
                    if reply.sender != 'Supervisor' or feedback.get('type') != 'answer':
                        raise ValueError('Expected an answer from Supervisor.')
                elif {'output'} <= set(action) <= {'output', 'checks'}:
                    output = self.output(action['output'], contract)
                    checks = self.checks(action.get('checks'), contract, executions)
                    return dict(type='output', package=packet['package'], revision=packet['revision'],
                                output=output, checks=checks, executions=executions, error=None)
                else:
                    raise ValueError('Workers may execute python, ask a question, or return output.')
            except (ValueError, TypeError, KeyError, OSError) as error:
                self.record('action_failed', agent=agent.name, error_type=type(error).__name__, error=str(error))
                feedback = {'error': str(error)}
            history.append({'role': 'user', 'content': json.dumps(feedback, ensure_ascii=False)})
        raise RuntimeError(f'{agent.name} exceeded 40 actions for {packet["package"]}.')

    async def worker_loop(self, agent):
        try:
            while True:
                self.record('agent_waiting', agent=agent.name)
                message = await self.bus.receive(agent.name)
                packet = json.loads(message.text)
                if message.sender != 'Supervisor' or packet.get('type') != 'contract':
                    raise ValueError('Workers receive contracts only from Supervisor.')
                executions = []
                try:
                    result = await self.perform(agent, packet, executions)
                except Exception as error:
                    self.record('worker_failed', agent=agent.name, package=packet['package'], error=str(error),
                                traceback=traceback.format_exc())
                    result = dict(type='output', package=packet['package'], revision=packet['revision'],
                                  output=None, checks=[], executions=executions, error=str(error))
                self.record('worker_output', agent=agent.name, **result)
                await self.bus.send(agent.name, 'Supervisor', json.dumps(result))
        finally:
            self.record('agent_stopped', agent=agent.name)

    def review(self, value, expected):
        if not isinstance(value, dict) or value.get('package') != expected:
            raise ValueError('Review the package presented in this event.')
        package = self.packages[expected]
        if package['state'] != 'review' or value.get('revision') != package['revision']:
            raise ValueError('Review the current delivered revision only.')
        if not isinstance(value.get('reason'), str) or not value['reason'].strip():
            raise ValueError('Review requires a reason.')
        if value.get('decision') == 'accept' and set(value) == {'package', 'revision', 'decision', 'reason', 'checks'}:
            if package['result']['error']:
                raise ValueError('This delivery failed; reject with a revised contract or finish unaccepted.')
            self.output(package['result']['output'], package['contract'])
            worker_checks = self.checks(package['result']['checks'], package['contract'], package['result']['executions'])
            reviewed_checks = self.checks(value['checks'], package['contract'], package['result']['executions'])
            if any(check['result'] != 'pass' for check in worker_checks + reviewed_checks):
                raise ValueError('Failed or unverified conditions require correction or verification through a revised contract.')
            package['state'] = 'accepted'
        elif (value.get('decision') == 'reject'
              and {'package', 'revision', 'decision', 'reason', 'contract'} <= set(value)
              <= {'package', 'revision', 'decision', 'reason', 'contract', 'checks'}):
            if 'checks' in value:
                self.checks(value['checks'], package['contract'], package['result']['executions'])
            revised = self.new_contract(value['contract'])
            original = package['contract'].outputs
            if not set(original['files']) <= set(revised.outputs['files']) or not set(original['data']) <= set(revised.outputs['data']):
                raise ValueError('A revision may add OUTPUT paths or fields but must retain existing references for dependencies.')
            candidate = {**self.packages, expected: {**package, 'contract': revised}}
            self.validate_plan(candidate)
            package.update(contract=revised, revision=package['revision'] + 1, state='pending',
                           worker=None, result=None, issued=None)
            self.save_contract(expected)
        else:
            raise ValueError('Accept requires checks for every ACCEPTANCE condition; reject requires a revised contract.')
        self.record('supervisor_review', agent='Supervisor', **value)
        self.save_plan()

    async def supervisor_turn(self, event, stage):
        self.history.append({'role': 'user', 'content': json.dumps(event, ensure_ascii=False)})
        for _ in range(20):
            try:
                action = await self.ask(self.supervisor, self.history)
                if {'read'} <= set(action) <= {'read', 'offset'}:
                    feedback = self.inspect(action['read'], action.get('offset', 0))
                    self.record('file_read', agent='Supervisor', **feedback)
                elif stage == 'review' and set(action) == {'execution'}:
                    number = action['execution']
                    runs = self.packages[event['package']]['result']['executions']
                    if type(number) is not int or not 1 <= number <= len(runs):
                        raise ValueError('execution must name a recorded run number for this delivery.')
                    run = runs[number - 1]
                    feedback = {**run, **{key: run[key][:12000] for key in ('code', 'stdout', 'stderr')}}
                    feedback['truncated'] = any(len(run[key]) > 12000 for key in ('code', 'stdout', 'stderr'))
                    self.record('execution_read', agent='Supervisor', package=event['package'], **feedback)
                elif stage == 'plan' and set(action) == {'plan', 'workers'}:
                    self.install_plan(action['plan'], action['workers'])
                    return None
                elif stage == 'review' and set(action) == {'review'}:
                    self.review(action['review'], event['package'])
                    return None
                elif stage == 'question' and set(action) == {'answer'}:
                    answer = payload(action['answer'])
                    for path in answer['files']:
                        if not self.path(path).is_file():
                            raise ValueError(f'Answer file does not exist: {path}')
                    return answer
                elif set(action) == {'finish'}:
                    result = action['finish']
                    if not isinstance(result, dict) or set(result) != {'accepted', 'reason'} or type(result['accepted']) is not bool:
                        raise ValueError('finish contains accepted (boolean) and reason.')
                    if not isinstance(result['reason'], str) or not result['reason'].strip():
                        raise ValueError('Finish requires a reason.')
                    if result['accepted'] and (not self.packages or any(p['state'] != 'accepted' for p in self.packages.values())):
                        raise ValueError('Cannot finish accepted until every package has been accepted.')
                    return result
                else:
                    raise ValueError(f'Action is not permitted during {stage}. Supervisor can read and make decisions, not execute workers\' Python.')
            except (ValueError, TypeError, KeyError, OSError) as error:
                self.record('action_failed', agent='Supervisor', error_type=type(error).__name__, error=str(error))
                feedback = {'error': str(error), 'stage': stage}
            self.history.append({'role': 'user', 'content': json.dumps(feedback, ensure_ascii=False)})
        raise RuntimeError('Supervisor exceeded 20 actions without a valid decision.')

    async def run(self):
        self.record('run_started', task=self.task, workspace=str(self.workspace), server=self.server,
                    models={'Supervisor': self.model}, worker_limit=self.limit)
        ui.start(self.task, self.workspace, [self.supervisor], self.log.with_suffix('.md'))
        try:
            async with asyncio.timeout(self.timeout):
                decision = await self.supervisor_turn({'event': 'project', 'worker_limit': self.limit}, 'plan')
                if decision is None:
                    async with asyncio.TaskGroup() as group:
                        actors = [group.create_task(self.worker_loop(agent)) for agent in self.workers.values()]
                        while decision is None:
                            await self.dispatch()
                            if all(p['state'] == 'accepted' for p in self.packages.values()):
                                decision = await self.supervisor_turn({'event': 'project_review', 'plan': self.status()}, 'finish')
                                continue
                            self.record('agent_waiting', agent='Supervisor')
                            message = await self.bus.receive('Supervisor')
                            event = json.loads(message.text)
                            name = event.get('package')
                            if self.busy.get(message.sender) != (name, event.get('revision')):
                                raise ValueError('Unexpected or stale worker response.')
                            package = self.packages[name]
                            if event['type'] == 'question':
                                answer = await self.supervisor_turn({**event, 'event': 'question', 'worker': message.sender,
                                    'contract': package['issued'], 'plan': self.status()}, 'question')
                                if answer is not None and set(answer) == {'accepted', 'reason'}:
                                    decision = answer
                                else:
                                    await self.bus.send('Supervisor', message.sender, json.dumps(dict(type='answer', answer=answer)))
                            elif event['type'] == 'output':
                                del self.busy[message.sender]
                                package.update(state='review', result=event)
                                self.save_plan()
                                # Keep idle workers useful while Supervisor reviews an independent delivery.
                                await self.dispatch()
                                evidence = [{**run, 'code': run['code'][:2000], 'stdout': run['stdout'][:1000],
                                             'stderr': run['stderr'][:1000], 'preview': True} for run in event['executions'][-6:]]
                                artifacts = []
                                remaining = 12000
                                for path in event['output']['files'] if event['output'] else []:
                                    artifact = self.inspect(path)
                                    if 'text' in artifact:
                                        size = min(2000, remaining)
                                        artifact['text'] = artifact['text'][:size]
                                        artifact['truncated'] = artifact['characters'] > size
                                        remaining -= len(artifact['text'])
                                    artifacts.append(artifact)
                                context = dict(event='delivery', package=name, revision=event['revision'],
                                    contract=package['issued'], output=event['output'], error=event['error'],
                                    executions=evidence, execution_count=len(event['executions']), worker_checks=event['checks'],
                                    artifacts=artifacts, plan=self.status())
                                decision = await self.supervisor_turn(context, 'review')
                            else:
                                raise ValueError('Unknown worker response.')
                        for actor in actors:
                            actor.cancel()
        except BaseException as error:
            event = 'run_cancelled' if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt)) else 'run_failed'
            self.record(event, error_type=type(error).__name__, error=str(error), traceback=traceback.format_exc())
            raise
        self.record('run_finished', summary=decision['reason'], accepted=decision['accepted'])
        ui.finish(('Accepted: ' if decision['accepted'] else 'Not accepted: ') + decision['reason'])
        return decision['accepted']
