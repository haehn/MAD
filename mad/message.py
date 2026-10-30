from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Message:
    sender: str
    recipient: str
    type: str
    content: str = ""
    task_id: str | None = None
    metadata: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
