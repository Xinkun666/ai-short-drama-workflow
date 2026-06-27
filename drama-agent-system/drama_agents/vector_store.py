from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any


VECTOR_DIM = 256


class LocalVectorStore:
    """Small persistent vector store backed by SQLite and deterministic local embeddings."""

    def __init__(self, path: Path | str, *, dimensions: int = VECTOR_DIM):
        self.path = Path(path)
        self.dimensions = dimensions
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS rag_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    record_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    title TEXT NOT NULL,
                    text TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    vector_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_rag_chunks_record ON rag_chunks(record_id, source_type)"
            )

    def upsert_chunks(self, chunks: list[dict[str, Any]]) -> int:
        rows = self._chunk_rows(chunks)
        with self.connect() as connection:
            self._upsert_rows(connection, rows)
        return len(rows)

    def replace_record_chunks(self, record_ids: list[str], chunks: list[dict[str, Any]]) -> int:
        clean_record_ids = [str(record_id) for record_id in record_ids if str(record_id).strip()]
        rows = self._chunk_rows(chunks)
        with self.connect() as connection:
            if clean_record_ids:
                placeholders = ",".join("?" for _ in clean_record_ids)
                connection.execute(f"DELETE FROM rag_chunks WHERE record_id IN ({placeholders})", clean_record_ids)
            self._upsert_rows(connection, rows)
        return len(rows)

    def _chunk_rows(self, chunks: list[dict[str, Any]]) -> list[tuple[str, str, str, str, str, str, str, str]]:
        rows = []
        for chunk in chunks:
            text = str(chunk.get("text") or "").strip()
            chunk_id = str(chunk.get("chunk_id") or "").strip()
            if not chunk_id or not text:
                continue
            rows.append(
                (
                    chunk_id,
                    str(chunk.get("record_id") or ""),
                    str(chunk.get("source_type") or ""),
                    str(chunk.get("source_ref") or ""),
                    str(chunk.get("title") or ""),
                    text,
                    json.dumps(chunk.get("metadata") or {}, ensure_ascii=False),
                    json.dumps(embed_text(text, self.dimensions), ensure_ascii=False),
                )
            )
        return rows

    def _upsert_rows(self, connection, rows: list[tuple[str, str, str, str, str, str, str, str]]) -> None:
        connection.executemany(
            """
            INSERT INTO rag_chunks (
                chunk_id, record_id, source_type, source_ref, title, text, metadata_json, vector_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chunk_id) DO UPDATE SET
                record_id=excluded.record_id,
                source_type=excluded.source_type,
                source_ref=excluded.source_ref,
                title=excluded.title,
                text=excluded.text,
                metadata_json=excluded.metadata_json,
                vector_json=excluded.vector_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            rows,
        )

    def search(self, query: str, *, record_ids: list[str] | None = None, limit: int = 6) -> list[dict[str, Any]]:
        query_vector = embed_text(query, self.dimensions)
        with self.connect() as connection:
            if record_ids:
                placeholders = ",".join("?" for _ in record_ids)
                rows = connection.execute(
                    f"SELECT * FROM rag_chunks WHERE record_id IN ({placeholders})",
                    [str(record_id) for record_id in record_ids],
                ).fetchall()
            else:
                rows = connection.execute("SELECT * FROM rag_chunks").fetchall()
        scored = []
        for row in rows:
            vector = json.loads(row["vector_json"])
            score = cosine_similarity(query_vector, vector)
            scored.append(
                {
                    "chunk_id": row["chunk_id"],
                    "record_id": row["record_id"],
                    "source_type": row["source_type"],
                    "source_ref": row["source_ref"],
                    "title": row["title"],
                    "text": row["text"],
                    "metadata": json.loads(row["metadata_json"] or "{}"),
                    "score": score,
                }
            )
        scored.sort(key=lambda item: item["score"], reverse=True)
        return [item for item in scored[:limit] if item["score"] > 0]


def embed_text(text: str, dimensions: int = VECTOR_DIM) -> list[float]:
    vector = [0.0] * dimensions
    for token in tokenize(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest()
        index = int.from_bytes(digest, "big") % dimensions
        vector[index] += 1.0
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        return vector
    return [round(value / norm, 8) for value in vector]


def tokenize(text: str) -> list[str]:
    lowered = text.lower()
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", lowered)
    bigrams = [lowered[index : index + 2] for index in range(max(0, len(lowered) - 1)) if has_cjk(lowered[index : index + 2])]
    return tokens + bigrams


def has_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def build_material_chunks(database, outputs_path: Path, record_ids: list[str]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for record_id in record_ids:
        record = database.find_record(record_id)
        if not record:
            continue
        for chapter in database.list_chapters(record_id):
            if chapter.get("include_in_analysis") is False:
                continue
            chapter_id = str(chapter.get("chapter_id") or "")
            chapter_title = str(chapter.get("title") or "")
            book_name = str(record.get("book_name") or "")
            text_path = resolve_output_path(chapter.get("text_path", ""), outputs_path)
            if text_path and text_path.exists():
                text = text_path.read_text(encoding="utf-8", errors="ignore")
                for index, part in enumerate(split_text(text), start=1):
                    chunks.append(
                        {
                            "chunk_id": f"{record_id}:chapter:{chapter_id}:{index}",
                            "record_id": record_id,
                            "source_type": "chapter",
                            "source_ref": chapter_id,
                            "title": chapter_title,
                            "text": format_chunk_text(book_name, chapter, part),
                            "metadata": {
                                "book_name": book_name,
                                "chapter_id": chapter_id,
                                "chunk_index": index,
                                "start_page": chapter.get("start_page"),
                                "end_page": chapter.get("end_page"),
                            },
                        }
                    )
            reader_path = resolve_reader_json_path(record, chapter, outputs_path)
            if not reader_path:
                continue
            for index, part in enumerate(reader_text_chunks(reader_path), start=1):
                chunks.append(
                    {
                        "chunk_id": f"{record_id}:reader:{chapter_id}:{index}",
                        "record_id": record_id,
                        "source_type": "reader",
                        "source_ref": chapter_id,
                        "title": chapter_title,
                        "text": format_chunk_text(book_name, chapter, part),
                        "metadata": {
                            "book_name": book_name,
                            "chapter_id": chapter_id,
                            "chunk_index": index,
                            "start_page": chapter.get("start_page"),
                            "end_page": chapter.get("end_page"),
                        },
                    }
                )
    return chunks


def resolve_reader_json_path(record: dict[str, Any], chapter: dict[str, Any], outputs_path: Path) -> Path | None:
    linked_path = resolve_output_path(chapter.get("reader_json_link", ""), outputs_path)
    if linked_path and linked_path.exists():
        return linked_path
    output_relative_path = str(record.get("output_relative_path") or "").strip()
    chapter_id = str(chapter.get("chapter_id") or "").strip()
    if not output_relative_path or not chapter_id:
        return None
    fallback = outputs_path / output_relative_path / "reader" / f"{chapter_id}_reader.json"
    return fallback if fallback.exists() else None


def resolve_output_path(value: str, outputs_path: Path) -> Path | None:
    if not value:
        return None
    if value.startswith("/outputs/"):
        return outputs_path / value.removeprefix("/outputs/")
    path = Path(value)
    if path.is_absolute():
        return path
    return outputs_path / value


def reader_text_chunks(reader_json_path: Path) -> list[str]:
    try:
        payload = json.loads(reader_json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(payload, dict):
        return []
    lines = []
    if payload.get("summary"):
        lines.extend(["摘要", str(payload.get("summary"))])
    for section in payload.get("sections") or []:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading") or "").strip()
        body = str(section.get("body") or "").strip()
        refs = section.get("page_refs") or []
        if heading:
            lines.append(heading)
        if body:
            lines.append(body)
        if refs:
            lines.append(f"页码：{', '.join(str(ref) for ref in refs)}")
    if payload.get("key_concepts"):
        lines.append("关键概念：" + "、".join(str(item) for item in payload.get("key_concepts") or []))
    if payload.get("drama_tags"):
        lines.append("短剧标签：" + "、".join(str(item) for item in payload.get("drama_tags") or []))
    return split_text("\n".join(lines), max_chars=1000, overlap=120)


def format_chunk_text(book_name: str, chapter: dict[str, Any], text: str) -> str:
    page_text = ""
    if chapter.get("start_page") or chapter.get("end_page"):
        page_text = f"页码：{chapter.get('start_page', '')}-{chapter.get('end_page', '')}"
    header = "\n".join(
        part
        for part in [
            f"书名：{book_name}" if book_name else "",
            f"章节：{chapter.get('chapter_id', '')} {chapter.get('title', '')}".strip(),
            page_text,
        ]
        if part
    )
    return f"{header}\n\n{text.strip()}" if header else text.strip()


def split_text(text: str, *, max_chars: int = 1200, overlap: int = 160) -> list[str]:
    clean = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not clean:
        return []
    parts = []
    start = 0
    while start < len(clean):
        end = min(len(clean), start + max_chars)
        if end < len(clean):
            next_break = max(clean.rfind("\n\n", start, end), clean.rfind("。", start, end))
            if next_break > start + 300:
                end = next_break + 1
        parts.append(clean[start:end].strip())
        if end >= len(clean):
            break
        start = max(0, end - overlap)
    return [part for part in parts if part]
