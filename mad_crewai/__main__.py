import argparse

from .contract import project
from .runtime import Runtime
from . import ui


def main():
    parser = argparse.ArgumentParser(description='MAD: role-based CrewAI agents and parallel contracts with QA.')
    parser.add_argument('project', help='Project Markdown file')
    parser.add_argument('--server', help='Override the default model server')
    parser.add_argument('--model', help='Override the default model')
    parser.add_argument('--workspace', help='Override the project workspace')
    args = parser.parse_args()
    try:
        root, workspace, agents = project(args.project, server=args.server, model=args.model, workspace=args.workspace)
        return 0 if Runtime(root, workspace, agents).run() else 1
    except KeyboardInterrupt:
        ui.error('Stopped.')
        return 130
    except Exception as error:
        ui.error(error)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
