"""Validate and route model-authored peer messages."""
from mad.message import Message


class MessageRouter:
    def __init__(self, agent, project, hub, reporter):
        self.agent = agent
        self.project = project
        self.hub = hub
        self.reporter = reporter

    async def route(self, messages, task_id=None):
        valid_agents = {agent.name for agent in self.project.agents}
        for outgoing in messages:
            recipient = outgoing.get('recipient')
            if recipient == self.agent.name:
                continue
            if recipient not in valid_agents:
                self.reporter.print(f'[yellow]⚠ {self.agent.name} tried to message unknown agent {recipient!r}[/yellow]')
                continue
            await self.hub.send(Message(sender=self.agent.name, recipient=recipient, type='PEER', task_id=task_id, content=outgoing.get('content', ''), metadata={'message_type': outgoing.get('type', 'message')}))
