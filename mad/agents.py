import asyncio
from dataclasses import dataclass

import requests


@dataclass
class Message:
    sender: str
    recipient: str
    text: str


class Bus:
    def __init__(self):
        self.inboxes = {}

    def register(self, name):
        if name in self.inboxes:
            raise ValueError(f"Agent already registered: {name}")
        self.inboxes[name] = asyncio.Queue()

    async def send(self, sender, recipient, text):
        if recipient not in self.inboxes:
            raise ValueError(f"Unknown recipient: {recipient}")
        await self.inboxes[recipient].put(Message(sender, recipient, text))

    async def receive(self, name):
        return await self.inboxes[name].get()


class ResponseTooLong(RuntimeError):
    pass


@dataclass
class Intelligence:
    server: str
    model: str

    async def reply(self, messages, *, json_mode=False):
        payload = {"model": self.model, "messages": list(messages),
                   "temperature": 0.2, "max_tokens": 4096 if json_mode else 512}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        response = await asyncio.to_thread(
            requests.post, f"{self.server.rstrip('/')}/chat/completions",
            json=payload, timeout=60,
        )
        response.raise_for_status()
        choice = response.json()["choices"][0]
        if choice.get("finish_reason") == "length":
            error = ResponseTooLong("Model response reached its token limit.")
            partial = choice.get("message", {}).get("content")
            if partial:
                error.add_note(f"Partial response:\n{partial}")
            raise error
        return choice["message"]["content"]


@dataclass
class Agent:
    name: str
    intelligence: Intelligence
    messaging: Bus
