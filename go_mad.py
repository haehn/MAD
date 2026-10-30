#!/usr/bin/env python3

import argparse

from mad.project import Project
from mad.llm import LLM
from mad.ui import console, print_banner, print_project


def main():
    parser = argparse.ArgumentParser(
        description="MAD — Multi Agent Driven"
    )

    parser.add_argument(
        "project",
        help="Path to the project Markdown file",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed project and agent information",
    )

    parser.add_argument(
        "--show-thinking",
        action="store_true",
        help="Show model reasoning blocks",
    )

    args = parser.parse_args()

    project = Project.from_file(args.project)

    print_banner()
    print_project(project, verbose=args.verbose)

    director = project.agents[0]

    system_prompt = director.build_system_prompt(project)

    llm = LLM(
        show_thinking=args.show_thinking
    )

    console.print(
        f"[bold bright_magenta]{director.name.upper()}[/bold bright_magenta]"
    )

    response = llm.chat(
        model=director.model,
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": (
                    "Review this project and create the initial "
                    "project plan and task breakdown."
                ),
            },
        ],
    )

    console.print(response)


if __name__ == "__main__":
    main()
