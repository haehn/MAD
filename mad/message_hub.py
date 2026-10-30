import asyncio

class MessageHub:
    def __init__(self):
        self.inboxes = {}
        self.messages = []

    def register(self, name):
        if name not in self.inboxes:
            self.inboxes[name] = asyncio.Queue()

    async def send(self, message):
        if message.recipient not in self.inboxes:
            raise ValueError(
                f"Unknown message recipient: {message.recipient}"
            )

        self.messages.append(message)

        await self.inboxes[message.recipient].put(message)

    async def receive(self, name):
        return await self.inboxes[name].get()

    def history(self):
        return list(self.messages)
