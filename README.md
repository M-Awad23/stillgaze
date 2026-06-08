# stillgaze
Localized AI 

## Ollama chat backend

StillGaze talks to a local Ollama server through FastAPI. The default model is
`llama2:13b`, and you can override it with `OLLAMA_MODEL`.

```powershell
ollama pull llama2:13b
ollama serve
```

In another terminal:

```powershell
uvicorn backend.main:app --reload
```

Send a chat request:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/chat `
  -ContentType "application/json" `
  -Body '{"messages":[{"role":"user","content":"Hello from StillGaze"}]}'
```

Configuration:

- `OLLAMA_BASE_URL`: defaults to `http://localhost:11434`
- `OLLAMA_MODEL`: defaults to `llama2:13b`
- `OLLAMA_NUM_GPU`: optional; set to `0` to force CPU inference through Ollama
- `OLLAMA_TIMEOUT_SECONDS`: defaults to `120`
