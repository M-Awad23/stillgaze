from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from backend.core import storage
from backend.core.agent_tools import (
    AgentToolCall,
    AgentToolError,
    AgentToolResult,
    build_call,
    execute_tool_call,
    register_pending_approval,
)
from backend.core.llm import ChatMessage, OllamaError, chat_with_ollama_tools
from backend.core.web_search import WebSource, collect_web_sources


MAX_AGENT_STEPS = 3
MAX_PLANNED_CALLS_PER_STEP = 3
TOOL_INTENT_RE = re.compile(
    r"\b(read|open|inspect|analyze|file|csv|json|pdf|remember|memory|search|web|"
    r"online|latest|current|recent|source|resource|run|execute|command|script|"
    r"edit|write|update|append|replace|merge)\b",
    re.IGNORECASE,
)

OLLAMA_TOOLS: list[dict[str, object]] = [
    {
        "type": "function",
        "function": {
            "name": "file_read",
            "description": "Read or summarize a file inside the StillGaze project folder.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_write",
            "description": "Save a durable user preference or fact in local memory.",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string"}},
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_write",
            "description": (
                "Create or edit a CSV, JSON, or PDF file inside the StillGaze project folder. "
                "CSV supports replace/append, JSON supports replace/merge, PDF supports replace/append. "
                "This always requires user confirmation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "mode": {
                        "type": "string",
                        "enum": ["replace", "append", "merge"],
                    },
                },
                "required": ["path", "content", "mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information and source pages.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "command_run",
            "description": "Run a local shell command. This always requires user confirmation.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
]

TOOL_NAME_MAP = {
    "file_read": "file.read",
    "file_write": "file.write",
    "memory_write": "memory.write",
    "web_search": "web.search",
    "command_run": "command.run",
}


@dataclass
class AgentLoopOutcome:
    tool_results: list[AgentToolResult] = field(default_factory=list)
    web_sources: list[WebSource] = field(default_factory=list)
    pending_call: AgentToolCall | None = None


def should_run_agent_loop(messages: list[ChatMessage]) -> bool:
    latest_user = next((message.content for message in reversed(messages) if message.role == "user"), "")
    return bool(TOOL_INTENT_RE.search(latest_user))


def run_agent_loop(
    messages: list[ChatMessage],
    model: str | None,
    temperature: float | None,
) -> AgentLoopOutcome:
    outcome = AgentLoopOutcome()
    if not should_run_agent_loop(messages):
        return outcome

    raw_messages: list[dict[str, object]] = [
        {
            "role": "system",
            "content": (
                "You are StillGaze's local tool planner. Use tools only when they materially help "
                "answer the latest request. Never claim a tool ran unless you call it. Commands "
                "always require confirmation. Stop calling tools once enough evidence is available."
            ),
        },
        *[message.model_dump() for message in messages[-10:]],
    ]

    for _ in range(MAX_AGENT_STEPS):
        try:
            planner_message = chat_with_ollama_tools(
                messages=raw_messages,
                tools=OLLAMA_TOOLS,
                model=model,
                temperature=temperature,
            )
        except OllamaError:
            break

        calls = parse_tool_calls(planner_message)
        if not calls:
            break

        raw_messages.append(planner_message)
        for call in calls[:MAX_PLANNED_CALLS_PER_STEP]:
            if call.requires_confirmation:
                outcome.pending_call = register_pending_approval(call)
                return outcome

            result, sources = execute_planned_call(call)
            outcome.tool_results.append(result)
            outcome.web_sources.extend(sources)
            raw_messages.append(
                {
                    "role": "tool",
                    "tool_name": call.name.replace(".", "_"),
                    "content": result.content or result.summary,
                }
            )

    return outcome


def parse_tool_calls(message: dict[str, object]) -> list[AgentToolCall]:
    raw_calls = message.get("tool_calls")
    if not isinstance(raw_calls, list):
        return []

    calls: list[AgentToolCall] = []
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict):
            continue
        function = raw_call.get("function")
        if not isinstance(function, dict):
            continue
        raw_name = function.get("name")
        name = TOOL_NAME_MAP.get(raw_name) if isinstance(raw_name, str) else None
        if not name:
            continue
        arguments = normalize_arguments(function.get("arguments"))
        string_arguments = {key: str(value) for key, value in arguments.items()}
        required_keys = {
            "file.read": ("path",),
            "file.write": ("path", "content", "mode"),
            "memory.write": ("content",),
            "web.search": ("query",),
            "command.run": ("command",),
        }[name]
        if any(not string_arguments.get(key, "").strip() for key in required_keys):
            continue
        calls.append(
            build_call(
                name,
                string_arguments,
                requires_confirmation=name in {"command.run", "file.write"},
            )
        )
    return calls


def normalize_arguments(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def execute_planned_call(call: AgentToolCall) -> tuple[AgentToolResult, list[WebSource]]:
    try:
        if call.name == "file.read":
            return execute_tool_call(call), []
        if call.name == "memory.write":
            content = call.arguments["content"].strip()[:1_000]
            memory = storage.create_memory(content=content, source="agent")
            return (
                AgentToolResult(
                    name=call.name,
                    status="completed",
                    summary=f"Saved memory: {memory['content']}",
                    content=memory["content"],
                ),
                [],
            )
        if call.name == "web.search":
            query = call.arguments["query"].strip()
            sources = collect_web_sources(query)
            content = "\n\n".join(
                f"{source.title}\nURL: {source.url}\n{source.content}" for source in sources
            )
            return (
                AgentToolResult(
                    name=call.name,
                    status="completed" if sources else "error",
                    summary=(
                        f"Retrieved {len(sources)} web source{'s' if len(sources) != 1 else ''} for: {query}"
                        if sources
                        else f"No web sources found for: {query}"
                    ),
                    content=content,
                ),
                sources,
            )
    except (AgentToolError, KeyError) as exc:
        return AgentToolResult(name=call.name, status="error", summary=str(exc)), []

    return AgentToolResult(name=call.name, status="error", summary="Unsupported tool call."), []
