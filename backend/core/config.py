import os
from functools import lru_cache

from pydantic import BaseModel, Field


class Settings(BaseModel):
    app_name: str = "StillGaze API"
    ollama_base_url: str = Field(default="http://127.0.0.1:11434")
    ollama_model: str = Field(default="llama2:13b")
    ollama_num_gpu: int | None = None
    ollama_num_predict: int = Field(default=160)
    ollama_timeout_seconds: int = Field(default=120)


@lru_cache
def get_settings() -> Settings:
    return Settings(
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
        ollama_model=os.getenv("OLLAMA_MODEL", "llama2:13b"),
        ollama_num_gpu=_get_optional_int("OLLAMA_NUM_GPU"),
        ollama_num_predict=int(os.getenv("OLLAMA_NUM_PREDICT", "160")),
        ollama_timeout_seconds=int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "120")),
    )


def _get_optional_int(name: str) -> int | None:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    return int(value)
