from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from backend.core import storage


router = APIRouter(prefix="/api/memory", tags=["memory"])


class MemoryOut(BaseModel):
    id: str
    content: str
    source: str
    created_at: str
    updated_at: str


class CreateMemoryRequest(BaseModel):
    content: str = Field(min_length=1, max_length=1000)
    source: str = Field(default="user", min_length=1, max_length=40)


@router.get("", response_model=list[MemoryOut])
def list_memories() -> list[MemoryOut]:
    return [MemoryOut(**memory) for memory in storage.list_memories()]


@router.post("", response_model=MemoryOut, status_code=status.HTTP_201_CREATED)
def create_memory(request: CreateMemoryRequest) -> MemoryOut:
    return MemoryOut(**storage.create_memory(request.content, request.source))


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(memory_id: str) -> None:
    if not storage.delete_memory(memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")
