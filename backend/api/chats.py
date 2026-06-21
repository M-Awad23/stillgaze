from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from backend.core import storage


router = APIRouter(prefix="/api/chats", tags=["chats"])


class MessageOut(BaseModel):
    id: str
    chat_id: str
    role: Literal["system", "user", "assistant"]
    content: str
    tools: list[dict[str, object]] = Field(default_factory=list)
    sources: list[dict[str, object]] = Field(default_factory=list)
    created_at: str


class ChatOut(BaseModel):
    id: str
    title: str
    pinned: bool
    archived: bool
    manual_title: bool
    created_at: str
    updated_at: str
    messages: list[MessageOut] = Field(default_factory=list)


class CreateChatRequest(BaseModel):
    title: str = Field(default="New chat", min_length=1, max_length=80)


class UpdateChatRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=80)
    pinned: bool | None = None
    archived: bool | None = None
    manual_title: bool | None = None


class CreateMessageRequest(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)
    tools: list[dict[str, object]] = Field(default_factory=list)
    sources: list[dict[str, object]] = Field(default_factory=list)


def hydrate_chat(chat: dict) -> ChatOut:
    return ChatOut(**chat, messages=storage.list_messages(chat["id"]))


@router.get("", response_model=list[ChatOut])
def list_chats() -> list[ChatOut]:
    return [hydrate_chat(chat) for chat in storage.list_chats()]


@router.post("", response_model=ChatOut, status_code=status.HTTP_201_CREATED)
def create_chat(request: CreateChatRequest) -> ChatOut:
    chat = storage.create_chat(title=request.title)
    return hydrate_chat(chat)


@router.patch("/{chat_id}", response_model=ChatOut)
def update_chat(chat_id: str, request: UpdateChatRequest) -> ChatOut:
    chat = storage.update_chat(
        chat_id,
        title=request.title,
        pinned=request.pinned,
        archived=request.archived,
        manual_title=request.manual_title,
    )
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return hydrate_chat(chat)


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chat(chat_id: str) -> None:
    if not storage.delete_chat(chat_id):
        raise HTTPException(status_code=404, detail="Chat not found")


@router.get("/{chat_id}/messages", response_model=list[MessageOut])
def list_messages(chat_id: str) -> list[MessageOut]:
    if storage.get_chat(chat_id) is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return [MessageOut(**message) for message in storage.list_messages(chat_id)]


@router.post("/{chat_id}/messages", response_model=MessageOut, status_code=status.HTTP_201_CREATED)
def create_message(chat_id: str, request: CreateMessageRequest) -> MessageOut:
    message = storage.create_message(
        chat_id,
        request.role,
        request.content,
        tools=request.tools,
        sources=request.sources,
    )
    if message is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return MessageOut(**message)
