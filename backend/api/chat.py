import json
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.core.agent_tools import (
    AgentToolCall,
    AgentToolError,
    AgentToolResult,
    consume_pending_approval,
    detect_command,
    detect_file_read,
    execute_tool_call,
    local_memories_context,
    maybe_store_memory,
    register_pending_approval,
)
from backend.core.agent_loop import AgentLoopOutcome, run_agent_loop
from backend.core.config import get_settings
from backend.core.llm import (
    ChatMessage,
    OllamaError,
    chat_with_ollama,
    list_ollama_models,
    stream_chat_with_ollama,
)
from backend.core.web_search import WebSource, build_web_augmented_messages, should_use_web


router = APIRouter(prefix="/api/chat", tags=["chat"])


class ToolCallRequest(BaseModel):
    id: str
    name: str
    arguments: dict[str, str] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    model: str | None = None
    temperature: Annotated[float | None, Field(ge=0, le=2)] = None
    max_tokens: Annotated[int | None, Field(ge=32, le=1024)] = None
    approved_tool_call: ToolCallRequest | None = None


class WebSourceResponse(BaseModel):
    title: str
    url: str
    truncated: bool


class ToolCallResponse(BaseModel):
    id: str
    name: str
    arguments: dict[str, str]
    requires_confirmation: bool


class ToolActivityResponse(BaseModel):
    name: str
    status: str
    summary: str
    requires_confirmation: bool = False
    call: ToolCallResponse | None = None


class ChatResponse(BaseModel):
    model: str
    message: ChatMessage
    web_sources: list[WebSourceResponse] = Field(default_factory=list)
    tools: list[ToolActivityResponse] = Field(default_factory=list)


class ModelsResponse(BaseModel):
    default_model: str
    models: list[str]
    available: bool


@router.get("/model")
def get_default_model() -> dict[str, str]:
    settings = get_settings()
    return {
        "model": settings.ollama_model,
        "ollama_base_url": settings.ollama_base_url,
    }


@router.get("/models", response_model=ModelsResponse)
def get_models() -> ModelsResponse:
    settings = get_settings()
    try:
        models = list_ollama_models()
    except OllamaError as exc:
        return ModelsResponse(
            default_model=settings.ollama_model,
            models=[settings.ollama_model],
            available=False,
        )

    if settings.ollama_model not in models:
        models.insert(0, settings.ollama_model)

    return ModelsResponse(default_model=settings.ollama_model, models=models, available=True)


@router.post("", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    messages = request.messages
    web_sources: list[WebSource] = []
    tool_results: list[AgentToolResult] = []

    latest_user = next((message.content for message in reversed(messages) if message.role == "user"), "")
    if memory_result := maybe_store_memory(latest_user):
        tool_results.append(memory_result)

    if request.approved_tool_call:
        try:
            approved_call = consume_pending_approval(request.approved_tool_call.id)
            tool_results.append(execute_tool_call(approved_call))
        except AgentToolError as exc:
            tool_results.append(
                AgentToolResult(
                    name=request.approved_tool_call.name,
                    status="error",
                    summary=str(exc),
                )
            )
    else:
        if file_call := detect_file_read(latest_user):
            try:
                tool_results.append(execute_tool_call(file_call))
            except AgentToolError as exc:
                tool_results.append(
                    AgentToolResult(name=file_call.name, status="error", summary=str(exc))
                )

        if command_call := detect_command(latest_user):
            command_call = register_pending_approval(command_call)
            return ChatResponse(
                model=request.model or get_settings().ollama_model,
                message=ChatMessage(
                    role="assistant",
                    content="I can run that local command, but I need your confirmation first.",
                ),
                tools=[
                    tool_result_to_response(
                        AgentToolResult(
                            name=command_call.name,
                            status="pending",
                            summary=f"Run local command: {command_call.arguments.get('command', '')}",
                            requires_confirmation=True,
                            call=command_call,
                        )
                    )
                ],
            )

    if should_use_web(messages):
        messages, web_sources = build_web_augmented_messages(messages)

    if not request.approved_tool_call and not tool_results and not web_sources:
        outcome = run_agent_loop(messages, request.model, request.temperature)
        if outcome.pending_call:
            return pending_tool_response(outcome.pending_call, request.model)
        tool_results.extend(outcome.tool_results)
        web_sources.extend(outcome.web_sources)

    messages = augment_messages_with_local_context(messages, tool_results)

    try:
        result = chat_with_ollama(
            messages=messages,
            model=request.model,
            temperature=request.temperature,
            num_predict=request.max_tokens,
        )
    except OllamaError as exc:
        if tool_results:
            return ChatResponse(
                model=request.model or get_settings().ollama_model,
                message=ChatMessage(role="assistant", content=tool_fallback_message(tool_results)),
                web_sources=[
                    WebSourceResponse(title=source.title, url=source.url, truncated=source.truncated)
                    for source in web_sources
                ],
                tools=[tool_result_to_response(tool_result) for tool_result in tool_results],
            )
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return ChatResponse(
        model=result.model,
        message=result.message,
        web_sources=[
            WebSourceResponse(title=source.title, url=source.url, truncated=source.truncated)
            for source in web_sources
        ],
        tools=[tool_result_to_response(tool_result) for tool_result in tool_results],
    )


@router.post("/stream")
def stream_chat(request: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        generate_chat_events(request),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def generate_chat_events(request: ChatRequest) -> Iterator[str]:
    messages = request.messages
    web_sources: list[WebSource] = []
    tool_results: list[AgentToolResult] = []
    latest_user = next((message.content for message in reversed(messages) if message.role == "user"), "")

    if memory_result := maybe_store_memory(latest_user):
        tool_results.append(memory_result)

    if request.approved_tool_call:
        try:
            approved_call = consume_pending_approval(request.approved_tool_call.id)
            tool_results.append(execute_tool_call(approved_call))
        except AgentToolError as exc:
            tool_results.append(
                AgentToolResult(
                    name=request.approved_tool_call.name,
                    status="error",
                    summary=str(exc),
                )
            )
    else:
        if file_call := detect_file_read(latest_user):
            try:
                tool_results.append(execute_tool_call(file_call))
            except AgentToolError as exc:
                tool_results.append(AgentToolResult(name=file_call.name, status="error", summary=str(exc)))

        if command_call := detect_command(latest_user):
            command_call = register_pending_approval(command_call)
            yield ndjson_event("token", content="I can run that local command, but I need your confirmation first.")
            yield ndjson_event("approval", tool=tool_call_to_response(command_call).model_dump())
            yield ndjson_event("done", model=request.model or get_settings().ollama_model)
            return

    if should_use_web(messages):
        messages, web_sources = build_web_augmented_messages(messages)

    if not request.approved_tool_call and not tool_results and not web_sources:
        outcome = run_agent_loop(messages, request.model, request.temperature)
        tool_results.extend(outcome.tool_results)
        web_sources.extend(outcome.web_sources)
        if outcome.pending_call:
            yield ndjson_event("token", content="I found a local action that can help, but I need your confirmation first.")
            yield ndjson_event("approval", tool=tool_call_to_response(outcome.pending_call).model_dump())
            yield ndjson_event("done", model=request.model or get_settings().ollama_model)
            return

    for tool_result in tool_results:
        yield ndjson_event("tool", tool=tool_result_to_response(tool_result).model_dump())
    if web_sources:
        yield ndjson_event(
            "sources",
            sources=[
                WebSourceResponse(title=source.title, url=source.url, truncated=source.truncated).model_dump()
                for source in dedupe_sources(web_sources)
            ],
        )

    messages = augment_messages_with_local_context(messages, tool_results)
    selected_model = request.model or get_settings().ollama_model
    yield ndjson_event("meta", model=selected_model)
    try:
        for token in stream_chat_with_ollama(
            messages=[message.model_dump() for message in messages],
            model=request.model,
            temperature=request.temperature,
            num_predict=request.max_tokens,
        ):
            yield ndjson_event("token", content=token)
    except OllamaError as exc:
        if tool_results:
            yield ndjson_event("token", content=tool_fallback_message(tool_results))
        else:
            yield ndjson_event("error", message=str(exc))
            return
    yield ndjson_event("done", model=selected_model)


def augment_messages_with_local_context(
    messages: list[ChatMessage],
    tool_results: list[AgentToolResult],
) -> list[ChatMessage]:
    context_parts: list[str] = []
    if memories := local_memories_context():
        context_parts.append(memories)
    completed_tools = [tool for tool in tool_results if tool.content]
    if completed_tools:
        context_parts.append(
            "The following local tools already ran. Report their actual results concisely; do not "
            "suggest commands or alternate steps for work that already completed.\n\n"
            + "\n\n".join(
                [
                    f"Local tool result: {tool.summary}\n{tool.content}"
                    for tool in completed_tools
                ]
            )
        )
    if not context_parts:
        return messages
    return [ChatMessage(role="system", content="\n\n".join(context_parts)), *messages]


def tool_result_to_response(tool_result: AgentToolResult) -> ToolActivityResponse:
    return ToolActivityResponse(
        name=tool_result.name,
        status=tool_result.status,
        summary=tool_result.summary,
        requires_confirmation=tool_result.requires_confirmation,
        call=tool_call_to_response(tool_result.call) if tool_result.call else None,
    )


def pending_tool_response(call: AgentToolCall, model: str | None) -> ChatResponse:
    pending_result = AgentToolResult(
        name=call.name,
        status="pending",
        summary=f"Awaiting confirmation: {call.arguments.get('command', call.name)}",
        requires_confirmation=True,
        call=call,
    )
    return ChatResponse(
        model=model or get_settings().ollama_model,
        message=ChatMessage(
            role="assistant",
            content="I found a local command that can help, but I need your confirmation first.",
        ),
        tools=[tool_result_to_response(pending_result)],
    )


def ndjson_event(event_type: str, **payload: object) -> str:
    return json.dumps({"type": event_type, **payload}, ensure_ascii=False) + "\n"


def dedupe_sources(sources: list[WebSource]) -> list[WebSource]:
    seen: set[str] = set()
    unique: list[WebSource] = []
    for source in sources:
        if source.url in seen:
            continue
        seen.add(source.url)
        unique.append(source)
    return unique


def tool_fallback_message(tool_results: list[AgentToolResult]) -> str:
    lines = [
        "The local model was unavailable after running local tools, so here is the raw tool result."
    ]
    for tool in tool_results:
        lines.extend(["", tool.summary])
        if tool.content:
            lines.append(tool.content[:4_000])
    return "\n".join(lines)


def tool_call_to_response(call: AgentToolCall) -> ToolCallResponse:
    return ToolCallResponse(
        id=call.id,
        name=call.name,
        arguments=call.arguments,
        requires_confirmation=call.requires_confirmation,
    )
