"""Colorful console output; no agent or task decisions live here."""
from rich.console import Console
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


def start(task, workspace, agents, log_path=None):
    banner = Text()
    for index, line in enumerate(MAD_ART.strip('\n').splitlines()):
        banner.append(line + '\n', style=f'bold {COLORS[index % len(COLORS)]}')
    console.print(banner)
    console.print('>>> MULTI AGENT DRIVER.', style='bold white')
    console.print('\nProject', style='bold cyan')
    console.print(task, markup=False, highlight=False)
    console.print('\nAgents', style='bold cyan')
    for index, agent in enumerate(agents):
        console.print(Text.assemble((f'  ● {agent.name}', COLORS[(index + 3) % len(COLORS)]),
                                   (f' → {agent.intelligence.model}', 'dim')))
    console.print(Text(f'\nWorkspace: {workspace}', style='dim'))
    if log_path is not None:
        console.print(Text(f'Log: {log_path}', style='dim'))
    console.print()


def activity(name):
    console.print(Text.assemble(('Thinking: ', 'green'), (name, 'bold'), '…'))


def message(sender, recipient, text):
    console.print(Text(f'◆ {sender} → {recipient}', style='bold cyan'))
    console.print(text, markup=False, highlight=False)
    console.print()


def tool_result(name, returncode, stdout, stderr):
    color = 'green' if returncode == 0 else 'red'
    symbol = '✓' if returncode == 0 else '✗'
    console.print(Text(f'    {symbol} {name}: Python exited {returncode}', style=color))
    if stdout:
        console.print(stdout.rstrip(), style='dim', markup=False, highlight=False)
    if stderr:
        console.print(stderr.rstrip(), style='dim' if returncode == 0 else 'red', markup=False, highlight=False)


def finish(summary):
    console.print('\nProject finished', style='bold cyan')
    console.print(summary, markup=False, highlight=False)


def error(message):
    console.print(Text(str(message) or type(message).__name__, style='bold red'))


def code_block(value, language='text'):
    value = str(value).rstrip("\n")
    fence = '```'
    while fence in value:
        fence += '`'
    return f'{fence}{language}\n{value}\n{fence}'


def log_entry(entry):
    event = entry['event']
    if event in {'message_received', 'model_request', 'model_response', 'supervisor_finished'}:
        return ''
    timestamp = entry['time'].replace('T', ' ').replace('+00:00', ' UTC')
    agent = entry.get('agent', '')
    body = ''
    if event == 'run_started':
        raw = entry['run_id'] + '.jsonl'
        team = '\n'.join(f'- **{name}**: {model}' for name, model in entry['models'].items())
        return (f"# MAD run\n\nStarted: {timestamp}\n\n[Full event data]({raw})\n\n"
                f"## Project\n\n{entry['task']}\n\nWorkspace: {entry['workspace']}\n\n"
                f"Server: {entry['server']}\n\n## Agents\n\n{team}\n\n")
    if event == 'message_sent':
        title = f"{agent} → {entry['recipient']}"
        body = code_block(entry['text'])
    elif event == 'agent_waiting':
        title = f'{agent}: Waiting'
        body = 'Waiting for a new message.'
        if entry.get('reason') == 'Message sent':
            recipient = f" to {entry['recipient']}" if entry.get('recipient') else ''
            body = f'Message sent{recipient}. ' + body
    elif event == 'python_started':
        title = f'{agent}: Python execution'
        body = code_block(entry['code'], 'python')
    elif event == 'python_finished':
        title = f"{agent}: Python exited {entry['returncode']}"
        if entry['stdout']:
            body += '### Standard output\n\n' + code_block(entry['stdout']) + '\n\n'
        if entry['stderr']:
            body += '### Standard error\n\n' + code_block(entry['stderr']) + '\n\n'
        if not body:
            body = 'No output.'
    elif event == 'run_finished':
        title = 'Run finished'
        body = str(entry['summary'])
    elif event in {'action_failed', 'agent_failed', 'run_failed', 'run_cancelled'}:
        title = f"{agent + ': ' if agent else ''}{event.replace('_', ' ')}"
        body = code_block(f"{entry['error_type']}: {entry['error']}")
        for key, label in [('reply', 'Model reply'), ('response_body', 'Provider response'), ('traceback', 'Traceback')]:
            if entry.get(key):
                body += f'\n\n### {label}\n\n' + code_block(entry[key])
    else:
        title = f"{agent} {event.removeprefix('agent_').replace('_', ' ')}".strip()
        details = [f'{key}: {value}' for key, value in entry.items()
                   if key not in {'time', 'run_id', 'event', 'agent'} and value is not None]
        body = code_block('\n'.join(details)) if details else ''
    return f'## {timestamp} — {title}\n\n{body.rstrip()}\n\n'
