"""MAD presentation, with explicit roles, contract IDs, and work/QA states."""
import sys

from rich.console import Console
from rich.table import Table
from rich.text import Text
from mad.ui import MAD_ART, COLORS

console = Console(file=sys.stdout)


def start(root, workspace, agents, audit):
    banner = Text()
    for index, line in enumerate(MAD_ART.strip('\n').splitlines()):
        banner.append(line + '\n', style=f'bold {COLORS[index % len(COLORS)]}')
    console.print(banner)
    console.print('>>> MULTI AGENT DRIVER.', style='bold white')
    console.print('\nProject', style='bold cyan')
    console.print(root.task, markup=False, highlight=False)
    table = Table(title='Team', expand=True)
    for heading in ('Agent', 'Role', 'Model', 'Server'):
        table.add_column(heading)
    for agent in agents:
        table.add_row(agent.name, agent.role, agent.intelligence.model, agent.server)
    console.print(table)
    console.print(Text(f'Workspace: {workspace}\nAudit: {audit}\n', style='dim'))


def status(agent, contract, phase, state):
    color = 'red' if state in {'failed', 'blocked'} else 'green' if state == 'accepted' else 'cyan'
    console.print(Text(f'◆ {agent.name} [{agent.role}] · {contract.id} r{contract.revision} · {phase} · {state}', style=color))


def finish(accepted, reason):
    console.print('\nProject accepted' if accepted else '\nProject not accepted', style='bold green' if accepted else 'bold red')
    console.print(reason, markup=False, highlight=False)


def error(message):
    console.print(Text(str(message), style='bold red'))
