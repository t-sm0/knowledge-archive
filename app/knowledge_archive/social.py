from __future__ import annotations

import asyncio
import json
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}
INSTAGRAM_PATH_PREFIXES = ("/p/", "/reel/", "/reels/", "/tv/")


class InstagramDownloadError(RuntimeError):
    pass


@dataclass
class DownloadedMedia:
    path: Path
    media_type: str | None
    role: str


@dataclass
class InstagramDownload:
    url: str
    title: str | None = None
    description: str | None = None
    uploader: str | None = None
    webpage_url: str | None = None
    media: list[DownloadedMedia] = field(default_factory=list)
    info_json: Path | None = None


def instagram_urls(urls: list[str]) -> list[str]:
    return [url for url in urls if is_instagram_post_url(url)]


def is_instagram_post_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    return host in INSTAGRAM_HOSTS and parsed.path.startswith(INSTAGRAM_PATH_PREFIXES)


async def download_instagram_url(
    url: str,
    output_dir: Path,
    cookies_file: Path | None = None,
) -> InstagramDownload:
    return await asyncio.to_thread(_download_instagram_url_sync, url, output_dir, cookies_file)


def _download_instagram_url_sync(
    url: str,
    output_dir: Path,
    cookies_file: Path | None,
) -> InstagramDownload:
    output_dir.mkdir(parents=True, exist_ok=True)
    before = {path.resolve() for path in output_dir.rglob("*") if path.is_file()}
    cookie_path = cookies_file if cookies_file and cookies_file.exists() else None
    options = {
        "outtmpl": str(output_dir / "%(extractor)s-%(id)s-%(playlist_index|0)02d.%(ext)s"),
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "writeinfojson": True,
        "writedescription": True,
        "trim_file_name": 120,
        "restrictfilenames": True,
        "noplaylist": False,
        "max_downloads": 20,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "ignoreerrors": False,
    }
    if cookie_path:
        options["cookiefile"] = str(cookie_path)

    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except DownloadError as exc:
        hint = ""
        if not cookie_path:
            hint = " Instagram may require login cookies for this post."
        raise InstagramDownloadError(f"Instagram download failed.{hint}") from exc

    after = {path.resolve() for path in output_dir.rglob("*") if path.is_file()}
    created = sorted(Path(path) for path in (after - before))
    media: list[DownloadedMedia] = []
    info_json: Path | None = None
    descriptions: list[str] = []
    for path in created:
        if path.suffix == ".json":
            info_json = info_json or path
            continue
        if path.suffix == ".description":
            descriptions.append(path.read_text(encoding="utf-8", errors="replace"))
            continue
        media_type = mimetypes.guess_type(path.name)[0]
        if not media_type:
            continue
        if media_type.startswith("image/") or media_type.startswith("video/"):
            media.append(
                DownloadedMedia(
                    path=path,
                    media_type=media_type,
                    role="instagram_media",
                )
            )

    metadata = _normalize_info(info)
    description = metadata.get("description") or "\n\n".join(descriptions) or None
    return InstagramDownload(
        url=url,
        title=metadata.get("title"),
        description=description,
        uploader=metadata.get("uploader") or metadata.get("channel"),
        webpage_url=metadata.get("webpage_url") or url,
        media=media,
        info_json=info_json,
    )


def _normalize_info(info: object) -> dict[str, str]:
    if not isinstance(info, dict):
        return {}
    if info.get("_type") == "playlist" and isinstance(info.get("entries"), list):
        first = next((entry for entry in info["entries"] if isinstance(entry, dict)), {})
        merged = {**first, **{key: value for key, value in info.items() if key != "entries"}}
        return {key: str(value) for key, value in merged.items() if value is not None}
    return {
        key: str(value)
        for key, value in info.items()
        if value is not None and not isinstance(value, (dict, list))
    }


def read_info_json(path: Path | None) -> dict[str, object]:
    if not path or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
