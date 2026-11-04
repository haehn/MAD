from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, is_dataclass
import hashlib
import json
from pathlib import Path

from .agent import Agent
from .contract import Contract
from .workspace import Workspace
from . import ui


class Runtime:
    """Assemble configured agents; dispatch contracts through work, QA, and review."""

    def __init__(self, root, workspace, agents, *, max_revisions=3):
        self.root = root
        self.workspace = Workspace(workspace)
        self.definitions = agents
        self.max_revisions = max_revisions
        self.agents = {}
        self.contracts = {}
        self.reason = ''

    def assemble(self):
        self.agents = {item['name']: Agent(**item) for item in self.definitions}
        if len(self.agents) != len(self.definitions):
            raise ValueError('Agent names must be unique.')
        self.supervisor = self.agents[self.root.owner]
        self.normalize(self.root)
        for path in self.root.inputs['files']:
            if not self.workspace.file(path).is_file():
                raise ValueError(f'Root INPUT file is missing: {path}')
        self.workspace.save(self.root)
        return self.agents

    def normalize(self, contract):
        for section in (contract.inputs, contract.outputs):
            section['files'] = [self.workspace.file(p).relative_to(self.workspace.path).as_posix()
                                for p in section['files']]
            if len(section['files']) != len(set(section['files'])):
                raise ValueError('Duplicate normalized file references.')
        contract.inputs['data'].pop('dependencies', None)
        if contract.owner not in self.agents or contract.qa not in self.agents or contract.owner == contract.qa:
            raise ValueError('Each contract needs a configured owner and a separate QA agent.')
        return contract

    def validate_plan(self, contracts):
        ancestors = {}
        remaining = set(contracts)
        while remaining:
            ready = [name for name in remaining if set(contracts[name].depends_on) <= ancestors.keys()]
            if not ready:
                raise ValueError('Dependencies contain a cycle or an unknown contract.')
            for name in ready:
                parents = set(contracts[name].depends_on)
                ancestors[name] = parents | set().union(*(ancestors[p] for p in parents))
                remaining.remove(name)
        for name, contract in contracts.items():
            if name == self.root.id or contract.owner == self.root.owner or contract.qa == self.root.owner:
                raise ValueError('The root ID is reserved; Supervisor coordinates rather than doing delegated work or QA.')
            provided = set().union(*(set(contracts[p].outputs['files']) for p in ancestors[name]))
            for path in contract.inputs['files']:
                if not self.workspace.file(path).is_file() and path not in provided:
                    raise ValueError(f'{name}: input {path} needs an existing file or a prerequisite producer.')
            for other, candidate in contracts.items():
                unordered = name != other and other not in ancestors[name] and name not in ancestors[other]
                overlap = set(contract.outputs['files']) & set(candidate.outputs['files'])
                if unordered and overlap:
                    raise ValueError(f'{name} and {other} write overlapping outputs without an ordering dependency.')
                if unordered and set(contract.inputs['files']) & set(candidate.outputs['files']):
                    raise ValueError(f'{name} reads files that {other} writes; declare an ordering dependency.')
        provided = set().union(*(set(c.outputs['files']) for c in contracts.values()))
        for path in self.root.outputs['files']:
            if not self.workspace.file(path).is_file() and path not in provided:
                raise ValueError(f'The plan must provide root output {path}.')

    def parse_plan(self, value):
        if not isinstance(value, dict) or set(value) != {'contracts', 'reason'} or not isinstance(value['contracts'], list) or not value['contracts']:
            raise ValueError('Planning requires contracts (a nonempty list) and reason.')
        contracts = [self.normalize(Contract.parse(item)) for item in value['contracts']]
        result = {c.id: c for c in contracts}
        if len(result) != len(contracts):
            raise ValueError('Contract IDs must be unique.')
        self.validate_plan(result)
        return result

    def brief(self, contract):
        value = json.loads(json.dumps(contract.definition()))
        dependencies = {name: self.contracts[name].delivery for name in contract.depends_on}
        for output in dependencies.values():
            for path in output['files']:
                if path not in value['INPUT']['files']:
                    value['INPUT']['files'].append(path)
        value['INPUT']['data']['dependencies'] = dependencies
        return value

    def progress(self, agent, contract, phase, step):
        details = asdict(step) if is_dataclass(step) else str(step)
        self.workspace.record('agent_step', agent=agent.name, role=agent.role,
                              contract=contract.id, revision=contract.revision, phase=phase, details=details)

    def ask(self, agent, contract, phase, event, expected, validate):
        shared = (Path(__file__).parent / ('supervisor.md' if phase == 'supervisor' else 'worker.md')).read_text()
        brief = shared + '\nAssignment:\n' + json.dumps(event, ensure_ascii=False)
        tools = self.workspace.tools(agent, contract, phase)
        for attempt in range(3):
            self.workspace.record('model_request', agent=agent.name, role=agent.role,
                                  contract=contract.id, revision=contract.revision, phase=phase, brief=brief)
            raw = agent.run(brief, expected, tools,
                            lambda step: self.progress(agent, contract, phase, step))
            self.workspace.record('model_response', agent=agent.name, contract=contract.id, phase=phase, response=raw)
            try:
                text = raw.strip()
                if text.startswith('```'):
                    text = text.split('\n', 1)[1].rsplit('```', 1)[0]
                return validate(json.loads(text))
            except (ValueError, TypeError, KeyError) as error:
                self.workspace.record('invalid_response', agent=agent.name, contract=contract.id,
                                      phase=phase, attempt=attempt + 1, error=str(error))
                brief += '\nPrevious response:\n' + raw + '\nCorrection required: ' + str(error)
        raise ValueError(f'{agent.name} could not return a valid {phase} response after 3 attempts.')

    def status(self, contract, phase, state, agent=None):
        agent = agent or self.agents[contract.owner if phase == 'work' else contract.qa]
        ui.status(agent, contract, phase, state)
        self.workspace.record('status', agent=agent.name, role=agent.role,
                              contract=contract.id, revision=contract.revision, phase=phase, state=state)

    def fingerprints(self, contract):
        return {name: hashlib.sha256(self.workspace.file(name).read_bytes()).hexdigest()
                if self.workspace.file(name).is_file() else None for name in contract.outputs['files']}

    def perform(self, contract, phase):
        agent = self.agents[contract.owner if phase == 'work' else contract.qa]
        event = dict(phase=phase, contract=self.brief(contract), revision=contract.revision)
        if phase == 'work':
            return self.ask(agent, contract, phase, event,
                            'Return only JSON: {"output":{"files":["declared paths"],"data":{}}}. '
                            'The output must exactly match declared file paths and inline field names.',
                            lambda value: self.workspace.delivery(value['output'], contract))
        event.update(delivery=contract.delivery, worker_error=contract.error,
                     recorded_evidence=self.workspace.evidence_for(contract))
        if contract is self.root:
            event['accepted_deliveries'] = {name: c.delivery for name, c in self.contracts.items()}
        before = self.fingerprints(contract)
        report = self.ask(agent, contract, phase, event,
                          'Return only JSON: {"checks":[{"criterion":1,"status":"pass|fail|unverified",'
                          '"method":"execution|inspection|none","evidence":["E1"],"details":"Actual observations"}],'
                          '"summary":"Results and limitations"}. Check every acceptance condition.',
                          lambda value: self.workspace.validate_qa(value, contract))
        if before != self.fingerprints(contract):
            raise ValueError('QA changed declared deliverables; QA must report problems rather than fix them.')
        return report

    def decide(self, value, contract):
        if not isinstance(value, dict) or not isinstance(value.get('reason'), str) or not value['reason'].strip():
            raise ValueError('A decision needs a reason.')
        if contract.state != 'review':
            raise ValueError('Supervisor can only decide a delivered contract after QA has returned.')
        decision = value.get('decision')
        if decision == 'accept' and set(value) == {'decision', 'reason'}:
            if contract.error or not contract.report or any(c['status'] != 'pass' for c in contract.report['checks']):
                raise ValueError('Acceptance requires a delivery and separate QA passing every condition.')
            self.workspace.delivery(contract.delivery, contract)
            self.workspace.validate_qa(contract.report, contract)
            contract.state = 'accepted'
            if contract is self.root:
                self.reason = value['reason']
        elif decision == 'revise' and contract.revision >= self.max_revisions:
            raise ValueError('Revision limit reached; stop honestly rather than repeat the same work.')
        elif decision == 'revise' and contract is self.root and set(value) == {'decision', 'reason', 'contracts'}:
            added = [self.normalize(Contract.parse(item)) for item in value['contracts']]
            if not added or len({c.id for c in added}) != len(added) or any(c.id in self.contracts for c in added):
                raise ValueError('Root corrections must add new uniquely named corrective contracts.')
            candidate = {**self.contracts, **{c.id: c for c in added}}
            self.validate_plan(candidate)
            self.contracts = candidate
            self.root.depends_on = list(candidate)
            for child in added:
                self.workspace.save(child)
            contract.revision += 1
            contract.state, contract.delivery, contract.report, contract.error = 'pending', None, None, None
        elif decision == 'revise' and contract is not self.root and set(value) == {'decision', 'reason', 'contract'}:
            revised = self.normalize(Contract.parse(value['contract']))
            if revised.id != contract.id or not set(contract.outputs['files']) <= set(revised.outputs['files']) or not set(contract.outputs['data']) <= set(revised.outputs['data']):
                raise ValueError('Revision must retain the ID and existing OUTPUT references; it may add outputs.')
            revised.revision = contract.revision + 1
            candidate = {**self.contracts, contract.id: revised}
            self.validate_plan(candidate)
            if any(self.contracts[d].state != 'accepted' for d in revised.depends_on):
                raise ValueError('A correction may only depend on already accepted prerequisites.')
            self.contracts[contract.id] = revised
            contract.state = 'revised'
            self.workspace.save(revised)
        elif decision == 'stop' and set(value) == {'decision', 'reason'}:
            contract.state = 'blocked'
            self.reason = value['reason']
        else:
            raise ValueError('Accept/stop need decision and reason. Revise needs a revised contract, or new contracts for root corrections.')
        self.workspace.record('supervisor_decision', contract=contract.id, revision=contract.revision, decision=value)
        self.workspace.save(contract)
        self.status(contract, 'review', contract.state, self.supervisor)
        return decision

    def review(self, contract):
        self.status(contract, 'review', 'running', self.supervisor)
        event = dict(event='review', contract=contract.definition(), revision=contract.revision,
                     delivery=contract.delivery, qa=contract.report, error=contract.error,
                     evidence=self.workspace.evidence_for(contract),
                     plan={name: dict(state=c.state, contract=c.definition()) for name, c in self.contracts.items()})
        return self.ask(self.supervisor, contract, 'supervisor', event,
                        'Return JSON: {"decision":"accept|stop","reason":"Evidence-based decision"}, '
                        'or {"decision":"revise","reason":"Specific correction","contract":{complete revised contract}}. '
                        'For the root contract use contracts:[new corrective contracts] instead of contract. '
                        f'This is revision {contract.revision}; the limit is {self.max_revisions}.',
                        lambda value: self.decide(value, contract))

    def root_delivery(self):
        result = dict(files=self.root.outputs['files'], data={})
        if self.root.outputs['data']:
            event = dict(event='root_delivery', contract=self.root.definition(),
                         accepted_deliveries={name: c.delivery for name, c in self.contracts.items()})
            result = self.ask(self.supervisor, self.root, 'supervisor', event,
                              'Return JSON {"output":{"files":[declared files],"data":{declared inline fields:actual values}}}.',
                              lambda value: self.workspace.delivery(value['output'], self.root))
        return self.workspace.delivery(result, self.root)

    def schedule(self):
        futures, busy = {}, set()
        with ThreadPoolExecutor(max_workers=len(self.agents) - 1) as pool:
            while self.root.state not in {'accepted', 'blocked'}:
                if not futures and all(c.state == 'accepted' for c in self.contracts.values()) and self.root.state == 'pending':
                    try:
                        self.root.delivery = self.root_delivery()
                    except ValueError as error:
                        self.root.error = str(error)
                    self.root.state = 'qa_pending'
                    self.workspace.save(self.root)
                for contract in [*self.contracts.values(), self.root]:
                    phase = 'qa' if contract.state == 'qa_pending' else 'work'
                    ready = contract.state == 'qa_pending' or (contract is not self.root and contract.state == 'pending'
                            and all(self.contracts[d].state == 'accepted' for d in contract.depends_on))
                    if not ready:
                        continue
                    name = contract.qa if phase == 'qa' else contract.owner
                    if name in busy:
                        continue
                    contract.inputs = self.brief(contract)['INPUT']
                    contract.state = 'qa_running' if phase == 'qa' else 'running'
                    self.workspace.save(contract)
                    self.status(contract, phase, 'running')
                    busy.add(name)
                    futures[pool.submit(self.perform, contract, phase)] = (contract, phase, name)
                if not futures:
                    raise RuntimeError('No runnable contracts remain; the project was not accepted.')
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    contract, phase, name = futures.pop(future)
                    busy.remove(name)
                    try:
                        result = future.result()
                        if phase == 'work':
                            contract.delivery = result
                        else:
                            contract.report = result
                    except Exception as error:
                        contract.error = str(error)
                        self.workspace.record('assignment_failed', contract=contract.id, phase=phase, error=str(error))
                        self.status(contract, phase, 'failed')
                    contract.state = 'qa_pending' if phase == 'work' else 'review'
                    self.workspace.save(contract)
                    self.status(contract, phase, 'delivered' if phase == 'work' else 'review')
                    if phase == 'qa' and self.review(contract) == 'stop':
                        self.root.state = 'blocked'
                        self.workspace.save(self.root)
                        for pending in futures:
                            pending.cancel()
                        break
        return self.root.state == 'accepted'

    def run(self):
        self.workspace.record('run_started', root=self.root.definition(), agents=self.definitions)
        try:
            with (self.workspace.audit / 'crew-output.log').open('w', encoding='utf-8') as details:
                with redirect_stdout(details), redirect_stderr(details):
                    self.assemble()
                ui.start(self.root, self.workspace.path, list(self.agents.values()), self.workspace.audit / 'events.md')
                self.status(self.root, 'planning', 'running', self.supervisor)
                with redirect_stdout(details), redirect_stderr(details):
                    event = dict(event='plan', root=self.root.definition(),
                                 team=[dict(name=a.name, role=a.role, instructions=a.instructions,
                                            server=a.server, model=a.intelligence.model) for a in self.agents.values()])
                    self.contracts = self.ask(self.supervisor, self.root, 'supervisor', event,
                                              'Return JSON {"contracts":[complete contracts],"reason":"Plan rationale"}.', self.parse_plan)
                    self.root.depends_on = list(self.contracts)
                    self.workspace.save(self.root)
                    for contract in self.contracts.values():
                        self.workspace.save(contract)
                    accepted = self.schedule()
            reason = self.reason or ('Root QA passed and Supervisor accepted the project.' if accepted else 'Project not accepted.')
            self.workspace.record('run_finished', accepted=accepted, reason=reason)
            ui.finish(accepted, reason)
            return accepted
        except BaseException as error:
            self.workspace.record('run_failed', error_type=type(error).__name__, error=str(error))
            raise
