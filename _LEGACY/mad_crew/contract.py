from dataclasses import dataclass
import json
from pathlib import Path


SECTIONS = ('SETTING', 'INPUT', 'TASK', 'OUTPUT', 'ACCEPTANCE')


def workspace_path(workspace, name):
    if not isinstance(name, str) or not name.strip():
        raise ValueError('File references must be nonempty relative paths.')
    root = Path(workspace).resolve()
    relative = Path(name)
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root):
        raise ValueError('File references must stay inside the workspace.')
    if '.mad' in relative.parts or '.mad' in path.relative_to(root).parts:
        raise ValueError('.mad contains infrastructure records, not deliverables.')
    return path


def payload(value):
    if not isinstance(value, dict) or set(value) != {'files', 'data'}:
        raise ValueError('INPUT, OUTPUT, and results contain files and data.')
    if not isinstance(value['files'], list) or any(not isinstance(p, str) or not p.strip() for p in value['files']):
        raise ValueError('files must be a list of relative paths.')
    if len(set(value['files'])) != len(value['files']) or not isinstance(value['data'], dict):
        raise ValueError('File references must be unique; data must be an object.')
    return {'files': list(value['files']), 'data': dict(value['data'])}


@dataclass
class Contract:
    setting: str
    inputs: dict
    task: str
    outputs: dict
    acceptance: list

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != set(SECTIONS):
            raise ValueError('A contract requires SETTING, INPUT, TASK, OUTPUT, ACCEPTANCE.')
        if any(not isinstance(value[key], str) or not value[key].strip() for key in ('SETTING', 'TASK')):
            raise ValueError('SETTING and TASK must be nonempty text.')
        criteria = value['ACCEPTANCE']
        if not isinstance(criteria, list) or not criteria or any(not isinstance(c, str) or not c.strip() for c in criteria):
            raise ValueError('ACCEPTANCE must be a nonempty list of conditions.')
        inputs, outputs = payload(value['INPUT']), payload(value['OUTPUT'])
        if not outputs['files'] and not outputs['data']:
            raise ValueError('OUTPUT must request files or named inline data fields.')
        return cls(value['SETTING'].strip(), inputs, value['TASK'].strip(), outputs, list(criteria))

    def to_dict(self):
        return dict(zip(SECTIONS, (self.setting, self.inputs, self.task, self.outputs, self.acceptance)))

    def markdown(self):
        parts = []
        for section, value in self.to_dict().items():
            if section in ('INPUT', 'OUTPUT'):
                value = '```json\n' + json.dumps(value, ensure_ascii=False, indent=2) + '\n```'
            elif section == 'ACCEPTANCE':
                value = '\n'.join('- ' + item.replace('\n', '\n  ') for item in value)
            parts.append(f'# {section}\n\n{value}')
        return '\n\n'.join(parts) + '\n'

    @classmethod
    def from_markdown(cls, text):
        sections = {}
        current = None
        for line in text.splitlines():
            if line.startswith('# ') and line[2:] in SECTIONS:
                current = line[2:]
                if current in sections:
                    raise ValueError(f'Duplicate section: {current}')
                sections[current] = []
            elif current is not None:
                sections[current].append(line)
            elif line.strip():
                raise ValueError('Contract starts with # SETTING.')
        if tuple(sections) != SECTIONS:
            raise ValueError('Contract sections must be SETTING, INPUT, TASK, OUTPUT, ACCEPTANCE in order.')
        result = {key: '\n'.join(lines).strip() for key, lines in sections.items()}
        for key in ('INPUT', 'OUTPUT'):
            block = result[key]
            if not block.startswith('```json\n') or not block.endswith('\n```'):
                raise ValueError(f'{key} must contain a fenced JSON object.')
            result[key] = json.loads(block[8:-4])
        criteria = []
        for line in result['ACCEPTANCE'].splitlines():
            if line.startswith('- '):
                criteria.append(line[2:])
            elif line.startswith('  ') and criteria:
                criteria[-1] += '\n' + line[2:]
            else:
                raise ValueError('ACCEPTANCE conditions must be Markdown list items.')
        result['ACCEPTANCE'] = criteria
        return cls.from_dict(result)
