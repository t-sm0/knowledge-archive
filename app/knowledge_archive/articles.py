from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
import trafilatura


ARTICLE_TIMEOUT = httpx.Timeout(30.0)


class ArticleExtractionError(RuntimeError):
    pass


@dataclass
class ArticleExtraction:
    url: str
    title: str | None
    author: str | None
    date: str | None
    text: str
    html_path: Path
    text_path: Path


def article_urls(urls: list[str]) -> list[str]:
    return [url for url in urls if is_article_url(url)]


def is_article_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    host = parsed.netloc.lower()
    if any(
        blocked in host
        for blocked in (
            "instagram.com",
            "youtube.com",
            "youtu.be",
            "tiktok.com",
            "x.com",
            "twitter.com",
        )
    ):
        return False
    path = parsed.path.strip("/")
    if not path:
        return False
    if Path(path).suffix.lower() in {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".mp4",
        ".mov",
        ".avi",
        ".pdf",
        ".zip",
    }:
        return False
    return True


async def fetch_article(url: str, output_dir: Path) -> ArticleExtraction:
    return await asyncio.to_thread(_fetch_article_sync, url, output_dir)


def _fetch_article_sync(url: str, output_dir: Path) -> ArticleExtraction:
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.Client(timeout=ARTICLE_TIMEOUT, follow_redirects=True) as client:
            response = client.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 knowledge-archive/0.1 "
                        "(compatible; personal archive bot)"
                    )
                },
            )
            response.raise_for_status()
            html = response.text
    except httpx.HTTPError as exc:
        raise ArticleExtractionError(f"Could not fetch article URL: {exc}") from exc

    html_path = output_dir / "article.html"
    html_path.write_text(html, encoding="utf-8", errors="replace")
    try:
        extracted = trafilatura.extract(
            html,
            url=url,
            output_format="json",
            with_metadata=True,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
    except Exception as exc:
        raise ArticleExtractionError(f"Could not parse article URL: {exc}") from exc
    if not extracted:
        raise ArticleExtractionError("Could not extract readable article text from URL")
    try:
        payload = json.loads(extracted)
    except json.JSONDecodeError as exc:
        raise ArticleExtractionError("Article extractor returned invalid metadata JSON") from exc
    text = str(payload.get("text") or "").strip()
    if not text:
        raise ArticleExtractionError("Extracted article did not contain readable text")
    text_path = output_dir / "article.txt"
    text_path.write_text(text, encoding="utf-8")
    return ArticleExtraction(
        url=url,
        title=payload.get("title"),
        author=payload.get("author"),
        date=payload.get("date"),
        text=text,
        html_path=html_path,
        text_path=text_path,
    )
