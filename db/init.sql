CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS archive_items (
    id UUID PRIMARY KEY,
    item_type TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    url TEXT,
    tags TEXT[] NOT NULL DEFAULT '{}',
    assets JSONB NOT NULL DEFAULT '[]'::jsonb,
    model TEXT NOT NULL,
    markdown_path TEXT NOT NULL,
    original_text TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding vector(1024)
);

CREATE INDEX IF NOT EXISTS archive_items_created_at_idx ON archive_items (created_at DESC);
CREATE INDEX IF NOT EXISTS archive_items_tags_idx ON archive_items USING GIN (tags);
CREATE INDEX IF NOT EXISTS archive_items_assets_idx ON archive_items USING GIN (assets);

