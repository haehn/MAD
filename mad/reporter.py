"""Console presentation boundary for orchestration and agent execution."""
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


class Reporter:
    def __init__(self, output=None):
        self.output = output if output is not None else console

    def print(self, *args, **kwargs):
        self.output.print(*args, **kwargs)

    def start(self, project, workspace, verbose=False):
        self._print_banner()
        self._print_project(project, verbose=verbose)
        self.print(f'[dim]Workspace: {workspace}[/dim]')
        self.print()

    def plan(self, tasks):
        self.print('\n[bold cyan]Initial Task Graph[/bold cyan]')
        for task in tasks:
            self.print(f'  [yellow]{task.id}[/yellow] {task.title} [dim]→ {task.owner}[/dim]')
        self.print()

    def review_rejected(self, error, response=None):
        self.print(f'[bold yellow]⚠ Replan rejected: {error}[/bold yellow]')
        if response is not None:
            self.print('Rejected Supervisor response:')
            self.print(response, markup=False)

    def review_accepted(self, reason):
        self.print('\n[bold magenta]Supervisor Review[/bold magenta]')
        if reason:
            self.print(f'[dim]{reason}[/dim]')
        self.print('[green]✓ Supervisor decision accepted[/green]')

    def finish(self, tasks):
        blocked = sum(task.status == 'blocked' for task in tasks)
        if any(task.status == 'pending' for task in tasks):
            self.print('[yellow]No runnable tasks remain. The project may be blocked.[/yellow]')
        if blocked:
            self.print(f'[yellow]Project ended with {blocked} blocked task(s).[/yellow]')

    def task_result(self, task, sender, elapsed, artifact, summary, output):
        if task.status == 'review':
            timing = f' in {elapsed:.1f}s' if elapsed is not None else ''
            self.print(f'[bold yellow]◇ {task.id} ready for review[/bold yellow] {task.title} [dim]({sender}{timing})[/dim]')
        else:
            timing = f' after {elapsed:.1f}s' if elapsed is not None else ''
            self.print(f'[bold red]✗ {task.id} blocked[/bold red] [dim]({sender}{timing})[/dim]')
        self.print(f'[dim]↳ audit: {artifact}[/dim]')
        if summary:
            self.print(f'\n[bold cyan]{sender}[/bold cyan]')
            self.print(summary)
            self.print()
        if output:
            self.print(f'[bold white]{sender} result[/bold white]')
            self.print(output)
            self.print()

    def project_review(self, accepted, reason):
        label = 'Project accepted' if accepted else 'Project not accepted'
        self.print(f'[bold]{label}[/bold]')
        if reason:
            self.print(reason, markup=False)

    def _print_banner(self):
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

        self.print(banner)

        self.print(
            "[bold white]>>> MULTI AGENT DRIVER.[/bold white]",
            justify="left",
        )

        self.print()

    def _print_agents(self, project, verbose=False):
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

            self.print(table)

        else:
            self.print("[bold cyan]Agents[/bold cyan]")

            colors = [
                "bright_green",
                "bright_cyan",
                "bright_magenta",
                "bright_yellow",
                "bright_blue",
            ]

            for i, agent in enumerate(project.agents):
                color = colors[i % len(colors)]

                self.print(
                    f"  [{color}]●[/{color}] "
                    f"[bold]{agent.name}[/bold] "
                    f"[dim]({agent.template} → {agent.model})[/dim]"
                )

        self.print()

    def _print_project(self, project, verbose=False):
        if verbose:
            self.print("[bold cyan]Objective[/bold cyan]")
            self.print(project.objective)
            self.print()

            self.print("[bold cyan]Workspace[/bold cyan]")
            self.print(project.workspace)
            self.print()

            if project.success:
                self.print("[bold cyan]Success Criteria[/bold cyan]")

                for item in project.success:
                    self.print(f"  [green]✓[/green] {item}")

                self.print()

        else:
            self.print(
                f"[bold cyan]Project[/bold cyan]  {project.objective}"
            )
            self.print()

        self._print_agents(project, verbose=verbose)
