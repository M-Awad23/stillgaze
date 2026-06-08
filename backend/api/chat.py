from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.core.config import get_settings
from backend.core.llm import ChatMessage, OllamaError, chat_with_ollama


router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    model: str | None = None
    temperature: Annotated[float | None, Field(ge=0, le=2)] = None


class ChatResponse(BaseModel):
    model: str
    message: ChatMessage


@router.get("/model")
def get_default_model() -> dict[str, str]:
    settings = get_settings()
    return {
        "model": settings.ollama_model,
        "ollama_base_url": settings.ollama_base_url,
    }


@router.post("", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    try:
        result = chat_with_ollama(
            messages=request.messages,
            model=request.model,
            temperature=request.temperature,
        )
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return ChatResponse(model=result.model, message=result.message)
