from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


ArchiveType = Literal["text", "photo", "document", "video", "instagram"]


class LLMAnalysis(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    summary: str = Field(min_length=1)
    why_interesting: str = ""
    facts: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    extracted_text: str = ""


class Asset(BaseModel):
    path: str
    media_type: str | None = None
    role: str = "original"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArchiveItem(BaseModel):
    id: UUID
    item_type: ArchiveType
    source: str
    created_at: datetime
    title: str
    url: str | None = None
    tags: list[str] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    model: str
    markdown_path: Path
    original_text: str | None = None
    analysis: LLMAnalysis
    metadata: dict[str, Any] = Field(default_factory=dict)
