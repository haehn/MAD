from rich.console import Console
from rich.text import Text
from rich.table import Table


console = Console()


MAD_ART = r"""
███╗   ███╗ █████╗ ██████╗
████╗ ████║██╔══██╗██╔══██╗
██╔████╔██║███████║██║  ██║
██║╚██╔╝██║██╔══██║██║  ██║
██║ ╚═╝ ██║██║  ██║██████╔╝
╚═╝     ╚═╝╚═╝  ╚═╝╚═════╝
"""


def print_banner():
    banner = Text()

    colors = [
        "bright_magenta",
        "bright_red",
        "bright_yellow",
        "bright_green",
        "bright_cyan",
        "bright_blue",
    ]

    for i, line in enumerate(MAD_ART.strip("\n").splitlines()):
        banner.append(line + "\n", style=f"bold {colors[i % len(colors)]}")

    console.print(banner)

    console.print(
        "[bold white]  MULTI AGENT DRIVEN[/bold white]",
        justify="left",
    )

    console.print()


def print_agents(project, verbose=False):
    if verbose:
        table = Table(
            title="Agents",
            show_header=True,
            header_style="bold cyan",
        )

        table.add_column("Agent", style="bold green")
        table.add_column("Template", style="yellow")
        table.add_column("Model", style="magenta")
        table.add_column("Skills", style="cyan")

        for agent in project.agents:
            table.add_row(
                agent.name,
                agent.template,
                agent.model,
                "\n".join(agent.skill_names),
            )

        console.print(table)

    else:
        console.print("[bold cyan]Agents[/bold cyan]")

        colors = [
            "bright_green",
            "bright_cyan",
            "bright_magenta",
            "bright_yellow",
            "bright_blue",
        ]

        for i, agent in enumerate(project.agents):
            color = colors[i % len(colors)]

            console.print(
                f"  [{color}]●[/{color}] "
                f"[bold]{agent.name}[/bold] "
                f"[dim]({agent.template} → {agent.model})[/dim]"
            )

    console.print()


def print_project(project, verbose=False):
    if verbose:
        console.print("[bold cyan]Objective[/bold cyan]")
        console.print(project.objective)
        console.print()

        console.print("[bold cyan]Workspace[/bold cyan]")
        console.print(project.workspace)
        console.print()

        if project.success:
            console.print("[bold cyan]Success Criteria[/bold cyan]")

            for item in project.success:
                console.print(f"  [green]✓[/green] {item}")

            console.print()

    else:
        console.print(
            f"[bold cyan]Project[/bold cyan]  {project.objective}"
        )
        console.print()

    print_agents(project, verbose=verbose)
