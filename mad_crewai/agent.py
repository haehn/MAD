import os
from pathlib import Path


class Agent:
    """A configured role and model, executing one CrewAI assignment at a time."""

    def __init__(self, name, role, server, model):
        os.environ.setdefault('OTEL_SDK_DISABLED', 'true')
        os.environ.setdefault('CREWAI_TELEMETRY_DISABLED', 'true')
        from crewai import Agent as CrewAIAgent, LLM

        self.name = name
        self.instructions = Path(role).read_text(encoding='utf-8')
        self.role = self.instructions.splitlines()[0].lstrip('# ').strip() or Path(role).stem
        self.server = server
        self.intelligence = LLM(model=model, custom_openai=True, base_url=server,
                                api_key=os.environ.get('MAD_API_KEY', 'local'),
                                temperature=0.2, max_tokens=4096, timeout=120)
        self.crewai = CrewAIAgent(
            role=f'{name} ({self.role})', goal='Perform your assigned role within the supplied contract.',
            backstory=self.instructions, llm=self.intelligence, allow_delegation=False,
            max_iter=30, max_retry_limit=2, verbose=True)

    def run(self, brief, expected, tools=(), progress=None):
        from crewai import Crew, Process, Task

        # A fresh executor gets only this assignment, not earlier contract conversations.
        agent = self.crewai.copy()
        agent.tools = list(tools)
        agent.step_callback = progress
        task = Task(description=brief, expected_output=expected, agent=agent)
        crew = Crew(agents=[agent], tasks=[task], process=Process.sequential,
                    memory=False, cache=False, verbose=True, tracing=False, share_crew=False)
        return crew.kickoff().raw
