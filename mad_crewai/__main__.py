import argparse

from mad_crew.__main__ import project
from .runtime import Runtime


def main():
    parser = argparse.ArgumentParser(description='MAD with CrewAI: Supervisor and generic workers.')
    parser.add_argument('project', help='Project Markdown file')
    parser.add_argument('--server', default='http://localhost:8000/v1')
    parser.add_argument('--model', default='Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--workspace', help='Override project workspace')
    args = parser.parse_args()
    try:
        task, workspace = project(args.project)
        runtime = Runtime(task, args.workspace or workspace, args.server, args.model, args.workers)
        print(f'Workspace: {runtime.workspace}\nAudit: {runtime.audit}')
        print(runtime.run())
        return 0
    except KeyboardInterrupt:
        print('Stopped.')
        return 130
    except Exception as error:
        print(f'Crew failed: {error}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
