import argparse
import asyncio
from pathlib import Path

from mad import ui
from .runtime import Runtime


def project(path):
    sections = {'task': [], 'workspace': [], 'success': [], 'agents': []}
    section = 'task'
    for raw in Path(path).expanduser().read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        header = line[:-1].lower() if line.endswith(':') else ''
        if header in sections:
            section = header
        elif line:
            sections[section].append(line)
    task = '\n'.join(sections['task'])
    if sections['success']:
        task += '\nSuccess conditions:\n' + '\n'.join(sections['success'])
    if not sections['task'] or not sections['workspace']:
        raise ValueError('Project needs an objective and WORKSPACE. Crew roles are defined by contracts, not AGENTS.')
    return task, ' '.join(sections['workspace'])


def main():
    parser = argparse.ArgumentParser(description='MAD: Supervisor and generic contract workers.')
    parser.add_argument('project', help='Project Markdown file')
    parser.add_argument('--server', default='http://localhost:8000/v1')
    parser.add_argument('--model', default='Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8')
    parser.add_argument('--workers', type=int, default=2, help='Maximum crew size; Supervisor selects how many to use')
    parser.add_argument('--workspace', help='Override project workspace')
    args = parser.parse_args()
    try:
        task, workspace = project(args.project)
        accepted = asyncio.run(Runtime(task, args.workspace or workspace, args.server, args.model, args.workers).run())
        return 0 if accepted else 1
    except KeyboardInterrupt:
        ui.error('Stopped.')
        return 130
    except ExceptionGroup as error:
        for problem in error.exceptions:
            ui.error(problem)
        return 1
    except Exception as error:
        ui.error(error)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
