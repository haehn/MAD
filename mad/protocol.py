"""Shared model response schemas and JSON wire-format parsing."""
import json
import re


TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
        },
        "title": {
            "type": "string",
        },
        "description": {
            "type": "string",
        },
        "owner": {
            "type": "string",
        },
        "dependencies": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
        "acceptance": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
    },
    "required": [
        "id",
        "title",
        "description",
        "owner",
        "dependencies",
        "acceptance",
    ],
    "additionalProperties": False,
}


TASK_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": TASK_SCHEMA,
        },
    },
    "required": [
        "tasks",
    ],
    "additionalProperties": False,
}


MESSAGE_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "recipient": {
            "type": "string",
        },
        "type": {
            "type": "string",
            "enum": [
                "question",
                "answer",
                "status",
                "handoff",
                "review",
                "blocker",
                "discovery",
            ],
        },
        "content": {
            "type": "string",
        },
    },
    "required": [
        "recipient",
        "type",
        "content",
    ],
    "additionalProperties": False,
}


MESSAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "messages": {
            "type": "array",
            "items": MESSAGE_ITEM_SCHEMA,
        },
    },
    "required": [
        "messages",
    ],
    "additionalProperties": False,
}


TOOL_CALL_SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {
            "type": "string",
            "enum": [
                "list",
                "read",
                "write",
                "append",
                "run",
                "serve",
                "stop_server",
                "browser_check",
            ],
        },
        "path": {
            "type": "string",
        },
        "content": {
            "type": "string",
        },
        "command": {
            "type": "string",
        },
        "name": {
            "type": "string",
        },
        "wait_ms": {
            "type": "integer",
            "minimum": 0,
            "maximum": 30000,
        },
    },
    "required": [
        "tool",
    ],
    "additionalProperties": False,
}


TASK_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "state": {
            "type": "string",
            "enum": [
                "tool",
                "final",
            ],
        },
    },
    "required": [
        "state",
    ],
    "additionalProperties": False,
}


TOOL_STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "tool_calls": {
            "type": "array",
            "items": TOOL_CALL_SCHEMA,
        },
        "messages": {
            "type": "array",
            "items": MESSAGE_ITEM_SCHEMA,
        },
    },
    "required": [
        "tool_calls",
        "messages",
    ],
    "additionalProperties": False,
}


FINAL_STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": [
                "completed",
                "blocked",
            ],
        },
        "summary": {
            "type": "string",
        },
        "output": {
            "type": "string",
        },
        "messages": {
            "type": "array",
            "items": MESSAGE_ITEM_SCHEMA,
        },
    },
    "required": [
        "status",
        "summary",
        "output",
        "messages",
    ],
    "additionalProperties": False,
}


ASSESSMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "criterion": {"type": "string", "minLength": 1},
        "reason": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    },
    "required": ["criterion", "reason", "evidence"],
    "additionalProperties": False,
}


TASK_UPDATES_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "owner": {"type": "string"},
        "dependencies": {"type": "array", "items": {"type": "string"}},
        "acceptance": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}


# Each action has exactly its own required fields. A permissive shared object
# allowed models to emit task objects for ACCEPT while omitting task_id.
ACTION_FIELDS = {
    "accept": {
        "task_id": {"type": "string"},
        "assessments": {"type": "array", "items": ASSESSMENT_SCHEMA, "minItems": 1},
    },
    "add": {"task": TASK_SCHEMA},
    "revise": {"task_id": {"type": "string"}, "updates": TASK_UPDATES_SCHEMA},
    "reassign": {"task_id": {"type": "string"}, "owner": {"type": "string"}},
    "cancel": {"task_id": {"type": "string"}},
    "reopen": {"task_id": {"type": "string"}},
}

REPLAN_ACTION_SCHEMA = {
    "anyOf": [
        {
            "type": "object",
            "properties": {"type": {"type": "string", "enum": [kind]}, **fields},
            "required": ["type", *fields],
            "additionalProperties": False,
        }
        for kind, fields in ACTION_FIELDS.items()
    ],
}


REPLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "actions": {"type": "array", "items": REPLAN_ACTION_SCHEMA},
        "tool_calls": {"type": "array", "items": TOOL_CALL_SCHEMA},
    },
    "required": ["reason", "actions", "tool_calls"],
    "additionalProperties": False,
}


PROJECT_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        **REPLAN_SCHEMA["properties"],
        "accepted": {"type": "boolean"},
        "assessments": {"type": "array", "items": ASSESSMENT_SCHEMA},
    },
    "required": ["reason", "actions", "tool_calls", "accepted", "assessments"],
    "additionalProperties": False,
}


def parse_json_response(response):
    response = response.strip()

    response = re.sub(
        r"^```(?:json)?\s*",
        "",
        response,
        flags=re.IGNORECASE,
    )

    response = re.sub(
        r"\s*```$",
        "",
        response,
    )

    return json.loads(
        response
    )
