"""Collect recorded evidence and submit work for domain-independent review."""
import asyncio
import json
from mad.message import Message


class ReviewService:
    def __init__(self, state, workspace, hub, supervisor, reporter, pending_reviews):
        self.state = state
        self.workspace = workspace
        self.audit = workspace.audit_store
        self.hub = hub
        self.supervisor = supervisor
        self.reporter = reporter
        self.pending_reviews = pending_reviews

    async def context(self, evidence):
        try:
            listing = await asyncio.to_thread(self.workspace.list_files, '.')
            evidence['workspace'] = {
                'id': 'workspace', 'source': 'runtime workspace listing',
                'success': True, 'result': listing,
            }
        except Exception as error:
            evidence['workspace'] = {
                'id': 'workspace', 'source': 'runtime workspace listing',
                'success': False, 'result': str(error),
            }
        # Keep full records in audit/state; mark prompt excerpts explicitly.
        preview = {}
        remaining = 60000
        for key, record in evidence.items():
            text = json.dumps(record, ensure_ascii=False)
            if len(text) > 6000:
                text = text[:6000] + " [excerpt; inspect the original artifact for more]"
            if remaining < len(text):
                preview['_omitted'] = 'More records exist in audit; inspect current deliverables with tools.'
                break
            preview[key] = record if len(json.dumps(record, ensure_ascii=False)) <= 6000 else {'id': key, 'excerpt': text}
            remaining -= len(text)
        return json.dumps(preview, indent=2, ensure_ascii=False)

    async def submit(self, message):
        task = self.state.record_result(message.task_id, message.sender,
                                        message.metadata.get('status', 'blocked'))
        output = message.metadata.get('output', '')
        summary = message.content or ''
        evidence = {
            'submission': {'id': 'submission', 'source': 'worker-authored submission',
                           'summary': summary, 'output': output},
        }
        for record in message.metadata.get('tool_records', []):
            evidence[record['id']] = record
        content = await self.context(evidence)
        self.state.evidence[task.id] = evidence
        artifact = self.audit.write_task_result(task, message.sender, task.status, summary, output)
        self.audit.write_review(task.id, {'status': 'awaiting Supervisor review'}, evidence, False)
        self.reporter.task_result(task, message.sender, message.metadata.get('elapsed'),
                                  artifact, summary, output)
        self.pending_reviews.add(task.id)
        await self.hub.send(Message(
            sender=message.sender, recipient=self.supervisor.name, type='REPLAN_REQUEST',
            task_id=task.id,
            content=f'TASK: {task.id} — {task.title}\n'
                    f'ACCEPTANCE CRITERIA: {json.dumps(task.review_criteria)}\n'
                    f'RECORDED EVIDENCE (IDs are object keys):\n{content}',
            metadata={'event_type': 'task_review' if task.status == 'review' else 'blocker',
                      'status': task.status, 'artifact': str(artifact),
                      'evidence_ids': list(evidence)},
        ))

    async def project_context(self):
        evidence = {}
        for task in self.state.tasks:
            for key, record in self.state.evidence.get(task.id, {}).items():
                evidence[f'{task.id}:{key}'] = {**record, 'id': f'{task.id}:{key}'}
        return evidence, await self.context(evidence)
