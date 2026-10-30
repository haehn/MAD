"""Supervisor planning and evidence-based review model protocol."""
import json
import copy
import time
from mad.message import Message
from mad.protocol import TASK_PLAN_SCHEMA, REPLAN_SCHEMA, PROJECT_REVIEW_SCHEMA
from mad.tool_session import ToolSession


class SupervisorProtocol:
    def __init__(self, agent, project, hub, model, reporter, workspace):
        self.agent = agent
        self.project = project
        self.hub = hub
        self.model = model
        self.reporter = reporter
        self.workspace = workspace

    async def plan(self, message):
        start = time.perf_counter()
        self.reporter.print(
            f"[bright_magenta]▶[/bright_magenta] [bold]{self.agent.name}[/bold] "
            "[dim]creating initial task graph...[/dim]"
        )
        agents = "\n".join(f"- {name}" for name in message.metadata['agents'])
        result, _ = await self.model.structured_chat([
            {'role': 'system', 'content': self.agent.build_system_prompt(self.project)},
            {'role': 'user', 'content': f"Create an initial task graph for the project.\n\nAvailable agents:\n\n{agents}"},
        ], schema=TASK_PLAN_SCHEMA, max_tokens=8192)
        elapsed = time.perf_counter() - start
        self.reporter.print(
            f"[bright_magenta]✓[/bright_magenta] [bold]{self.agent.name}[/bold] "
            f"[dim]created task graph in {elapsed:.1f}s[/dim]"
        )
        await self.hub.send(Message(
            sender=self.agent.name, recipient='__runtime__', type='PLAN_RESULT',
            content=json.dumps(result), metadata={'elapsed': elapsed},
        ))

    async def review(self, message):
        start = time.perf_counter()
        final_review = message.type == 'PROJECT_REVIEW_REQUEST'
        criteria = (self.project.success or [self.project.objective]) if final_review else []
        self.reporter.print(f'[bright_magenta]◆[/bright_magenta] {self.agent.name} reviewing '
                            f'{"final project" if final_review else message.task_id}')
        prompt = f"""Review event: {message.metadata.get('event_type', 'review')}
Related task: {message.task_id or '(final project review)'}
Current task graph:
{json.dumps([task.to_dict() for task in self.project.tasks], indent=2)}

Recorded evidence:
{message.content}

FINAL PROJECT REVIEW: {final_review}
Project criteria: {json.dumps(criteria)}
"""
        conversation = [
            {'role': 'system', 'content': self.agent.build_system_prompt(self.project)},
            {'role': 'user', 'content': prompt},
        ]
        prefix = f"review_retry{message.metadata['review_attempt']}" if message.metadata.get('review_attempt') else 'review'
        tools = ToolSession(self.workspace, self.reporter, prefix=prefix, allowed_tools={
            'list', 'read', 'run', 'serve', 'stop_server', 'browser_check',
        })
        for _ in range(20):
            schema = self._review_schema(message, final_review, tools.records)
            result, raw = await self.model.structured_chat(conversation, schema=schema,
                                                       max_tokens=8192 if final_review else 4096)
            calls = result.get('tool_calls', [])
            if not calls:
                break
            outcomes = await tools.execute(calls)
            conversation.extend([
                {'role': 'assistant', 'content': raw},
                {'role': 'user', 'content': 'RECORDED SUPERVISOR TOOL EVIDENCE:\n' +
                 json.dumps(tools.records[-len(outcomes):], indent=2) +
                 '\nInspect further or return your evidence-grounded decision with tool_calls=[].'},
            ])
        else:
            raise RuntimeError('Supervisor review exceeded 20 tool steps without a decision.')
        self.reporter.print(f'[bright_magenta]✓[/bright_magenta] {self.agent.name} '
                            f'reviewed evidence in {time.perf_counter() - start:.1f}s')
        await self.hub.send(Message(
            sender=self.agent.name, recipient='__runtime__',
            type='PROJECT_REVIEW_RESULT' if final_review else 'REPLAN_RESULT',
            task_id=message.task_id, content=json.dumps(result),
            metadata={'review_evidence': tools.records},
        ))

    def _review_schema(self, message, final_review, records):
        """Constrain generated references to the criteria/evidence actually supplied."""
        schema = copy.deepcopy(PROJECT_REVIEW_SCHEMA if final_review else REPLAN_SCHEMA)
        evidence_ids = [*message.metadata.get('evidence_ids', []), *[r['id'] for r in records]]
        related = next((task for task in self.project.tasks if task.id == message.task_id), None)
        variants = schema['properties']['actions']['items']['anyOf']
        for variant in list(variants):
            if variant['properties']['type']['enum'] != ['accept']:
                continue
            if final_review or related is None or related.status != 'review':
                variants.remove(variant)
                continue
            variant['properties']['task_id']['enum'] = [related.id]
            self._constrain_assessments(variant['properties']['assessments'],
                                        related.review_criteria, evidence_ids)
        if final_review:
            # Rejected final reviews may have no assessments; approval is checked by state.
            self._constrain_assessments(schema['properties']['assessments'],
                                        self.project.success or [self.project.objective],
                                        evidence_ids, require_all=False)
        return schema

    @staticmethod
    def _constrain_assessments(field, criteria, evidence_ids, require_all=True):
        if require_all:
            field['minItems'] = field['maxItems'] = len(set(criteria))
        field['items']['properties']['criterion']['enum'] = list(dict.fromkeys(criteria))
        if evidence_ids:
            field['items']['properties']['evidence']['items']['enum'] = list(dict.fromkeys(evidence_ids))
