from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import yaml
from slugify import slugify

from knowledge_archive.models import ArchiveItem, Asset


class Storage:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.assets_dir = data_dir / "assets"
        self.notes_dir = data_dir / "notes"
        self.tmp_dir = data_dir / "tmp"
        for directory in (self.assets_dir, self.notes_dir, self.tmp_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def dated_asset_dir(self, when: datetime | None = None) -> Path:
        when = when or datetime.now(UTC)
        directory = self.assets_dir / f"{when:%Y}" / f"{when:%m}" / f"{when:%d}"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def dated_note_dir(self, when: datetime | None = None) -> Path:
        when = when or datetime.now(UTC)
        directory = self.notes_dir / f"{when:%Y}" / f"{when:%m}" / f"{when:%d}"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def copy_asset(
        self,
        source_path: Path,
        archive_id: UUID,
        filename: str,
        media_type: str | None,
        role: str = "original",
    ) -> Asset:
        safe_name = _safe_filename(filename)
        target = self.dated_asset_dir() / f"{archive_id}-{safe_name}"
        shutil.copyfile(source_path, target)
        return Asset(path=self.relative(target), media_type=media_type, role=role)

    def temp_path(self, name: str) -> Path:
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        return self.tmp_dir / _safe_filename(name)

    def relative(self, path: Path) -> str:
        return str(path.relative_to(self.data_dir))

    def write_markdown(self, item: ArchiveItem) -> Path:
        slug = slugify(item.title, max_length=80) or item.item_type
        path = self.dated_note_dir(item.created_at) / f"{item.created_at:%H%M%S}-{item.id}-{slug}.md"
        path.write_text(render_markdown(item), encoding="utf-8")
        return path


def render_markdown(item: ArchiveItem) -> str:
    frontmatter = {
        "id": str(item.id),
        "type": item.item_type,
        "source": item.source,
        "created": item.created_at.isoformat(),
        "url": item.url,
        "tags": item.tags,
        "assets": [asset.model_dump(mode="json") for asset in item.assets],
        "model": item.model,
    }
    yaml_text = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()
    original = item.original_text or item.analysis.extracted_text or ""
    return (
        f"---\n{yaml_text}\n---\n\n"
        f"# {item.title}\n\n"
        f"## Summary\n\n{item.analysis.summary}\n\n"
        f"## Warum interessant\n\n{item.analysis.why_interesting or '-'}\n\n"
        "## Extrahierte Fakten\n\n"
        f"{_bullet_list(item.analysis.facts)}\n\n"
        "## Offene Fragen\n\n"
        f"{_bullet_list(item.analysis.open_questions)}\n\n"
        f"## Original\n\n{original or '-'}\n"
    )


def _bullet_list(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values) if values else "-"


def _safe_filename(filename: str) -> str:
    name = filename.strip().replace("/", "_").replace("\\", "_")
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
    return name[:180] or "asset"

