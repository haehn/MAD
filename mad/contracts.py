"""MAD's contract lifecycle; CrewAI owns execution and retries."""
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, StrictInt


@dataclass
class Criterion:
    id: str
    criterion: str
    verification: Literal['inspection', 'execution']


@dataclass
class Contract:
    id: str
    owner: str  # Exact configured agent name from project AGENTS.
    input: str
    task: str
    output: list[str]
    acceptance: list[Criterion]
    dependencies: list[str]
    state: str = field(default='OPEN', init=False)


class Plan(BaseModel):
    contracts: list[Contract] = Field(min_length=1)


class Delivery(BaseModel):
    output: str = Field(min_length=1, description='The actual substantive result, including the full text for inline work.')
    files: list[str] = Field(default_factory=list, description='Exactly the declared contract output paths; empty for inline delivery.')


class CriterionResult(BaseModel):
    criterion_id: str = Field(description='The contract acceptance criterion ID, such as A1.')
    criterion: str = Field(default='', description='Criterion text; MAD resolves it from criterion_id.')
    status: Literal['PASS', 'FAIL']
    evidence: str = ''
    verification: Literal['inspection', 'execution'] | None = Field(default=None, description='Resolved by MAD from criterion_id; may be omitted.')
    executions: list[StrictInt] = Field(default_factory=list, description='Numbered executions from this QC review; optional for inspection.')


class QCResult(BaseModel):
    status: Literal['PASS', 'FAIL']
    findings: str = Field(min_length=1)
    checks: list[CriterionResult] = Field(default_factory=list)


class Acceptance(BaseModel):
    accepted: list[str]


class Completion(BaseModel):
    complete: bool
    summary: str = Field(min_length=1)
