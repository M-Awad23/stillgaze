# stillgaze

Localized AI workspace powered by FastAPI, Ollama, and SQLite.

## What it does

StillGaze is a local chat app for talking to Ollama models from a browser UI.

- Local Ollama chat through FastAPI
- Model dropdown populated from installed Ollama models
- SQLite-backed chat history
- Rename, pin, archive, delete, and export chats
- Light/dark frontend inspired by the signature-authenticator project
- Configurable generation temperature and response length

## Run StillGaze

Start Ollama first:

```powershell
ollama serve
```

In another terminal, from this project folder:

```powershell
.\venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

Open:

```text
http://127.0.0.1:8000/
```

API docs are available at:

```text
http://127.0.0.1:8000/docs
```

## Add models

Install models with Ollama, then refresh StillGaze.

Good daily model:

```powershell
ollama pull qwen2.5:7b
```

Higher-performance-machine option:

```powershell
ollama pull mistral-small:22b
```

See installed models:

```powershell
ollama list
```

## Chat API

Send a direct chat request:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/chat `
  -ContentType "application/json" `
  -Body '{"model":"qwen2.5:7b","messages":[{"role":"user","content":"Hello from StillGaze"}],"max_tokens":160}'
```

Chat persistence routes:

```text
GET    /api/chats
POST   /api/chats
PATCH  /api/chats/{chat_id}
DELETE /api/chats/{chat_id}
GET    /api/chats/{chat_id}/messages
POST   /api/chats/{chat_id}/messages
```

## Local data

Chat history is stored locally in:

```text
data/stillgaze.sqlite3
```

The SQLite database is ignored by git so local conversations stay local.

## Configuration

- `OLLAMA_BASE_URL`: defaults to `http://127.0.0.1:11434`
- `OLLAMA_MODEL`: defaults to `llama2:13b`
- `OLLAMA_NUM_GPU`: optional; set to `0` to force CPU inference through Ollama
- `OLLAMA_NUM_PREDICT`: defaults to `160` to keep local CPU generations shorter
- `OLLAMA_TIMEOUT_SECONDS`: defaults to `120`
