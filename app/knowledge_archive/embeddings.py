from collections.abc import Sequence


class EmbeddingService:
    """Placeholder boundary for future local bge-m3 or remote embedding support."""

    async def embed(self, _texts: Sequence[str]) -> list[list[float]] | None:
        # TODO: Add bge-m3 embedding generation and persist vector dimensions consistently.
        return None

