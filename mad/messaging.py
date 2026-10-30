"""Agent messages and inbox delivery, without interpreting decisions."""
import asyncio
from dataclasses import dataclass, field


@dataclass
class Message:
    sender: str
    recipient: str
    type: str
    content: str = ''
    task_id: str | None = None
    metadata: dict = field(default_factory=dict)


class MessageHub:
    def __init__(self):
        self.inboxes = {}
        self.messages = []

    def register(self, name):
        self.inboxes.setdefault(name, asyncio.Queue())

    async def send(self, message):
        if message.recipient not in self.inboxes:
            raise ValueError(f'Unknown recipient: {message.recipient}')
        self.messages.append(message)
        await self.inboxes[message.recipient].put(message)

    async def receive(self, name):
        return await self.inboxes[name].get()

    def history(self):
        return list(self.messages)
