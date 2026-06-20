import base64
import json
import logging
import re
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

    async def analyze_text(self, text: str, urls: list[str], model: str | None = None) -> LLMAnalysis:
        prompt = (
            "Analyze this Telegram text message for a personal knowledge archive.\n"
            f"Detected URLs: {urls}\n\n"
            f"Message:\n{text}"
        )
        return await self._chat_json(
            model or self.settings.openrouter_text_model,
            [{"type": "text", "text": prompt}],
        )

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
        except (KeyError, IndexError) as exc:
            logger.exception("Invalid LLM response")
            raise LLMError("OpenRouter returned invalid analysis JSON") from exc

        try:
            return _validate_analysis(raw_content)
        except (json.JSONDecodeError, TypeError, ValidationError) as exc:
            if model == self.settings.openrouter_text_model:
                logger.exception("Invalid LLM response from JSON repair model")
                raise LLMError("OpenRouter returned invalid analysis JSON") from exc
            logger.warning(
                "Model %s returned non-conforming JSON; repairing with %s",
                model,
                self.settings.openrouter_text_model,
            )
            return await self._repair_json(raw_content, model)

    async def _repair_json(self, raw_content: Any, source_model: str) -> LLMAnalysis:
        repair_prompt = (
            "Convert this model response into valid JSON matching the archive_analysis schema. "
            "Preserve useful facts, OCR text, tags, open questions and uncertainty. "
            "Do not add fields outside the schema.\n\n"
            f"Source model: {source_model}\n"
            f"Raw response:\n{_stringify_raw(raw_content)}"
        )
        payload = {
            "model": self.settings.openrouter_text_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": [{"type": "text", "text": repair_prompt}]},
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
            logger.warning("OpenRouter JSON repair failed: %s", exc.response.text[:1000])
            raise LLMError(f"OpenRouter JSON repair returned HTTP {exc.response.status_code}") from exc
        try:
            repaired_content = response.json()["choices"][0]["message"]["content"]
            return _validate_analysis(repaired_content)
        except (KeyError, IndexError, json.JSONDecodeError, TypeError, ValidationError) as exc:
            logger.exception("Invalid repaired LLM response")
            raise LLMError("OpenRouter JSON repair returned invalid analysis JSON") from exc


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


def _validate_analysis(raw_content: Any) -> LLMAnalysis:
    parsed = _loads_model_json(raw_content)
    if isinstance(parsed, dict):
        if not str(parsed.get("title") or "").strip():
            parsed["title"] = "Untitled archive item"
        if not str(parsed.get("summary") or "").strip():
            parsed["summary"] = "No summary provided."
    analysis = LLMAnalysis.model_validate(parsed)
    analysis.tags = normalize_tags(analysis.tags)
    return analysis


def _loads_model_json(raw_content: Any) -> Any:
    if not isinstance(raw_content, str):
        raise TypeError("LLM content is not a string")
    text = raw_content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    return json.loads(text)


def _stringify_raw(raw_content: Any) -> str:
    if isinstance(raw_content, str):
        return raw_content[:12000]
    return json.dumps(raw_content, ensure_ascii=False, default=str)[:12000]
