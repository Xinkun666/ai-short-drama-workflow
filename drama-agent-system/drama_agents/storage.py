from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any


class MaterialDatabase:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS material_records (
                    record_id TEXT PRIMARY KEY,
                    parsed_at TEXT NOT NULL,
                    book_name TEXT NOT NULL,
                    source_relative_path TEXT NOT NULL,
                    page_count INTEGER NOT NULL DEFAULT 0,
                    total_words INTEGER NOT NULL DEFAULT 0,
                    chapter_count INTEGER NOT NULL DEFAULT 0,
                    excluded_count INTEGER NOT NULL DEFAULT 0,
                    refinement_status TEXT NOT NULL DEFAULT 'not_run',
                    refinement_message TEXT NOT NULL DEFAULT '',
                    refined_chapter_count INTEGER NOT NULL DEFAULT 0,
                    timeline_status TEXT NOT NULL DEFAULT 'not_run',
                    timeline_message TEXT NOT NULL DEFAULT '',
                    timeline_event_count INTEGER NOT NULL DEFAULT 0,
                    timeline_url TEXT NOT NULL DEFAULT '',
                    output_relative_path TEXT NOT NULL DEFAULT '',
                    detail_url TEXT NOT NULL DEFAULT '',
                    links_json TEXT NOT NULL DEFAULT '{}',
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS material_chapters (
                    record_id TEXT NOT NULL,
                    chapter_id TEXT NOT NULL,
                    section_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    start_page INTEGER NOT NULL DEFAULT 0,
                    end_page INTEGER NOT NULL DEFAULT 0,
                    kind TEXT NOT NULL DEFAULT 'chapter',
                    include_in_analysis INTEGER NOT NULL DEFAULT 1,
                    pdf_path TEXT NOT NULL DEFAULT '',
                    text_path TEXT NOT NULL DEFAULT '',
                    word_count INTEGER NOT NULL DEFAULT 0,
                    source_format TEXT NOT NULL DEFAULT '',
                    source_path TEXT NOT NULL DEFAULT '',
                    reader_status TEXT NOT NULL DEFAULT '',
                    reader_message TEXT NOT NULL DEFAULT '',
                    reader_json_link TEXT NOT NULL DEFAULT '',
                    reader_markdown_link TEXT NOT NULL DEFAULT '',
                    reader_html_link TEXT NOT NULL DEFAULT '',
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (record_id, chapter_id),
                    FOREIGN KEY (record_id) REFERENCES material_records(record_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS material_excluded_sections (
                    record_id TEXT NOT NULL,
                    section_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    start_page INTEGER NOT NULL DEFAULT 0,
                    end_page INTEGER NOT NULL DEFAULT 0,
                    kind TEXT NOT NULL DEFAULT '',
                    include_in_analysis INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (record_id, section_id),
                    FOREIGN KEY (record_id) REFERENCES material_records(record_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS timeline_events (
                    record_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    chapter_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    time_label TEXT NOT NULL DEFAULT '',
                    time_start_year REAL,
                    time_end_year REAL,
                    time_precision TEXT NOT NULL DEFAULT '',
                    place_label TEXT NOT NULL DEFAULT '',
                    place_scope TEXT NOT NULL DEFAULT '',
                    places_json TEXT NOT NULL DEFAULT '[]',
                    source_pages_json TEXT NOT NULL DEFAULT '[]',
                    importance INTEGER NOT NULL DEFAULT 0,
                    confidence TEXT NOT NULL DEFAULT '',
                    evidence_note TEXT NOT NULL DEFAULT '',
                    drama_potential TEXT NOT NULL DEFAULT '',
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (record_id, event_id),
                    FOREIGN KEY (record_id) REFERENCES material_records(record_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS script_generations (
                    generation_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    time_range TEXT NOT NULL,
                    time_start_year REAL,
                    time_end_year REAL,
                    selected_record_ids_json TEXT NOT NULL DEFAULT '[]',
                    matched_events_json TEXT NOT NULL DEFAULT '[]',
                    script_json TEXT NOT NULL DEFAULT '{}',
                    subjects_json TEXT NOT NULL DEFAULT '[]',
                    map_shots_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL DEFAULT '',
                    json_path TEXT NOT NULL DEFAULT '',
                    markdown_path TEXT NOT NULL DEFAULT '',
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS script_assistant_messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    generation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    selection_text TEXT NOT NULL DEFAULT '',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    contexts_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS script_edit_patches (
                    patch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    generation_id TEXT NOT NULL,
                    selection_text TEXT NOT NULL,
                    replacement_text TEXT NOT NULL,
                    answer TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    applied_at TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_material_chapters_record
                    ON material_chapters(record_id, chapter_id);
                CREATE INDEX IF NOT EXISTS idx_timeline_events_record
                    ON timeline_events(record_id, time_start_year, event_id);
                CREATE INDEX IF NOT EXISTS idx_script_generations_created
                    ON script_generations(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_script_assistant_messages_generation
                    ON script_assistant_messages(generation_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_script_edit_patches_generation
                    ON script_edit_patches(generation_id, status, created_at);
                """
            )

    def upsert_parse(self, record: dict[str, Any], result_data: dict[str, Any]) -> dict[str, Any]:
        normalized_record = normalize_record(record)
        warnings = result_data.get("warnings") or []
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO material_records (
                    record_id, parsed_at, book_name, source_relative_path, page_count, total_words,
                    chapter_count, excluded_count, refinement_status, refinement_message,
                    refined_chapter_count, timeline_status, timeline_message, timeline_event_count,
                    timeline_url, output_relative_path, detail_url, links_json, warnings_json, raw_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(record_id) DO UPDATE SET
                    parsed_at=excluded.parsed_at,
                    book_name=excluded.book_name,
                    source_relative_path=excluded.source_relative_path,
                    page_count=excluded.page_count,
                    total_words=excluded.total_words,
                    chapter_count=excluded.chapter_count,
                    excluded_count=excluded.excluded_count,
                    refinement_status=excluded.refinement_status,
                    refinement_message=excluded.refinement_message,
                    refined_chapter_count=excluded.refined_chapter_count,
                    timeline_status=excluded.timeline_status,
                    timeline_message=excluded.timeline_message,
                    timeline_event_count=excluded.timeline_event_count,
                    timeline_url=excluded.timeline_url,
                    output_relative_path=excluded.output_relative_path,
                    detail_url=excluded.detail_url,
                    links_json=excluded.links_json,
                    warnings_json=excluded.warnings_json,
                    raw_json=excluded.raw_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                record_values(normalized_record, warnings),
            )
            record_id = normalized_record["record_id"]
            connection.execute("DELETE FROM material_chapters WHERE record_id = ?", (record_id,))
            connection.execute("DELETE FROM material_excluded_sections WHERE record_id = ?", (record_id,))
            connection.execute("DELETE FROM timeline_events WHERE record_id = ?", (record_id,))
            for chapter in result_data.get("chapters", []):
                connection.execute(
                    """
                    INSERT INTO material_chapters (
                        record_id, chapter_id, section_id, title, start_page, end_page, kind,
                        include_in_analysis, pdf_path, text_path, word_count, source_format,
                        source_path, reader_status, reader_message, reader_json_link,
                        reader_markdown_link, reader_html_link, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    chapter_values(record_id, chapter),
                )
            for section in result_data.get("excluded_sections", []):
                connection.execute(
                    """
                    INSERT INTO material_excluded_sections (
                        record_id, section_id, title, start_page, end_page, kind, include_in_analysis, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    section_values(record_id, section),
                )
        return normalized_record

    def list_records(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM material_records ORDER BY parsed_at DESC, updated_at DESC"
            ).fetchall()
        return [record_from_row(row) for row in rows]

    def find_record(self, record_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM material_records WHERE record_id = ?", (record_id,)).fetchone()
        return record_from_row(row) if row else None

    def list_chapters(self, record_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM material_chapters WHERE record_id = ? ORDER BY chapter_id",
                (record_id,),
            ).fetchall()
        return [chapter_from_row(row) for row in rows]

    def list_excluded_sections(self, record_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM material_excluded_sections WHERE record_id = ? ORDER BY start_page, section_id",
                (record_id,),
            ).fetchall()
        return [section_from_row(row) for row in rows]

    def delete_record(self, record_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            connection.execute("DELETE FROM material_records WHERE record_id = ?", (record_id,))
        return self.list_records()

    def update_book_name(self, record_id: str, book_name: str) -> dict[str, Any]:
        clean_name = book_name.strip()
        if not clean_name:
            raise ValueError("书名不能为空")
        record = self.find_record(record_id)
        if not record:
            raise KeyError(record_id)
        record["book_name"] = clean_name
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE material_records
                SET book_name = ?,
                    raw_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE record_id = ?
                """,
                (clean_name, json_dumps(record), record_id),
            )
        updated = self.find_record(record_id)
        if not updated:
            raise KeyError(record_id)
        return updated

    def update_timeline(
        self,
        record_id: str,
        timeline_data: dict[str, Any],
        links: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        record = self.find_record(record_id)
        if not record:
            raise KeyError(record_id)
        merged_links = dict(record.get("links") or {})
        merged_links.update(links or {})
        events = read_timeline_events(timeline_data)
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE material_records
                SET timeline_status = ?,
                    timeline_message = ?,
                    timeline_event_count = ?,
                    timeline_url = ?,
                    links_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE record_id = ?
                """,
                (
                    timeline_data.get("status", "not_run"),
                    timeline_data.get("message", ""),
                    timeline_data.get("event_count", len(events)),
                    f"/materials/{record_id}/timeline",
                    json_dumps(merged_links),
                    record_id,
                ),
            )
            connection.execute("DELETE FROM timeline_events WHERE record_id = ?", (record_id,))
            for event in events:
                connection.execute(
                    """
                    INSERT INTO timeline_events (
                        record_id, event_id, chapter_id, title, content, time_label,
                        time_start_year, time_end_year, time_precision, place_label,
                        place_scope, places_json, source_pages_json, importance, confidence,
                        evidence_note, drama_potential, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    event_values(record_id, event),
                )
        updated = self.find_record(record_id)
        if not updated:
            raise KeyError(record_id)
        return updated

    def list_timeline_events(self, record_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM timeline_events WHERE record_id = ? ORDER BY time_start_year, event_id",
                (record_id,),
            ).fetchall()
        return [event_from_row(row) for row in rows]

    def save_script_generation(self, result: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO script_generations (
                    generation_id, created_at, topic, time_range, time_start_year, time_end_year,
                    selected_record_ids_json, matched_events_json, script_json, subjects_json,
                    map_shots_json, status, message, json_path, markdown_path, raw_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(generation_id) DO UPDATE SET
                    created_at=excluded.created_at,
                    topic=excluded.topic,
                    time_range=excluded.time_range,
                    time_start_year=excluded.time_start_year,
                    time_end_year=excluded.time_end_year,
                    selected_record_ids_json=excluded.selected_record_ids_json,
                    matched_events_json=excluded.matched_events_json,
                    script_json=excluded.script_json,
                    subjects_json=excluded.subjects_json,
                    map_shots_json=excluded.map_shots_json,
                    status=excluded.status,
                    message=excluded.message,
                    json_path=excluded.json_path,
                    markdown_path=excluded.markdown_path,
                    raw_json=excluded.raw_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                script_generation_values(result),
            )
        return result

    def list_script_generations(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM script_generations ORDER BY created_at DESC, updated_at DESC"
            ).fetchall()
        return [script_generation_from_row(row) for row in rows]

    def find_script_generation(self, generation_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM script_generations WHERE generation_id = ?",
                (generation_id,),
            ).fetchone()
        return script_generation_from_row(row) if row else None

    def update_script_article(self, generation_id: str, article: str) -> dict[str, Any]:
        current = self.find_script_generation(generation_id)
        if not current:
            raise KeyError(generation_id)
        script = dict(current.get("script") or {})
        script["article"] = article
        current["script"] = script
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE script_generations
                SET script_json = ?,
                    raw_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE generation_id = ?
                """,
                (json_dumps(script), json_dumps(current), generation_id),
            )
        updated = self.find_script_generation(generation_id)
        if not updated:
            raise KeyError(generation_id)
        return updated

    def delete_script_generation(self, generation_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            connection.execute("DELETE FROM script_generations WHERE generation_id = ?", (generation_id,))
        return self.list_script_generations()

    def add_script_assistant_message(
        self,
        *,
        generation_id: str,
        role: str,
        content: str,
        selection: str = "",
        result: dict[str, Any] | None = None,
        contexts: list[dict[str, Any]] | None = None,
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO script_assistant_messages (
                    generation_id, role, content, selection_text, result_json, contexts_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    generation_id,
                    role,
                    content,
                    selection,
                    json_dumps(result or {}),
                    json_dumps(contexts or []),
                ),
            )
            return int(cursor.lastrowid)

    def list_script_assistant_messages(self, generation_id: str, *, limit: int = 12) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM script_assistant_messages
                WHERE generation_id = ?
                ORDER BY message_id DESC
                LIMIT ?
                """,
                (generation_id, limit),
            ).fetchall()
        messages = [script_assistant_message_from_row(row) for row in rows]
        messages.reverse()
        return messages

    def list_script_assistant_messages_for_selection(
        self,
        generation_id: str,
        selection: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        target_key = selection_match_key(selection)
        if not target_key:
            return []
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM script_assistant_messages
                WHERE generation_id = ? AND selection_text <> ''
                ORDER BY message_id DESC
                LIMIT 200
                """,
                (generation_id,),
            ).fetchall()
        messages = [script_assistant_message_from_row(row) for row in rows]
        messages.reverse()
        matched = [message for message in messages if selections_match(selection, str(message.get("selection") or ""))]
        return matched[-limit:]

    def create_script_edit_patch(
        self,
        *,
        generation_id: str,
        selection: str,
        replacement: str,
        answer: str = "",
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO script_edit_patches (
                    generation_id, selection_text, replacement_text, answer, status
                ) VALUES (?, ?, ?, ?, 'pending')
                """,
                (generation_id, selection, replacement, answer),
            )
            return int(cursor.lastrowid)

    def latest_pending_script_edit_patch(self, generation_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM script_edit_patches
                WHERE generation_id = ? AND status = 'pending'
                ORDER BY patch_id DESC
                LIMIT 1
                """,
                (generation_id,),
            ).fetchone()
        return script_edit_patch_from_row(row) if row else None

    def mark_script_edit_patch_applied(self, patch_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE script_edit_patches
                SET status = 'applied',
                    applied_at = CURRENT_TIMESTAMP
                WHERE patch_id = ?
                """,
                (patch_id,),
            )


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    normalized.setdefault("links", {})
    normalized.setdefault("timeline_status", "not_run")
    normalized.setdefault("timeline_message", "")
    normalized.setdefault("timeline_event_count", 0)
    normalized.setdefault("timeline_url", f"/materials/{normalized.get('record_id', '')}/timeline")
    normalized.setdefault("detail_url", f"/materials/{normalized.get('record_id', '')}")
    normalized.setdefault("output_relative_path", "")
    normalized.setdefault("refinement_status", "not_run")
    normalized.setdefault("refinement_message", "")
    normalized.setdefault("refined_chapter_count", 0)
    return normalized


def record_values(record: dict[str, Any], warnings: list[Any]) -> tuple[Any, ...]:
    return (
        record["record_id"],
        record.get("parsed_at", ""),
        record.get("book_name", ""),
        record.get("source_relative_path", ""),
        int(record.get("page_count") or 0),
        int(record.get("total_words") or 0),
        int(record.get("chapter_count") or 0),
        int(record.get("excluded_count") or 0),
        record.get("refinement_status", "not_run"),
        record.get("refinement_message", ""),
        int(record.get("refined_chapter_count") or 0),
        record.get("timeline_status", "not_run"),
        record.get("timeline_message", ""),
        int(record.get("timeline_event_count") or 0),
        record.get("timeline_url", ""),
        record.get("output_relative_path", ""),
        record.get("detail_url", ""),
        json_dumps(record.get("links") or {}),
        json_dumps(warnings),
        json_dumps(record),
    )


def chapter_values(record_id: str, chapter: dict[str, Any]) -> tuple[Any, ...]:
    return (
        record_id,
        chapter.get("chapter_id", ""),
        chapter.get("section_id", ""),
        chapter.get("title", ""),
        int(chapter.get("start_page") or 0),
        int(chapter.get("end_page") or 0),
        chapter.get("kind", "chapter"),
        1 if chapter.get("include_in_analysis", True) else 0,
        chapter.get("pdf_path", ""),
        chapter.get("text_path", ""),
        int(chapter.get("word_count") or 0),
        chapter.get("source_format", ""),
        chapter.get("source_path", ""),
        chapter.get("reader_status", ""),
        chapter.get("reader_message", ""),
        chapter.get("reader_json_link", ""),
        chapter.get("reader_markdown_link", ""),
        chapter.get("reader_html_link", ""),
        json_dumps(chapter),
    )


def section_values(record_id: str, section: dict[str, Any]) -> tuple[Any, ...]:
    return (
        record_id,
        section.get("section_id", ""),
        section.get("title", ""),
        int(section.get("start_page") or 0),
        int(section.get("end_page") or 0),
        section.get("kind", ""),
        1 if section.get("include_in_analysis", False) else 0,
        json_dumps(section),
    )


def event_values(record_id: str, event: dict[str, Any]) -> tuple[Any, ...]:
    return (
        record_id,
        event.get("event_id", ""),
        event.get("chapter_id", ""),
        event.get("title", ""),
        event.get("content", ""),
        event.get("time_label", ""),
        event.get("time_start_year"),
        event.get("time_end_year"),
        event.get("time_precision", ""),
        event.get("place_label", ""),
        event.get("place_scope", ""),
        json_dumps(event.get("places") or []),
        json_dumps(event.get("source_pages") or []),
        int(event.get("importance") or 0),
        event.get("confidence", ""),
        event.get("evidence_note", ""),
        event.get("drama_potential", ""),
        json_dumps(event),
    )


def script_generation_values(result: dict[str, Any]) -> tuple[Any, ...]:
    return (
        result.get("generation_id", ""),
        result.get("created_at", ""),
        result.get("topic", ""),
        result.get("time_range", ""),
        result.get("time_start_year"),
        result.get("time_end_year"),
        json_dumps(result.get("selected_record_ids") or []),
        json_dumps(result.get("matched_events") or []),
        json_dumps(result.get("script") or {}),
        json_dumps(result.get("subjects") or []),
        json_dumps(result.get("map_shots") or []),
        result.get("status", ""),
        result.get("message", ""),
        result.get("json_path", ""),
        result.get("markdown_path", ""),
        json_dumps(result),
    )


def record_from_row(row: sqlite3.Row) -> dict[str, Any]:
    record = json_loads(row["raw_json"], default={})
    record.update(
        {
            "record_id": row["record_id"],
            "parsed_at": row["parsed_at"],
            "book_name": row["book_name"],
            "source_relative_path": row["source_relative_path"],
            "page_count": row["page_count"],
            "total_words": row["total_words"],
            "chapter_count": row["chapter_count"],
            "excluded_count": row["excluded_count"],
            "refinement_status": row["refinement_status"],
            "refinement_message": row["refinement_message"],
            "refined_chapter_count": row["refined_chapter_count"],
            "timeline_status": row["timeline_status"],
            "timeline_message": row["timeline_message"],
            "timeline_event_count": row["timeline_event_count"],
            "timeline_url": row["timeline_url"],
            "output_relative_path": row["output_relative_path"],
            "detail_url": row["detail_url"],
            "links": json_loads(row["links_json"], default={}),
        }
    )
    return record


def chapter_from_row(row: sqlite3.Row) -> dict[str, Any]:
    chapter = json_loads(row["raw_json"], default={})
    chapter.update(
        {
            "record_id": row["record_id"],
            "chapter_id": row["chapter_id"],
            "section_id": row["section_id"],
            "title": row["title"],
            "start_page": row["start_page"],
            "end_page": row["end_page"],
            "kind": row["kind"],
            "include_in_analysis": bool(row["include_in_analysis"]),
            "pdf_path": row["pdf_path"],
            "text_path": row["text_path"],
            "word_count": row["word_count"],
            "source_format": row["source_format"],
            "source_path": row["source_path"],
            "reader_status": row["reader_status"],
            "reader_message": row["reader_message"],
            "reader_json_link": row["reader_json_link"],
            "reader_markdown_link": row["reader_markdown_link"],
            "reader_html_link": row["reader_html_link"],
        }
    )
    return chapter


def section_from_row(row: sqlite3.Row) -> dict[str, Any]:
    section = json_loads(row["raw_json"], default={})
    section.update(
        {
            "record_id": row["record_id"],
            "section_id": row["section_id"],
            "title": row["title"],
            "start_page": row["start_page"],
            "end_page": row["end_page"],
            "kind": row["kind"],
            "include_in_analysis": bool(row["include_in_analysis"]),
        }
    )
    return section


def event_from_row(row: sqlite3.Row) -> dict[str, Any]:
    event = json_loads(row["raw_json"], default={})
    event.update(
        {
            "record_id": row["record_id"],
            "event_id": row["event_id"],
            "chapter_id": row["chapter_id"],
            "title": row["title"],
            "content": row["content"],
            "time_label": row["time_label"],
            "time_start_year": row["time_start_year"],
            "time_end_year": row["time_end_year"],
            "time_precision": row["time_precision"],
            "place_label": row["place_label"],
            "place_scope": row["place_scope"],
            "places": json_loads(row["places_json"], default=[]),
            "source_pages": json_loads(row["source_pages_json"], default=[]),
            "importance": row["importance"],
            "confidence": row["confidence"],
            "evidence_note": row["evidence_note"],
            "drama_potential": row["drama_potential"],
        }
    )
    return event


def script_generation_from_row(row: sqlite3.Row) -> dict[str, Any]:
    result = json_loads(row["raw_json"], default={})
    script = json_loads(row["script_json"], default={})
    subjects = json_loads(row["subjects_json"], default=[])
    map_shots = json_loads(row["map_shots_json"], default=[])
    matched_events = json_loads(row["matched_events_json"], default=[])
    result.update(
        {
            "generation_id": row["generation_id"],
            "created_at": row["created_at"],
            "topic": row["topic"],
            "time_range": row["time_range"],
            "time_start_year": row["time_start_year"],
            "time_end_year": row["time_end_year"],
            "selected_record_ids": json_loads(row["selected_record_ids_json"], default=[]),
            "matched_events": matched_events,
            "matched_event_count": len(matched_events),
            "script": script,
            "subjects": subjects,
            "map_shots": map_shots,
            "status": row["status"],
            "message": row["message"],
            "json_path": row["json_path"],
            "markdown_path": row["markdown_path"],
            "script_title": script.get("title", row["topic"]) if isinstance(script, dict) else row["topic"],
            "subject_count": len(subjects) if isinstance(subjects, list) else 0,
            "map_shot_count": len(map_shots) if isinstance(map_shots, list) else 0,
            "places": places_from_map_shots(map_shots),
        }
    )
    return result


def script_assistant_message_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "message_id": row["message_id"],
        "generation_id": row["generation_id"],
        "role": row["role"],
        "content": row["content"],
        "selection": row["selection_text"],
        "result": json_loads(row["result_json"], default={}),
        "contexts": json_loads(row["contexts_json"], default=[]),
        "created_at": row["created_at"],
    }


def script_edit_patch_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "patch_id": row["patch_id"],
        "generation_id": row["generation_id"],
        "selection": row["selection_text"],
        "replacement": row["replacement_text"],
        "answer": row["answer"],
        "status": row["status"],
        "created_at": row["created_at"],
        "applied_at": row["applied_at"],
    }


def selection_match_key(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"\[\d+\]", "", text)
    text = re.sub(r"\s+", "", text)
    return text.strip()


def selections_match(left: str, right: str) -> bool:
    left_key = selection_match_key(left)
    right_key = selection_match_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    shorter, longer = sorted((left_key, right_key), key=len)
    return len(shorter) >= 8 and shorter in longer


def places_from_map_shots(map_shots: Any) -> list[str]:
    places: list[str] = []
    if not isinstance(map_shots, list):
        return places
    for shot in map_shots:
        if not isinstance(shot, dict):
            continue
        for place in shot.get("places") or []:
            place_text = str(place).strip()
            if place_text and place_text not in places:
                places.append(place_text)
    return places


def read_timeline_events(timeline_data: dict[str, Any]) -> list[dict[str, Any]]:
    direct_events = timeline_data.get("events")
    if isinstance(direct_events, list):
        return direct_events
    timeline_path = timeline_data.get("timeline_json_path")
    if not timeline_path:
        return []
    path = Path(timeline_path)
    if not path.exists():
        return []
    payload = json_loads(path.read_text(encoding="utf-8"), default={})
    events = payload.get("events") if isinstance(payload, dict) else []
    return events if isinstance(events, list) else []


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value: str, default):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default
