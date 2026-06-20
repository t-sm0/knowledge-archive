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
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11, $12,
                    $13::jsonb, $14::vector
                )
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
                vector_literal(embedding),
            )

    async def search_items(
        self,
        query: str,
        limit: int = 8,
        query_embedding: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.pool:
            raise RuntimeError("Database pool is not connected")
        terms = [term for term in query.split() if len(term) >= 3][:8]
        results: list[dict[str, Any]] = []
        async with self.pool.acquire() as conn:
            if query_embedding:
                vector_rows = await conn.fetch(
                    """
                    SELECT id, item_type, created_at, title, summary, url, tags, assets,
                           markdown_path, original_text, metadata,
                           embedding <=> $1::vector AS distance
                    FROM archive_items
                    WHERE embedding IS NOT NULL
                    ORDER BY embedding <=> $1::vector
                    LIMIT $2
                    """,
                    vector_literal(query_embedding),
                    limit,
                )
                results.extend(dict(row) for row in vector_rows)
            if not terms:
                rows = await conn.fetch(
                    """
                    SELECT id, item_type, created_at, title, summary, url, tags, assets,
                           markdown_path, original_text, metadata
                    FROM archive_items
                    ORDER BY created_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
            else:
                patterns = [f"%{term}%" for term in terms]
                rows = await conn.fetch(
                    """
                    SELECT id, item_type, created_at, title, summary, url, tags, assets,
                           markdown_path, original_text, metadata
                    FROM archive_items
                    WHERE title ILIKE ANY($1::text[])
                       OR summary ILIKE ANY($1::text[])
                       OR original_text ILIKE ANY($1::text[])
                       OR array_to_string(tags, ' ') ILIKE ANY($1::text[])
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    patterns,
                    limit,
                )
        results.extend(dict(row) for row in rows)
        deduped: list[dict[str, Any]] = []
        seen: set[Any] = set()
        for row in results:
            row_id = row.get("id")
            if row_id in seen:
                continue
            seen.add(row_id)
            deduped.append(row)
            if len(deduped) >= limit:
                break
        return deduped


def vector_literal(vector: list[float] | None) -> str | None:
    if vector is None:
        return None
    return "[" + ",".join(f"{float(value):.8f}" for value in vector) + "]"
