"""Internal task reports, separate from workspace deliverables."""
from pathlib import Path
import re
import json


class AuditStore:
    def __init__(self, root):
        self.root = Path(root)
        self.directory = self.root / ".mad" / "audit"
        self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_name(value):
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)

    def write_task_result(self, task, agent_name, status, summary, output):
        task_id = self._safe_name(task.id)
        path = self.directory / f'{task_id}.md'
        content = [f'# {task.id}: {task.title}', '', f'**Agent:** {agent_name}', f'**Status:** {status}', '']
        if summary:
            content.extend(['## Summary', '', summary, ''])
        if output:
            content.extend(['## Output', '', output, ''])
        path.write_text('\n'.join(content), encoding='utf-8')
        return path.relative_to(self.root)

    def write_review(self, name, response, evidence, accepted, error=None):
        path = self.directory / f"{self._safe_name(name)}-review.json"
        record = {
            'response': response, 'evidence': evidence,
            'accepted': accepted, 'error': str(error) if error else None,
        }
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding='utf-8')
        with path.with_suffix('.jsonl').open('a', encoding='utf-8') as history:
            history.write(json.dumps(record, ensure_ascii=False) + '\n')
        return path.relative_to(self.root)
