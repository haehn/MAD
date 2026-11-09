"""MAD's contract lifecycle; CrewAI owns execution and retries."""
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field


@dataclass
class Contract:
    id: str
    role: str
    input: str
    task: str
    output: list[str]
    acceptance: list[str]
    dependencies: list[str]
    state: str = field(default='OPEN', init=False)


class Plan(BaseModel):
    contracts: list[Contract] = Field(min_length=1)


class Delivery(BaseModel):
    output: str = Field(min_length=1, description='The actual substantive result, including the full text for inline work.')
    files: list[str] = Field(default_factory=list, description='Exactly the declared contract output paths; empty for inline delivery.')


class QCResult(BaseModel):
    status: Literal['PASS', 'FAIL']
    findings: str = Field(min_length=1)


class Acceptance(BaseModel):
    accepted: list[str]


class Completion(BaseModel):
    complete: bool
    summary: str = Field(min_length=1)
