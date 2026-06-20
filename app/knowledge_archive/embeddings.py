from __future__ import annotations

import logging
from collections.abc import Sequence

import httpx

from knowledge_archive.config import Settings

logger = logging.getLogger(__name__)


class EmbeddingService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            timeout=httpx.Timeout(60.0),
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": settings.public_base_url or "http://localhost",
                "X-Title": "knowledge-archive",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def embed(self, texts: Sequence[str]) -> list[list[float]] | None:
        if not self.settings.embeddings_enabled or not texts:
            return None
        payload = {
            "model": self.settings.openrouter_embedding_model,
            "input": list(texts),
            "dimensions": self.settings.openrouter_embedding_dimensions,
        }
        response = await self._client.post("/embeddings", json=payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("Embedding request failed: %s", exc.response.text[:1000])
            return None
        data = response.json().get("data", [])
        vectors = [item.get("embedding") for item in data if isinstance(item, dict)]
        if len(vectors) != len(texts) or not all(isinstance(vector, list) for vector in vectors):
            logger.warning("Embedding response shape did not match request")
            return None
        return vectors

