import base64
import json
import logging
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from knowledge_archive.config import Settings
from knowledge_archive.models import LLMAnalysis
from knowledge_archive.text import normalize_tags

logger = logging.getLogger(__name__)


ANALYSIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "title",
        "summary",
        "why_interesting",
        "facts",
        "open_questions",
        "tags",
        "extracted_text",
    ],
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "why_interesting": {"type": "string"},
        "facts": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
        "extracted_text": {"type": "string"},
    },
}


SYSTEM_PROMPT = """You are an archival analyst.
Return only valid JSON matching the requested schema.
Be concise and factual. Use German unless the source content is clearly another language.
For images and videos, include OCR text when visible. For weak evidence, put uncertainty in open_questions."""


class LLMError(RuntimeError):
    pass


class OpenRouterClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            timeout=httpx.Timeout(90.0),
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": settings.public_base_url or "http://localhost",
                "X-Title": "knowledge-archive",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def analyze_text(self, text: str, urls: list[str]) -> LLMAnalysis:
        prompt = (
            "Analyze this Telegram text message for a personal knowledge archive.\n"
            f"Detected URLs: {urls}\n\n"
            f"Message:\n{text}"
        )
        return await self._chat_json(self.settings.openrouter_text_model, [{"type": "text", "text": prompt}])

    async def analyze_images(
        self,
        image_paths: list[Path],
        prompt: str,
        original_text: str | None = None,
    ) -> LLMAnalysis:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if original_text:
            content.append({"type": "text", "text": f"Telegram caption/text:\n{original_text}"})
        for path in image_paths:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _image_data_url(path)},
                }
            )
        return await self._chat_json(self.settings.openrouter_vision_model, content)

    async def analyze_document_metadata(
        self,
        filename: str,
        media_type: str | None,
        caption: str | None,
    ) -> LLMAnalysis:
        prompt = (
            "Create archive metadata for a document that was stored as an asset. "
            "Do not invent document contents if they are unavailable.\n"
            f"Filename: {filename}\nMedia type: {media_type}\nCaption: {caption or ''}"
        )
        return await self._chat_json(self.settings.openrouter_text_model, [{"type": "text", "text": prompt}])

    async def _chat_json(self, model: str, content: list[dict[str, Any]]) -> LLMAnalysis:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "archive_analysis",
                    "strict": True,
                    "schema": ANALYSIS_SCHEMA,
                },
            },
        }
        response = await self._client.post("/chat/completions", json=payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("OpenRouter request failed: %s", exc.response.text[:1000])
            raise LLMError(f"OpenRouter returned HTTP {exc.response.status_code}") from exc

        try:
            raw_content = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(raw_content)
            analysis = LLMAnalysis.model_validate(parsed)
            analysis.tags = normalize_tags(analysis.tags)
            return analysis
        except (KeyError, IndexError, json.JSONDecodeError, ValidationError) as exc:
            logger.exception("Invalid LLM response")
            raise LLMError("OpenRouter returned invalid analysis JSON") from exc


def _image_data_url(path: Path) -> str:
    suffix = path.suffix.lower()
    media_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix, "image/jpeg")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"

