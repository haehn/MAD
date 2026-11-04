from dataclasses import dataclass, field
from pathlib import Path
import re

import yaml


def payload(value):
    if not isinstance(value, dict) or set(value) != {'files', 'data'}:
        raise ValueError('INPUT and OUTPUT need files (a list) and data (a mapping).')
    files, data = value['files'], value['data']
    if not isinstance(files, list) or any(not isinstance(p, str) or not p.strip() for p in files):
        raise ValueError('files must list relative paths.')
    if len(set(files)) != len(files) or not isinstance(data, dict):
        raise ValueError('File references must be unique; data must be a mapping.')
    return {'files': list(files), 'data': dict(data)}


@dataclass
class Contract:
    id: str
    setting: str
    inputs: dict
    task: str
    outputs: dict
    acceptance: list
    owner: str
    qa: str
    depends_on: list = field(default_factory=list)
    revision: int = 1
    state: str = 'pending'
    delivery: dict | None = None
    report: dict | None = None
    error: str | None = None

    @classmethod
    def parse(cls, value):
        fields = {'ID', 'SETTING', 'INPUT', 'TASK', 'OUTPUT', 'ACCEPTANCE', 'OWNER', 'QA', 'DEPENDS_ON'}
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError('A contract requires exactly: ' + ', '.join(sorted(fields)))
        for key in ('ID', 'SETTING', 'TASK', 'OWNER', 'QA'):
            if not isinstance(value[key], str) or not value[key].strip():
                raise ValueError(f'{key} must be nonempty text.')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', value['ID']):
            raise ValueError('ID may contain letters, numbers, underscores, and hyphens.')
        acceptance = value['ACCEPTANCE']
        if not isinstance(acceptance, list) or not acceptance or any(not isinstance(c, str) or not c.strip() for c in acceptance):
            raise ValueError('ACCEPTANCE must list explicit conditions.')
        dependencies = value['DEPENDS_ON']
        if not isinstance(dependencies, list) or any(not isinstance(d, str) for d in dependencies) or len(set(dependencies)) != len(dependencies):
            raise ValueError('DEPENDS_ON must list unique contract IDs.')
        outputs = payload(value['OUTPUT'])
        if not outputs['files'] and not outputs['data']:
            raise ValueError('OUTPUT must declare files or inline data.')
        if value['OWNER'] == value['QA']:
            raise ValueError('OWNER and QA must be separate agents.')
        return cls(value['ID'], value['SETTING'], payload(value['INPUT']), value['TASK'],
                   outputs, list(acceptance), value['OWNER'], value['QA'], list(dependencies))

    def definition(self):
        return dict(ID=self.id, SETTING=self.setting, INPUT=self.inputs, TASK=self.task,
                    OUTPUT=self.outputs, ACCEPTANCE=self.acceptance, OWNER=self.owner,
                    QA=self.qa, DEPENDS_ON=self.depends_on)

    def markdown(self):
        metadata = f'# Contract {self.id}\n\nRevision: {self.revision}\nState: {self.state}\n\n'
        sections = []
        for key, value in self.definition().items():
            body = value if isinstance(value, str) else yaml.safe_dump(value, sort_keys=False).rstrip()
            sections.append(f'## {key}\n\n{body}\n')
        return metadata + '\n'.join(sections)


def project(path, *, server=None, model=None, workspace=None):
    """Read Markdown sections; structured sections contain ordinary YAML."""
    path = Path(path).expanduser().resolve()
    names = {'PROJECT', 'WORKSPACE', 'INTELLIGENCE', 'AGENTS', 'SETTING', 'INPUT', 'OUTPUT', 'ACCEPTANCE', 'OWNER', 'QA'}
    sections = {key: [] for key in names}
    section = 'PROJECT'
    for line in path.read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'([A-Z_]+):\s*(.*)', line)
        if match and match[1] in names:
            section = match[1]
            sections[section].append(match[2])
        else:
            sections[section].append(line)
    text = {key: '\n'.join(lines).strip() for key, lines in sections.items()}
    defaults = yaml.safe_load(text['INTELLIGENCE']) or {}
    if not isinstance(defaults, dict):
        raise ValueError('INTELLIGENCE must contain server and model settings.')
    defaults.update({key: value for key, value in [('server', server), ('model', model)] if value is not None})
    definitions = yaml.safe_load(text['AGENTS'])
    if not isinstance(definitions, list) or len(definitions) < 3:
        raise ValueError('AGENTS needs a Supervisor, a worker, and a separate QA agent.')
    agents = []
    for definition in definitions:
        if not isinstance(definition, dict) or not {'name', 'role'} <= definition.keys() or set(definition) - {'name', 'role', 'server', 'model'}:
            raise ValueError('Each agent needs name and role, with optional server and model overrides.')
        settings = {**defaults, **definition}
        if any(not isinstance(settings.get(key), str) or not settings[key].strip() for key in ('name', 'role', 'server', 'model')):
            raise ValueError('Every agent requires a name, role path, server, and model.')
        settings['role'] = str((path.parent / settings['role']).resolve())
        if not Path(settings['role']).is_file():
            raise ValueError(f'Role file does not exist: {settings["role"]}')
        agents.append(settings)
    if len({agent['name'] for agent in agents}) != len(agents):
        raise ValueError('Agent names must be unique.')
    root = Contract.parse(dict(
        ID='project', SETTING=text['SETTING'] or 'Coordinate the complete project.',
        INPUT=yaml.safe_load(text['INPUT']) or {'files': [], 'data': {}}, TASK=text['PROJECT'],
        OUTPUT=yaml.safe_load(text['OUTPUT']), ACCEPTANCE=yaml.safe_load(text['ACCEPTANCE']),
        OWNER=text['OWNER'] or agents[0]['name'], QA=text['QA'], DEPENDS_ON=[]))
    if root.owner not in {a['name'] for a in agents} or root.qa not in {a['name'] for a in agents}:
        raise ValueError('Root OWNER and QA must name configured agents.')
    location = workspace or text['WORKSPACE']
    if not location:
        raise ValueError('WORKSPACE is required.')
    return root, Path(location).expanduser().resolve(), agents
