import re
from collections.abc import Iterable


URL_RE = re.compile(r"https?://[^\s<>()\"']+")


def extract_urls(text: str) -> list[str]:
    return list(dict.fromkeys(match.rstrip(".,;:!?") for match in URL_RE.findall(text)))


def normalize_tags(tags: Iterable[str], max_tags: int = 12) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        clean = re.sub(r"[^a-zA-Z0-9äöüÄÖÜß_-]+", "-", tag.strip().lower()).strip("-")
        if not clean or clean in seen:
            continue
        seen.add(clean)
        normalized.append(clean[:48])
        if len(normalized) >= max_tags:
            break
    return normalized

