from fastapi import FastAPI

from backend.api.chat import router as chat_router
from backend.core.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.app_name)
app.include_router(chat_router)

@app.get("/health")
def health():
    return {
        "status": "StillGaze is running",
        "model": settings.ollama_model,
    }
