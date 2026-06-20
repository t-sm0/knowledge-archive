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
from aiogram.filters import Command
from aiogram.types import Message

from knowledge_archive.articles import ArticleExtractionError, article_urls, fetch_article
from knowledge_archive.config import settings
from knowledge_archive.db import Database
from knowledge_archive.documents import extract_pdf_text
from knowledge_archive.embeddings import EmbeddingService
from knowledge_archive.llm import OpenRouterClient
from knowledge_archive.models import ArchiveItem, Asset
from knowledge_archive.social import download_instagram_url, instagram_urls, read_info_json
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
embeddings = EmbeddingService(settings)
MAX_TELEGRAM_MESSAGE = 3900


def allowed(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id == settings.telegram_allowed_user_id)


def require_allowed(
    handler: Callable[[Message], Awaitable[None]],
) -> Callable[[Message], Awaitable[None]]:
    async def wrapped(message: Message) -> None:
        if not allowed(message):
            user_id = message.from_user.id if message.from_user else None
            logger.warning("Rejected message from unauthorized user_id=%s", user_id)
            return
        try:
            await handler(message)
        except Exception as exc:
            logger.exception("Ingest failed")
            try:
                detail = str(exc).strip()
                suffix = (
                    f"\n\n{escape_html(detail)}"
                    if detail
                    else "\n\nDetails stehen im Bot-Log."
                )
                await message.answer(f"Ingest fehlgeschlagen.{suffix}", parse_mode=ParseMode.HTML)
            except TelegramAPIError:
                logger.exception("Could not send Telegram error response")

    return wrapped


@router.message(Command("help"))
@require_allowed
async def handle_help(message: Message) -> None:
    await message.answer(
        "Commands:\n"
        "/archive <text> - archive a text note or link\n"
        "/ask <question> - explicit archive Q&A\n"
        "/chat <message> - assistant chat alias\n\n"
        "Plain text chats with the assistant using the archive as context. "
        "News/article links, photos, documents, videos and Instagram links are archived automatically."
    )


@router.message(Command("archive"))
@require_allowed
async def handle_archive(message: Message) -> None:
    text = command_payload(message.text or "")
    if not text:
        await message.answer("Usage: /archive <text or link>")
        return
    await archive_text_message(message, text)


@router.message(Command("ask"))
@require_allowed
async def handle_ask(message: Message) -> None:
    question = command_payload(message.text or "")
    if not question:
        await message.answer("Usage: /ask <question>")
        return
    await answer_with_archive_context(message, question)


@router.message(Command("chat"))
@require_allowed
async def handle_chat(message: Message) -> None:
    text = command_payload(message.text or "")
    if not text:
        await message.answer("Usage: /chat <message>")
        return
    answer = await llm.chat(text)
    await message.answer(truncate_telegram(answer))


@router.message(F.text)
@require_allowed
async def handle_text(message: Message) -> None:
    text = message.text or ""
    urls = extract_urls(text)
    instagram = instagram_urls(urls)
    if instagram:
        await handle_instagram_text(message, text, urls, instagram)
        return
    articles = article_urls(urls)
    if articles:
        try:
            await archive_article_message(message, text, urls, articles[0])
            return
        except ArticleExtractionError:
            logger.info("Article extraction failed; falling back to archive chat", exc_info=True)

    await answer_with_archive_context(message, text)


async def archive_text_message(message: Message, text: str) -> None:
    urls = extract_urls(text)
    articles = article_urls(urls)
    if articles:
        try:
            await archive_article_message(message, text, urls, articles[0])
            return
        except ArticleExtractionError:
            logger.info("Article extraction failed; archiving raw text instead", exc_info=True)

    model = select_text_model(text)
    analysis = await llm.analyze_text(text, urls, model=model)
    await persist_and_confirm(
        message=message,
        item_type="text",
        source="telegram",
        model=model,
        analysis=analysis,
        original_text=text,
        url=urls[0] if urls else None,
        assets=[],
        metadata={"urls": urls, "telegram_message_id": message.message_id},
    )


async def archive_article_message(
    message: Message,
    text: str,
    urls: list[str],
    article_url: str,
) -> None:
    archive_id = uuid4()
    article_dir = storage.tmp_dir / "articles" / str(archive_id)
    article = await fetch_article(article_url, article_dir)
    html_asset = storage.copy_asset(
        article.html_path,
        archive_id,
        "article.html",
        "text/html",
        role="article_html",
    )
    text_asset = storage.copy_asset(
        article.text_path,
        archive_id,
        "article.txt",
        "text/plain",
        role="article_text",
    )
    source_text = (
        f"Telegram note:\n{text}\n\n"
        f"URL: {article.url}\n"
        f"Title: {article.title or '-'}\n"
        f"Author: {article.author or '-'}\n"
        f"Date: {article.date or '-'}\n\n"
        f"Article text:\n{article.text[:60000]}"
    )
    model = select_text_model(source_text)
    analysis = await llm.analyze_text(source_text, urls, model=model)
    await persist_and_confirm(
        message=message,
        item_type="article",
        source="telegram/article",
        model=model,
        analysis=analysis,
        original_text=article.text,
        url=article.url,
        assets=[html_asset, text_asset],
        metadata={
            "urls": urls,
            "article_title": article.title,
            "article_author": article.author,
            "article_date": article.date,
            "telegram_message_id": message.message_id,
        },
        archive_id=archive_id,
    )


async def answer_with_archive_context(message: Message, question: str) -> None:
    query_vectors = await embeddings.embed([question])
    query_embedding = query_vectors[0] if query_vectors else None
    items = await db.search_items(question, limit=8, query_embedding=query_embedding)
    answer = await llm.answer_archive_question(question, items)
    await message.answer(truncate_telegram(answer))


async def handle_instagram_text(
    message: Message,
    text: str,
    urls: list[str],
    instagram: list[str],
) -> None:
    archive_id = uuid4()
    downloads = []
    assets: list[Asset] = []
    image_paths: list[Path] = []
    frame_paths: list[Path] = []
    metadata: dict[str, Any] = {
        "urls": urls,
        "instagram_urls": instagram,
        "telegram_message_id": message.message_id,
    }

    for index, url in enumerate(instagram, start=1):
        download_dir = storage.tmp_dir / "instagram" / str(archive_id) / str(index)
        download = await download_instagram_url(url, download_dir, settings.instagram_cookies_file)
        downloads.append(download)
        if download.info_json:
            info_asset = storage.copy_asset(
                download.info_json,
                archive_id,
                f"instagram-{index}.info.json",
                "application/json",
                role="instagram_metadata",
            )
            assets.append(info_asset)
            metadata[f"instagram_info_{index}"] = read_info_json(download.info_json)

        for media in download.media:
            asset = storage.copy_asset(
                media.path,
                archive_id,
                media.path.name,
                media.media_type,
                role=media.role,
            )
            assets.append(asset)
            asset_path = settings.data_dir / asset.path
            if media.media_type and media.media_type.startswith("image/"):
                image_paths.append(asset_path)
            elif media.media_type and media.media_type.startswith("video/"):
                frames_dir = storage.dated_asset_dir() / f"{archive_id}-instagram-{index}-frames"
                extracted = await extract_frames(asset_path, frames_dir)
                frame_paths.extend(extracted)
                assets.extend(
                    Asset(
                        path=storage.relative(frame),
                        media_type="image/jpeg",
                        role="instagram_video_frame",
                        metadata={"source_url": url, "index": frame_index},
                    )
                    for frame_index, frame in enumerate(extracted, start=1)
                )

    prompt_text = build_instagram_prompt(text, downloads)
    visual_paths = [*image_paths, *frame_paths[:20]]
    if visual_paths:
        analysis = await llm.analyze_images(
            visual_paths,
            prompt_text,
            text,
        )
        model = settings.openrouter_vision_model
    else:
        analysis = await llm.analyze_text(
            prompt_text,
            instagram,
            model=settings.openrouter_text_model,
        )
        model = settings.openrouter_text_model

    await persist_and_confirm(
        message=message,
        item_type="instagram",
        source="telegram/instagram",
        model=model,
        analysis=analysis,
        original_text=text,
        url=instagram[0],
        assets=assets,
        metadata=metadata,
        archive_id=archive_id,
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

    extracted_text: str | None = None
    if media_type == "application/pdf":
        extracted_text = extract_pdf_text(settings.data_dir / asset.path)
        if extracted_text:
            analysis = await llm.analyze_text(
                f"PDF filename: {filename}\nCaption: {message.caption or ''}\n\n{extracted_text}",
                [],
                model=select_text_model(extracted_text),
            )
            model = select_text_model(extracted_text)
        else:
            analysis = await llm.analyze_document_metadata(filename, media_type, message.caption)
            model = settings.openrouter_text_model
    elif media_type and media_type.startswith("image/"):
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
            "parser_status": (
                "text_extracted"
                if extracted_text
                else "asset_only"
                if media_type == "application/pdf"
                else "metadata_only"
            ),
        },
        archive_id=archive_id,
        extracted_override=extracted_text,
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
        Asset(
            path=storage.relative(path),
            media_type="image/jpeg",
            role="video_frame",
            metadata={"index": index},
        )
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
    extracted_override: str | None = None,
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
        original_text=extracted_override or original_text,
        analysis=analysis,
        metadata=metadata,
    )
    markdown_path = storage.write_markdown(item)
    item.markdown_path = Path(storage.relative(markdown_path))

    embedding_text = (
        f"{item.title}\n\n"
        f"{item.analysis.summary}\n\n"
        f"{item.analysis.why_interesting}\n\n"
        f"{' '.join(item.analysis.facts)}\n\n"
        f"{item.original_text or ''}"
    )
    vectors = await embeddings.embed([embedding_text])
    await db.insert_item(item, embedding=vectors[0] if vectors else None)

    tags = ", ".join(item.tags[:6]) if item.tags else "keine Tags"
    await message.answer(
        f"Archiviert: <b>{escape_html(item.title)}</b>\nTags: {escape_html(tags)}",
        parse_mode=ParseMode.HTML,
    )


def escape_html(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def command_payload(text: str) -> str:
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) == 2 else ""


def truncate_telegram(text: str) -> str:
    if len(text) <= MAX_TELEGRAM_MESSAGE:
        return text
    return text[: MAX_TELEGRAM_MESSAGE - 20].rstrip() + "\n\n[truncated]"


def build_instagram_prompt(text: str, downloads: list[Any]) -> str:
    lines = [
        "Analyze this Instagram post/reel shared to a personal knowledge archive.",
        "Use the downloaded media, captions and metadata. Extract OCR if visible.",
        f"Telegram message:\n{text}",
        "",
        "Instagram downloads:",
    ]
    for download in downloads:
        lines.extend(
            [
                f"- URL: {download.webpage_url or download.url}",
                f"  Title: {download.title or '-'}",
                f"  Uploader: {download.uploader or '-'}",
                f"  Caption/description: {download.description or '-'}",
                f"  Media files: {len(download.media)}",
            ]
        )
    return "\n".join(lines)


def select_text_model(text: str) -> str:
    if len(text) >= 12000:
        return settings.openrouter_reasoning_model
    return settings.openrouter_text_model


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
        await embeddings.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
