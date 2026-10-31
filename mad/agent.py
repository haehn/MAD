"""An agent loads a Markdown role and skills, then uses the shared action loop."""
import asyncio
import json
from pathlib import Path
import re
import requests
import yaml


MAD_ROOT = Path(__file__).resolve().parent.parent


TEXT = {'type': 'string'}
TEXTS = {'type': 'array', 'items': TEXT}
TASK_FIELDS = dict(id=TEXT, title=TEXT, description=TEXT, owner=TEXT,
                   dependencies=TEXTS, acceptance=TEXTS, artifacts=TEXTS)
TASK_SCHEMA = {'type': 'object', 'properties': {**TASK_FIELDS, 'status': {
    'type': 'string', 'enum': ['pending', 'cancelled'],
}}, 'required': list(TASK_FIELDS), 'additionalProperties': False}
TOOLS = {
    'list': ('path',), 'read': ('path',), 'write': ('path', 'content'),
    'append': ('path', 'content'), 'run': ('command',),
    'serve': ('command', 'name'), 'stop_server': ('name',),
    'browser_check': ('path', 'wait_ms'),
}
FIELDS = {
    'tool': {'tool': {'type': 'string', 'enum': list(TOOLS)}, 'path': TEXT, 'content': TEXT, 'command': TEXT, 'name': TEXT,
        'wait_ms': {'type': 'integer', 'minimum': 0, 'maximum': 30000}},
    'message': {'recipient': TEXT, 'content': TEXT},
    'plan': {'tasks': {'type': 'array', 'items': TASK_SCHEMA}},
    'result': {'status': {'type': 'string', 'enum': ['completed', 'blocked']},
               'summary': TEXT, 'output': TEXT},
    'review': {'verdict': {'type': 'string', 'enum': ['accept', 'rework', 'blocked']},
               'reason': TEXT, 'evidence': TEXTS},
    'replan': {'tasks': {'type': 'array', 'items': TASK_SCHEMA}, 'reason': TEXT},
    'finish': {'accepted': {'type': 'boolean'}, 'reason': TEXT, 'evidence': TEXTS},
    'done': {},
}


def response_schema(actions, review=False):
    variants = []
    for action in actions:
        if action == 'tool':
            for tool, names in TOOLS.items():
                if review and tool in {'write', 'append'}:
                    continue
                fields = {'tool': {'type': 'string', 'enum': [tool]},
                          **{name: ({'type': 'string', 'minLength': 1, 'pattern': r'\S'}
                                    if name in {'path', 'command'} else FIELDS['tool'][name]) for name in names}}
                variants.append({'type': 'object',
                                 'properties': {'action': {'type': 'string', 'enum': ['tool']}, **fields},
                                 'required': ['action', 'tool', *[name for name in names if name not in {'name', 'wait_ms'}]],
                                 'additionalProperties': False})
        else:
            variants.append({'type': 'object',
                             'properties': {'action': {'type': 'string', 'enum': [action]}, **FIELDS[action]},
                             'required': ['action', *FIELDS[action]], 'additionalProperties': False})
    return {'anyOf': variants}


def parse_response(text, actions):
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip(), flags=re.IGNORECASE)
    result = json.loads(text)
    if not isinstance(result, dict) or result.get('action') not in actions:
        raise ValueError('Return one allowed action object.')
    fields = FIELDS[result['action']]
    if result['action'] == 'tool':
        tool = result.get('tool')
        if not isinstance(tool, str) or tool not in TOOLS:
            raise ValueError('Choose a known workspace tool.')
        fields = {'tool': fields['tool'], **{name: fields[name] for name in TOOLS[tool]}}
        required = {'tool', *[name for name in TOOLS[tool] if name not in {'name', 'wait_ms'}]}
        for name in ('path', 'command'):
            if name in fields and (not isinstance(result.get(name), str) or not result[name].strip()):
                raise ValueError(f'{tool} requires a nonempty {name}.')
    else:
        required = set(fields)
    if set(result) - {'action', *fields} or not required.issubset(result):
        raise ValueError('Do not mix action fields; provide all required fields for one action.')
    return result


def tool_preview(record, limit=4000):
    """Keep tool metadata and bounded excerpts without changing the audit record."""
    preview = dict(record)
    request = dict(record.get('request', {}))
    if 'content' in request:
        request.pop('content')
        preview['content_note'] = 'Submitted file content omitted; inspect the file when needed.'
    command = request.get('command')
    if isinstance(command, str) and len(command) > limit:
        request['command'] = command[:limit] + ' [command excerpt]'
    if request:
        preview['request'] = request
    result = preview.get('result')
    if isinstance(result, str) and len(result) > limit:
        head = limit * 3 // 4
        preview['result'] = (result[:head] + '\n[Tool output excerpt; middle omitted. Full output retained in audit. '
                             'Use targeted inspection for missing material.]\n' + result[-(limit - head):])
    return preview


class Skill:
    def __init__(self, name, path, instructions):
        self.name, self.path, self.instructions = name, Path(path), instructions

    @classmethod
    def load(cls, name, mad_root):
        path = Path(mad_root) / 'mad_skills' / name / 'SKILL.md'
        text = path.read_text(encoding='utf-8')
        if text.startswith('---'):
            _, metadata, text = text.split('---', 2)
            yaml.safe_load(metadata)  # Reject malformed frontmatter.
        return cls(name, path, text.strip())


class Agent:
    def __init__(self, name, description, template, model, mad_root=MAD_ROOT,
                 server='http://localhost:8000/v1', show_thinking=False):
        self.name, self.description, self.template, self.model = name, description, template, model
        self.mad_root = Path(mad_root)
        paths = [self.mad_root / 'mad_agents' / folder / template for folder in ['core', 'software']]
        path = next((path for path in paths if path.is_file()), None)
        if path is None:
            raise FileNotFoundError(f'Role template not found: {template}')
        text = path.read_text(encoding='utf-8')
        metadata = {}
        if text.startswith('---'):
            _, header, text = text.split('---', 2)
            metadata = yaml.safe_load(header) or {}
        self.instructions = text.strip()
        common = (self.mad_root / 'mad_agents' / 'TASK.md').read_text(encoding='utf-8')
        self.completion_instruction = common.partition('# Task completion\n')[2].strip()
        self.review_instruction = metadata.get('review_instruction', '')
        self.final_instruction = metadata.get('final_instruction', '')
        self.skill_names = metadata.get('skills', [])
        self.skills = [Skill.load(name, self.mad_root) for name in self.skill_names]
        self.server = server.rstrip('/')
        self.show_thinking = show_thinking

    @classmethod
    def from_definition(cls, definition):
        match = re.fullmatch(r'(.*?)\s*\(([^:]+):(.+)\)', definition.removeprefix('-').strip())
        if not match:
            raise ValueError(f'Invalid agent definition: {definition}')
        role, template, model = [part.strip() for part in match.groups()]
        name, separator, description = role.partition(' who ')
        return cls(name, 'who ' + description if separator else '', template, model)

    def build_system_prompt(self, project):
        common = (self.mad_root / 'mad_agents' / 'TASK.md').read_text(encoding='utf-8')
        parts = [f'# Your identity\nName: {self.name}\nRole: {self.template}\n{self.description}', self.instructions]
        parts.extend(f'## Skill: {skill.name}\n{skill.instructions}' for skill in self.skills)
        parts.append(f'## Project\n{project.objective}\nWorkspace: {project.workspace}\n'
                     f'Success criteria: {json.dumps(project.success)}\n'
                     f'Team: {json.dumps([dict(name=agent.name, role=agent.template, description=agent.description, skills=agent.skill_names) for agent in project.agents])}')
        parts.append(common)
        return '\n\n'.join(parts)

    def _chat(self, messages, json_schema=None, max_tokens=4096):
        payload = dict(model=self.model, messages=messages, max_tokens=max_tokens, temperature=0.2)
        if json_schema is not None:
            payload['response_format'] = {
                'type': 'json_schema',
                'json_schema': {'name': 'mad_step', 'schema': json_schema, 'strict': True},
            }
        response = requests.post(f'{self.server}/chat/completions', json=payload, timeout=300)
        if not response.ok:
            raise RuntimeError(f'Model request failed ({response.status_code}): {response.text}')
        choice = response.json()['choices'][0]
        if choice.get('finish_reason') == 'length':
            raise ValueError('Model response hit its token limit; return a shorter complete response.')
        content = choice['message'].get('content') or ''
        if not self.show_thinking:
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
        return content

    async def ask(self, conversation, actions, review=False):
        for attempt in range(3):
            try:
                raw = await asyncio.to_thread(self._chat,
                                               messages=conversation, json_schema=response_schema(actions, review=review))
                return parse_response(raw, actions), raw
            except (ValueError, RuntimeError, OSError) as error:
                if attempt == 2:
                    raise
                conversation.append({'role': 'user', 'content': f'Request failed: {error}. Return compact valid JSON.'})

    async def work(self, project, event, actions, apply):
        content = event.content if event.type != 'PEER' else json.dumps({
            'event': 'peer', 'sender': event.sender, 'task_id': event.task_id,
            'content': event.content, 'reply': event.metadata.get('reply', False),
        })
        conversation = [
            {'role': 'system', 'content': self.build_system_prompt(project)},
            {'role': 'user', 'content': content},
        ]
        reads, failures = set(), 0
        messages, writes = set(), {}
        observations, unchanged = {}, 0
        records = event.metadata.setdefault('records', [])
        for _ in range(100):
            for index in range(2, len(conversation)):
                message = conversation[index]
                try:
                    data = json.loads(message['content'])
                except (ValueError, TypeError):
                    continue
                if not isinstance(data, dict):
                    continue
                limit = 4000 if index >= len(conversation) - 2 else 400
                if message['role'] == 'user' and data.get('request', {}).get('action') == 'tool':
                    preview = tool_preview(data, limit)
                    if index < len(conversation) - 2:
                        preview.pop('next_action', None)
                    message['content'] = json.dumps(preview, ensure_ascii=False)
                elif message['role'] == 'assistant' and data.get('action') == 'tool':
                    for key in ('content', 'command'):
                        if isinstance(data.get(key), str) and len(data[key]) > limit:
                            data[key] = data[key][:limit] + ' [historical tool argument excerpt; original retained in audit]'
                    message['content'] = json.dumps(data, ensure_ascii=False)
            step, raw = await self.ask(conversation, actions, review=event.type != 'TASK')
            try:
                message = None
                path = None
                if step['action'] == 'message':
                    message = (step['recipient'], ' '.join(step['content'].split()))
                    if message in messages:
                        raise ValueError('Message already sent this turn. Continue your task or submit a result.')
                elif step['action'] == 'tool' and step.get('tool') in {'write', 'append'}:
                    path = str(Path(step.get('path', '.')))
                    if step['tool'] == 'write' and path in writes and writes[path] == step.get('content', ''):
                        raise ValueError('Identical content already written this turn. Verify the work or submit a result.')
                done, result = await apply(self, step, event, reads, records)
                if result is not None and result.get('success', True):
                    if message is not None:
                        messages.add(message)
                    if path is not None:
                        if step['tool'] == 'write':
                            writes[path] = step.get('content', '')
                        else:
                            writes.pop(path, None)
                if step['action'] == 'tool' and result.get('success', True):
                    if step.get('tool') in {'list', 'read', 'browser_check'}:
                        key = (step['tool'], str(Path(step.get('path') or '.')))
                        observed = result.get('result')
                        if step['tool'] == 'browser_check':
                            page = json.loads(observed)
                            observed = {key: page.get(key) for key in ('status', 'title', 'errors')}
                        unchanged = unchanged + 1 if key in observations and observations[key] == observed else 0
                        observations[key] = observed
                        if unchanged >= 3:
                            raise ValueError('Repeated inspections show no change. Submit a result or decision, '
                                             'or take corrective action instead of checking again.')
                    else:
                        observations.clear()
                        unchanged = 0
                failures = failures + 1 if step['action'] == 'tool' and not result.get('success', True) else 0
                if failures >= 3:
                    raise RuntimeError(f'{self.name} repeated failed tools without resolving them.')
            except (ValueError, TypeError, KeyError) as error:
                failures += 1
                if failures >= 3:
                    raise RuntimeError(f'{self.name} repeated invalid actions: {error}') from error
                done, result = False, {'success': False, 'error': str(error)}
            if done:
                return
            conversation.extend([{'role': 'assistant', 'content': raw},
                                 {'role': 'user', 'content': json.dumps(result, ensure_ascii=False)}])
        raise RuntimeError(f'{self.name} exceeded 100 steps without finishing its turn.')
