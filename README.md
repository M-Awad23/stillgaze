# stillgaze

Localized AI workspace powered by FastAPI, Ollama, and SQLite.

## What it does

StillGaze is a local chat app for talking to Ollama models from a browser UI.

- Local Ollama chat through FastAPI
- Model dropdown populated from installed Ollama models
- Token-by-token response streaming with safe Markdown rendering
- SQLite-backed chat history
- Automatic web retrieval when prompts ask for current info, sources, or resources
- Bounded Qwen tool loop for local files, web retrieval, memory, and commands
- Confirmed CSV, JSON, and PDF edits inside the project folder
- Rename, pin, archive, delete, and export chats
- Light/dark frontend inspired by the signature-authenticator project
- Configurable generation temperature and response length

## Run StillGaze

Start Ollama first:

```powershell
ollama serve
```

If Ollama hangs while discovering GPU backends on Windows, start it CPU-only:

```powershell
$env:OLLAMA_LLM_LIBRARY="cpu"
$env:OLLAMA_VULKAN="0"
$env:CUDA_VISIBLE_DEVICES="-1"
$env:GGML_VK_VISIBLE_DEVICES="-1"
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" serve
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

Streaming chat responses are available as newline-delimited JSON:

```text
POST /api/chat/stream
```

Stream event types are `meta`, `token`, `tool`, `sources`, `approval`, `error`, and `done`.

Chat persistence routes:

```text
GET    /api/chats
POST   /api/chats
PATCH  /api/chats/{chat_id}
DELETE /api/chats/{chat_id}
GET    /api/chats/{chat_id}/messages
POST   /api/chats/{chat_id}/messages
```

Memory routes:

```text
GET    /api/memory
POST   /api/memory
DELETE /api/memory/{memory_id}
```

## Local agent tools

StillGaze can use a small local tool layer during chat:

- `file.read`: reads text-like files inside this project folder when a prompt asks to read, summarize, inspect, or analyze a path.
  - Text/source files are read directly.
  - CSV and JSON files are summarized with structure and preview content.
  - PDFs use local `pypdf` text extraction; scanned PDFs still need OCR.
- `file.write`: creates or edits local data files after explicit confirmation.
  - CSV supports `replace` and `append`.
  - JSON supports `replace` and top-level object `merge`.
  - PDF supports `replace` and appending generated text pages.
- `memory.write`: stores local preferences or facts when a prompt starts with phrases like `remember that ...`.
- `command.run`: proposes a local shell command when a prompt uses `run command: ...`, then waits for the browser confirmation before executing it.

File writes and command execution are one-time, expiry-limited, confirmation-gated actions. Commands run from the project folder. The agent loop is capped at three planning steps and three calls per step.
Saved memories can also be reviewed, added, and removed from the sidebar.

Web access:

StillGaze automatically searches and reads web sources when the latest prompt asks for current info, sources, resources, search, or a direct URL. Returned answers include a Sources section when web pages were retrieved.

Direct page-read route for debugging:

```text
POST   /api/web/read
```

Example:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/web/read `
  -ContentType "application/json" `
  -Body '{"url":"https://example.com"}'
```

## Local data

Chat history is stored locally in:

```text
data/stillgaze.sqlite3
```

The SQLite database is ignored by git so local conversations stay local.

## Configuration

- `OLLAMA_BASE_URL`: defaults to `http://127.0.0.1:11434`
- `OLLAMA_MODEL`: defaults to `qwen2.5:7b`
- `OLLAMA_NUM_GPU`: optional; set to `0` to force CPU inference through Ollama
- `OLLAMA_NUM_PREDICT`: defaults to `512`; length-limited responses automatically continue once
- `OLLAMA_TIMEOUT_SECONDS`: defaults to `120`

## Tests

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```
