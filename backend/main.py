from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.api.chat import router as chat_router
from backend.api.chats import router as chats_router
from backend.core.config import get_settings
from backend.core.storage import init_db

settings = get_settings()
frontend_dir = Path(__file__).resolve().parent.parent / "frontend"

init_db()

app = FastAPI(title=settings.app_name)
app.include_router(chat_router)
app.include_router(chats_router)
app.mount("/static", StaticFiles(directory=frontend_dir), name="static")


@app.get("/")
def index():
    return FileResponse(frontend_dir / "index.html")


@app.get("/health")
def health():
    return {
        "status": "StillGaze is running",
        "model": settings.ollama_model,
    }
