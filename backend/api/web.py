from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.core.web_reader import WebReadError, read_web_page


router = APIRouter(prefix="/api/web", tags=["web"])


class ReadWebRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class ReadWebResponse(BaseModel):
    url: str
    title: str
    content: str
    truncated: bool
    source_characters: int


@router.post("/read", response_model=ReadWebResponse)
def read_url(request: ReadWebRequest) -> ReadWebResponse:
    try:
        return ReadWebResponse(**read_web_page(request.url))
    except WebReadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
