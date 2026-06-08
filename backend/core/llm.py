from collections.abc import Iterable
import json
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from backend.core.config import get_settings


ChatRole = Literal["system", "user", "assistant"]


class ChatMessage(BaseModel):
    role: ChatRole
    content: str = Field(min_length=1)


class ChatResult(BaseModel):
    model: str
    message: ChatMessage
    done: bool


class OllamaError(RuntimeError):
    pass


def chat_with_ollama(
    messages: Iterable[ChatMessage],
    model: str | None = None,
    temperature: float | None = None,
    num_predict: int | None = None,
) -> ChatResult:
    settings = get_settings()
    selected_model = model or settings.ollama_model

    payload: dict[str, object] = {
        "model": selected_model,
        "messages": [message.model_dump() for message in messages],
        "stream": False,
    }
    options: dict[str, float | int] = {}
    if temperature is not None:
        options["temperature"] = temperature
    if settings.ollama_num_gpu is not None:
        options["num_gpu"] = settings.ollama_num_gpu
    options["num_predict"] = num_predict or settings.ollama_num_predict
    if options:
        payload["options"] = options

    request = Request(
        f"{settings.ollama_base_url}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=settings.ollama_timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise OllamaError(
            f"Ollama rejected the request with status {exc.code}: {detail}"
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise OllamaError(
            "Could not reach Ollama. Make sure Ollama is running and the model is pulled."
        ) from exc
    except json.JSONDecodeError as exc:
        raise OllamaError("Ollama returned an invalid JSON response.") from exc
    message = data.get("message")
    if not isinstance(message, dict) or not message.get("content"):
        raise OllamaError("Ollama returned an unexpected chat response.")

    return ChatResult(
        model=data.get("model", selected_model),
        message=ChatMessage(
            role=message.get("role", "assistant"),
            content=message["content"],
        ),
        done=bool(data.get("done", True)),
    )
