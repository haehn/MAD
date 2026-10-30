#!/usr/bin/env python3

import sys

from mad.project import Project
from mad.llm import LLM


def main():
    if len(sys.argv) != 2:
        print("Usage: python go_mad.py <project.md>")
        sys.exit(1)

    try:
        project = Project.from_file(sys.argv[1])
    except (FileNotFoundError, ValueError) as error:
        print(f"MAD error: {error}")
        sys.exit(1)

    print("\nMAD — Multi Agent Driven\n")

    print("OBJECTIVE:")
    print(project.objective)

    print("\nWORKSPACE:")
    print(project.workspace)

    if project.success:
        print("\nSUCCESS:")
        for item in project.success:
            print(f"  - {item}")

    print("\nAGENTS:")

    for agent in project.agents:
        print(f"\n{agent.name}")
        print(f"  template: {agent.template}")
        print(f"  model:    {agent.model}")

        if agent.skill_names:
            print("  skills:")
            for skill in agent.skill_names:
                print(f"    - {skill}")

    director = project.agents[0]

    system_prompt = director.build_system_prompt(project)

    llm = LLM()

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

    print("\nDIRECTOR:\n")
    print(response)

if __name__ == "__main__":
    main()
