#!/usr/bin/env python3

import sys

from mad.project import Project


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
        print(f"  - {agent}")


if __name__ == "__main__":
    main()
