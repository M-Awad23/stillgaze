from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.core.config import get_settings
from backend.core.llm import ChatMessage, OllamaError, chat_with_ollama, list_ollama_models


router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    model: str | None = None
    temperature: Annotated[float | None, Field(ge=0, le=2)] = None
    max_tokens: Annotated[int | None, Field(ge=32, le=1024)] = None


class ChatResponse(BaseModel):
    model: str
    message: ChatMessage


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
    try:
        result = chat_with_ollama(
            messages=request.messages,
            model=request.model,
            temperature=request.temperature,
            num_predict=request.max_tokens,
        )
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return ChatResponse(model=result.model, message=result.message)
