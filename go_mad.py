#!/usr/bin/env python3

import argparse
import asyncio

from mad.project import Project
from mad.runtime import Runtime


def main():
    parser = argparse.ArgumentParser(
        description="MAD — Multi Agent Driver"
    )

    parser.add_argument(
        "project",
        help="Path to the project Markdown file",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed project information",
    )

    parser.add_argument(
        "--show-thinking",
        action="store_true",
        help="Show model reasoning blocks",
    )

    parser.add_argument(
        "--server",
        default="http://localhost:8000/v1",
        help="vLLM OpenAI-compatible server",
    )

    args = parser.parse_args()

    project = Project.from_file(
        args.project
    )

    runtime = Runtime(
        project=project,
        server=args.server,
        show_thinking=args.show_thinking,
        verbose=args.verbose,
    )

    accepted = asyncio.run(
        runtime.run()
    )

    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
