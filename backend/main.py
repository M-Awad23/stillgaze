from fastapi import FastAPI

app = FastAPI(title="StillGaze API")

@app.get("/health")
def health():
    return {"status": "StillGaze is running"}