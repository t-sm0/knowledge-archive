from __future__ import annotations

import asyncio
import logging
import mimetypes
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from knowledge_archive.config import settings
from knowledge_archive.db import Database
from knowledge_archive.embeddings import EmbeddingService
from knowledge_archive.llm import OpenRouterClient
from knowledge_archive.models import ArchiveItem, Asset
from knowledge_archive.storage import Storage
from knowledge_archive.text import extract_urls
from knowledge_archive.video import extract_frames

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

router = Router()
storage = Storage(settings.data_dir)
db = Database(settings.database_url)
llm = OpenRouterClient(settings)
embeddings = EmbeddingService()


def allowed(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id == settings.telegram_allowed_user_id)


def require_allowed(
    handler: Callable[[Message], Awaitable[None]],
) -> Callable[[Message], Awaitable[None]]:
    async def wrapped(message: Message) -> None:
        if not allowed(message):
            logger.warning("Rejected message from unauthorized user_id=%s", message.from_user.id if message.from_user else None)
            return
        try:
            await handler(message)
        except Exception:
            logger.exception("Ingest failed")
            try:
                await message.answer("Ingest fehlgeschlagen. Details stehen im Bot-Log.")
            except TelegramAPIError:
                logger.exception("Could not send Telegram error response")

    return wrapped


@router.message(F.text)
@require_allowed
async def handle_text(message: Message) -> None:
    text = message.text or ""
    urls = extract_urls(text)
    analysis = await llm.analyze_text(text, urls)
    await persist_and_confirm(
        message=message,
        item_type="text",
        source="telegram",
        model=settings.openrouter_text_model,
        analysis=analysis,
        original_text=text,
        url=urls[0] if urls else None,
        assets=[],
        metadata={"urls": urls, "telegram_message_id": message.message_id},
    )


@router.message(F.photo)
@require_allowed
async def handle_photo(message: Message) -> None:
    archive_id = uuid4()
    photo = max(message.photo or [], key=lambda item: (item.width or 0) * (item.height or 0))
    temp_path = storage.temp_path(f"{archive_id}-telegram-photo.jpg")
    await download_telegram_file(message.bot, photo.file_id, temp_path)
    asset = storage.copy_asset(temp_path, archive_id, "photo.jpg", "image/jpeg")
    image_path = settings.data_dir / asset.path
    analysis = await llm.analyze_images(
        [image_path],
        "Analyze this Telegram photo/screenshot for OCR, description, summary, facts and tags.",
        message.caption,
    )
    await persist_and_confirm(
        message=message,
        item_type="photo",
        source="telegram",
        model=settings.openrouter_vision_model,
        analysis=analysis,
        original_text=message.caption,
        assets=[asset],
        metadata={"telegram_message_id": message.message_id},
        archive_id=archive_id,
    )


@router.message(F.document)
@require_allowed
async def handle_document(message: Message) -> None:
    archive_id = uuid4()
    document = message.document
    if document is None:
        raise RuntimeError("Document message without document payload")

    filename = document.file_name or f"document-{document.file_unique_id}"
    media_type = document.mime_type or mimetypes.guess_type(filename)[0]
    temp_path = storage.temp_path(f"{archive_id}-{filename}")
    await download_telegram_file(message.bot, document.file_id, temp_path)
    asset = storage.copy_asset(temp_path, archive_id, filename, media_type)

    if media_type and media_type.startswith("image/"):
        analysis = await llm.analyze_images(
            [settings.data_dir / asset.path],
            "Analyze this image document for OCR, description, summary, facts and tags.",
            message.caption,
        )
        model = settings.openrouter_vision_model
    else:
        analysis = await llm.analyze_document_metadata(filename, media_type, message.caption)
        model = settings.openrouter_text_model

    await persist_and_confirm(
        message=message,
        item_type="document",
        source="telegram",
        model=model,
        analysis=analysis,
        original_text=message.caption,
        assets=[asset],
        metadata={
            "telegram_message_id": message.message_id,
            "filename": filename,
            "media_type": media_type,
            "file_size": document.file_size,
            "parser_status": "asset_only" if media_type == "application/pdf" else "metadata_only",
        },
        archive_id=archive_id,
    )


@router.message(F.video)
@require_allowed
async def handle_video(message: Message) -> None:
    archive_id = uuid4()
    video = message.video
    if video is None:
        raise RuntimeError("Video message without video payload")

    filename = video.file_name or f"video-{video.file_unique_id}.mp4"
    media_type = video.mime_type or mimetypes.guess_type(filename)[0] or "video/mp4"
    temp_path = storage.temp_path(f"{archive_id}-{filename}")
    await download_telegram_file(message.bot, video.file_id, temp_path)
    original_asset = storage.copy_asset(temp_path, archive_id, filename, media_type)

    frames_dir = storage.dated_asset_dir() / f"{archive_id}-frames"
    frame_paths = await extract_frames(settings.data_dir / original_asset.path, frames_dir)
    frame_assets = [
        Asset(path=storage.relative(path), media_type="image/jpeg", role="video_frame", metadata={"index": index})
        for index, path in enumerate(frame_paths, start=1)
    ]
    analysis = await llm.analyze_images(
        frame_paths,
        "Analyze these video frames. Summarize the video, extract visible facts, OCR and tags.",
        message.caption,
    )
    await persist_and_confirm(
        message=message,
        item_type="video",
        source="telegram",
        model=settings.openrouter_vision_model,
        analysis=analysis,
        original_text=message.caption,
        assets=[original_asset, *frame_assets],
        metadata={
            "telegram_message_id": message.message_id,
            "duration": video.duration,
            "width": video.width,
            "height": video.height,
            "frame_count": len(frame_assets),
        },
        archive_id=archive_id,
    )


@router.message()
@require_allowed
async def handle_unsupported(message: Message) -> None:
    await message.answer("Dieser Nachrichtentyp wird noch nicht unterstützt.")


async def download_telegram_file(bot: Bot, file_id: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    await bot.download(file_id, destination=target)


async def persist_and_confirm(
    *,
    message: Message,
    item_type: Any,
    source: str,
    model: str,
    analysis: Any,
    original_text: str | None,
    assets: list[Asset],
    metadata: dict[str, Any],
    url: str | None = None,
    archive_id: Any | None = None,
) -> None:
    item_id = archive_id or uuid4()
    item = ArchiveItem(
        id=item_id,
        item_type=item_type,
        source=source,
        created_at=datetime.now(UTC),
        title=analysis.title,
        url=url,
        tags=analysis.tags,
        assets=assets,
        model=model,
        markdown_path=Path(""),
        original_text=original_text,
        analysis=analysis,
        metadata=metadata,
    )
    markdown_path = storage.write_markdown(item)
    item.markdown_path = Path(storage.relative(markdown_path))

    embedding_text = f"{item.title}\n\n{item.analysis.summary}\n\n{item.original_text or ''}"
    vectors = await embeddings.embed([embedding_text])
    await db.insert_item(item, embedding=vectors[0] if vectors else None)

    tags = ", ".join(item.tags[:6]) if item.tags else "keine Tags"
    await message.answer(
        f"Archiviert: <b>{escape_html(item.title)}</b>\nTags: {escape_html(tags)}",
        parse_mode=ParseMode.HTML,
    )


def escape_html(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def main() -> None:
    await db.connect()
    bot = Bot(settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    try:
        logger.info("Starting Telegram polling")
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()
        await llm.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())

