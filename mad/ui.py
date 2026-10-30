"""Colorful console output; no agent or task decisions live here."""
from rich.console import Console
from rich.table import Table
from rich.text import Text


console = Console()
MAD_ART = r"""
███╗   ███╗ █████╗ ██████╗
████╗ ████║██╔══██╗██╔══██╗
██╔████╔██║███████║██║  ██║
██║╚██╔╝██║██╔══██║██║  ██║
██║ ╚═╝ ██║██║  ██║██████╔╝
╚═╝     ╚═╝╚═╝  ╚═╝╚═════╝
"""
COLORS = ['bright_magenta', 'bright_red', 'bright_yellow', 'bright_green', 'bright_cyan', 'bright_blue']


def start(project, workspace, verbose=False):
    banner = Text()
    for index, line in enumerate(MAD_ART.strip('\n').splitlines()):
        banner.append(line + '\n', style=f'bold {COLORS[index % len(COLORS)]}')
    console.print(banner)
    console.print('>>> MULTI AGENT DRIVER.', style='bold white')
    console.print()
    console.print('Objective' if verbose else 'Project', style='bold cyan')
    console.print(project.objective, markup=False, highlight=False)
    if verbose and project.success:
        console.print('\nSuccess Criteria', style='bold cyan')
        for criterion in project.success:
            console.print(Text.assemble(('  ✓ ', 'green'), criterion))
    console.print()
    if verbose:
        table = Table(title='Agents', header_style='bold cyan')
        for label, color in [('Agent', 'bold green'), ('Template', 'yellow'),
                             ('Model', 'magenta'), ('Skills', 'cyan')]:
            table.add_column(label, style=color)
        for agent in project.agents:
            table.add_row(Text(agent.name), Text(agent.template), Text(agent.model),
                          Text('\n'.join(agent.skill_names)))
        console.print(table)
    else:
        console.print('Agents', style='bold cyan')
        for index, agent in enumerate(project.agents):
            console.print(Text.assemble((f'  ● {agent.name}', COLORS[(index + 3) % len(COLORS)]),
                                       (f' ({agent.template} → {agent.model})', 'dim')))
    console.print(Text(f'\nWorkspace: {workspace}\n', style='dim'))


def plan(tasks):
    console.print('\nInitial Task Graph', style='bold cyan')
    for task in tasks:
        console.print(Text.assemble((f'  {task.id} ', 'yellow'), task.title, (f' → {task.owner}', 'dim')))
    console.print()


def activity(agent, event):
    color = 'cyan' if event.type == 'PEER' else 'green' if event.type == 'TASK' else 'bright_magenta'
    console.print(Text.assemble(('▶ ', color), (agent.name, 'bold'),
                               (f': {event.type} {event.task_id or ""}', 'dim')))


def tool_result(agent, step, outcome):
    target = step.get('path') or step.get('command') or step.get('name', '')
    console.print(Text(f'    {agent}: {step["tool"]} {target} → {outcome["success"]}',
                       style='dim' if outcome['success'] else 'yellow'))


def task_result(task, artifact):
    if task.status == 'review':
        console.print(Text.assemble((f'◇ {task.id} ready for review ', 'bold yellow'),
                                   task.title, (f' ({task.owner})', 'dim')))
    else:
        console.print(Text(f'✗ {task.id} blocked ({task.owner})', style='bold red'))
    console.print(Text(f'↳ audit: {artifact}', style='dim'))
    for heading, value in [(task.owner, task.result.get('summary')),
                           (f'{task.owner} result', task.result.get('output'))]:
        if value:
            console.print(Text(f'\n{heading}', style='bold cyan'))
            console.print(value, markup=False, highlight=False)
    console.print()


def review(task, verdict, reason):
    console.print('\nSupervisor Review', style='bold magenta')
    color = {'accept': 'green', 'rework': 'yellow', 'blocked': 'red'}[verdict]
    console.print(Text(f'  {task.id}: {verdict} — {reason}', style=color))


def finish(accepted, reason):
    console.print('Project accepted' if accepted else 'Project not accepted',
                  style='bold green' if accepted else 'bold yellow')
    console.print(reason, markup=False, highlight=False)


def error(message):
    console.print(Text(str(message), style='bold red'))
