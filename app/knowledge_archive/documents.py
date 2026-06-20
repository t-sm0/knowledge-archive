from __future__ import annotations

from pathlib import Path

import pymupdf


def extract_pdf_text(path: Path, max_chars: int = 60000) -> str:
    chunks: list[str] = []
    with pymupdf.open(path) as document:
        for page in document:
            chunks.append(page.get_text("text"))
            if sum(len(chunk) for chunk in chunks) >= max_chars:
                break
    return "\n\n".join(chunks).strip()[:max_chars]
