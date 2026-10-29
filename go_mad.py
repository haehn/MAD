#!/usr/bin/env python3

import sys
from mad.parser import parse_project
from mad.llm import LLMClient


def main():
    if len(sys.argv) != 2:
        print("Usage: python go_mad.py Project.md")
        sys.exit(1)

    project = parse_project(sys.argv[1])

    print("\nMAD — Multi Agent Development\n")

    print("TASK:")
    print(project["task"])

    print("\nAGENTS:")
    for agent in project["agents"]:
        print(f"  - {agent}")

    print("\nTesting LLM...\n")

    llm = LLMClient()

    answer = llm.chat(
        model="Qwen/Qwen3-8B",
        messages=[
            {
                "role": "user",
                "content": f"""
You are the Director of a software engineering project.

Project task:

{project['task']}

Give me a concise implementation plan with 3-5 tasks.
""",
            }
        ],
    )

    print("DIRECTOR:")
    print(answer)


if __name__ == "__main__":
    main()
