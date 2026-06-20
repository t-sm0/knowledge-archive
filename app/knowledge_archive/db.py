import json
from typing import Any

import asyncpg

from knowledge_archive.models import ArchiveItem


class Database:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=5)

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def insert_item(self, item: ArchiveItem, embedding: list[float] | None = None) -> None:
        if not self.pool:
            raise RuntimeError("Database pool is not connected")
        assets = [asset.model_dump(mode="json") for asset in item.assets]
        metadata: dict[str, Any] = {
            **item.metadata,
            "why_interesting": item.analysis.why_interesting,
            "facts": item.analysis.facts,
            "open_questions": item.analysis.open_questions,
            "extracted_text": item.analysis.extracted_text,
        }
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO archive_items (
                    id, item_type, source, created_at, title, summary, url, tags, assets,
                    model, markdown_path, original_text, metadata, embedding
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11, $12, $13::jsonb, $14)
                """,
                item.id,
                item.item_type,
                item.source,
                item.created_at,
                item.title,
                item.analysis.summary,
                item.url,
                item.tags,
                json.dumps(assets, ensure_ascii=False),
                item.model,
                str(item.markdown_path),
                item.original_text,
                json.dumps(metadata, ensure_ascii=False),
                embedding,
            )

