# knowledge-archive

Docker-Compose-Projekt fuer einen privaten Telegram-Wissensarchiv-Bot.

Der Bot ist ein privater Telegram-Assistent fuer dein Wissensarchiv. Plain-Text-Nachrichten werden als Chat/Fragen gegen die Knowledge Base behandelt. Mit `/archive` sowie durch Fotos, Screenshots, Dokumente, Videos und Instagram-Links werden Inhalte lokal unter `./data` gespeichert, ueber OpenRouter analysiert und als OKF/Markdown plus Metadaten nach Postgres/pgvector geschrieben.

## Features

- Telegram Bot Polling mit `aiogram`
- Zugriff nur fuer `TELEGRAM_ALLOWED_USER_ID`
- Plain text = Assistant-Chat mit Kontext aus archivierten Eintraegen
- Chat/Ingest commands:
  - `/archive <text>` archiviert Text oder Links explizit
  - `/ask <frage>` beantwortet Fragen explizit mit Kontext aus archivierten Eintraegen
  - `/chat <nachricht>` ist ein Alias fuer Assistant-Chat
- Text-Ingest per `/archive` mit URL-Erkennung, LLM-Summary, Markdown und DB-Eintrag
- Foto/Screenshot-Ingest mit groesster Telegram-Foto-Version, lokaler Asset-Speicherung und Vision-Analyse
- Instagram-Post/Reel-Links mit `yt-dlp`, optionalen Cookies, lokaler Medienablage und Vision-Analyse
- Dokument-Ingest mit Asset-Speicherung; PDFs werden zunaechst als Asset plus Metadaten archiviert
- Video-Ingest mit `ffmpeg` Frame-Extraktion alle 3 Sekunden, maximal 20 Frames, plus Vision-Analyse
- Validierte JSON-Ausgabe per Pydantic
- OKF/Markdown mit YAML Frontmatter
- Postgres/pgvector via Docker Compose
- Embedding-Service als saubere TODO-Grenze fuer spaeteres bge-m3

## Setup

```bash
cp .env.example .env
```

Dann `.env` ausfuellen:

```dotenv
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_USER_ID=...
OPENROUTER_API_KEY=...
OPENROUTER_TEXT_MODEL=deepseek/deepseek-v4-flash
OPENROUTER_REASONING_MODEL=z-ai/glm-5.2
OPENROUTER_VISION_MODEL=minimax/minimax-m3
INSTAGRAM_COOKIES_FILE=/app/data/secrets/instagram-cookies.txt
```

Keine echten Secrets committen. `.env` ist in `.gitignore` ausgeschlossen.

## Start

```bash
docker compose up --build
```

Nur Postgres starten:

```bash
docker compose up postgres
```

Logs ansehen:

```bash
docker compose logs -f bot
```

Stoppen:

```bash
docker compose down
```

Volumes loeschen, inklusive Postgres-Datenbank:

```bash
docker compose down -v
```

## Datenlayout

Lokale Daten liegen unter `./data`:

```text
data/
  assets/YYYY/MM/DD/
  notes/YYYY/MM/DD/
  secrets/
  tmp/
```

Markdown-Dateien enthalten YAML Frontmatter:

```yaml
---
id: ...
type: text
source: telegram
created: ...
url: ...
tags: [...]
assets: [...]
model: ...
---
```

Danach folgen:

- `Summary`
- `Warum interessant`
- `Extrahierte Fakten`
- `Offene Fragen`
- `Original`

## Datenbank

`db/init.sql` erstellt:

- `vector` Extension
- Tabelle `archive_items`
- Indizes fuer Datum, Tags und Assets
- `embedding vector(1024)` als Platzhalter fuer spaetere bge-m3-Embeddings

## OpenRouter models

Current defaults:

- Text/archive summaries: `deepseek/deepseek-v4-flash`
  - Fast, low-cost MoE model with 1M context. Good default for Telegram text, links, captions and document metadata.
- Long reasoning fallback: `z-ai/glm-5.2`
  - Higher-cost reasoning model with 1M context. Keep this for later complex document parsing, project-level synthesis, or difficult multi-step extraction.
- Photos, screenshots and video frames: `minimax/minimax-m3`
  - Current open-weight multimodal model with text, image and video input support, 1M context, and a much lower price than frontier closed multimodal models.

Optional experimental/free multimodal alternative:

- `nvidia/nemotron-nano-12b-v2-vl:free`
  - Open multimodal model focused on OCR, charts, document intelligence and video understanding. Useful for cost-free experiments, but keep `minimax/minimax-m3` as the default when reliability matters.

The bot validates every LLM response against the archive JSON schema before writing files or database rows. If a primary model returns fenced JSON or the wrong shape, the client attempts one repair pass through `OPENROUTER_TEXT_MODEL` and validates the repaired output.

## Instagram links

The bot detects Instagram post/reel URLs in text messages, downloads media with `yt-dlp`, stores the original downloaded files under `./data/assets/YYYY/MM/DD/`, extracts frames from downloaded videos, then sends downloaded images/video frames plus captions and metadata to the vision model.

Supported URL shapes include:

- `https://www.instagram.com/p/...`
- `https://www.instagram.com/reel/...`
- `https://www.instagram.com/tv/...`

Login is optional but often needed. Public posts can sometimes be downloaded without cookies, but Instagram commonly requires an authenticated browser session for reels, private/follower-only posts, stories, age-gated posts, or after rate limits.

To use login cookies:

1. Log in to Instagram in a browser.
2. Export Instagram cookies in Netscape `cookies.txt` format.
3. Save them locally as:

```bash
mkdir -p data/secrets
$EDITOR data/secrets/instagram-cookies.txt
chmod 600 data/secrets/instagram-cookies.txt
```

4. Keep this in `.env`:

```dotenv
INSTAGRAM_COOKIES_FILE=/app/data/secrets/instagram-cookies.txt
```

`./data` is git-ignored, so cookies are not committed.

## Chat and archive Q&A

Plain text messages are treated as assistant chat over your archive. Use `/archive` when you want to save a text note or link:

```text
Was habe ich zuletzt zu pgvector gespeichert?
/archive https://example.com Ein Link, den ich spaeter zusammenfassen will.
/ask Welche Links habe ich zu pgvector gespeichert?
/chat Erklaere mir kurz, wie ich dieses Archiv nutzen sollte.
```

Plain text and `/ask` use a simple lexical database search over title, summary, original text and tags, then ask the configured text model to answer from those matching archive items. Embedding search remains a TODO behind the existing embedding service boundary.

## Hinweise

- OpenRouter-Modelle sind ueber `.env` konfigurierbar:
  - `OPENROUTER_TEXT_MODEL`
  - `OPENROUTER_REASONING_MODEL`
  - `OPENROUTER_VISION_MODEL`
- PDF-Inhalte werden aktuell nicht geparst. Sie werden als Asset gespeichert und mit Metadaten archiviert.
- Videoanalyse basiert auf extrahierten JPG-Frames, nicht auf Tonspur-Transkription.
- Bei Fehlern schreibt der Bot Details ins Container-Log und sendet dem erlaubten Telegram-User eine kurze Fehlermeldung.
