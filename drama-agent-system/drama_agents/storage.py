from __future__ import annotations

import json
import hashlib
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


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
                    conversation_id TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    selection_text TEXT NOT NULL DEFAULT '',
                    reference_selection_text TEXT NOT NULL DEFAULT '',
                    intent TEXT NOT NULL DEFAULT '',
                    focus_action TEXT NOT NULL DEFAULT '',
                    patch_id INTEGER,
                    selection_hash TEXT NOT NULL DEFAULT '',
                    reference_selection_hash TEXT NOT NULL DEFAULT '',
                    paragraph_id TEXT NOT NULL DEFAULT '',
                    start_offset INTEGER,
                    end_offset INTEGER,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    contexts_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS script_assistant_conversations (
                    conversation_id TEXT PRIMARY KEY,
                    generation_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_message_preview TEXT NOT NULL DEFAULT '',
                    message_count INTEGER NOT NULL DEFAULT 0,
                    title_manual INTEGER NOT NULL DEFAULT 0,
                    is_archived INTEGER NOT NULL DEFAULT 0,
                    session_summary TEXT NOT NULL DEFAULT '',
                    style_preferences_json TEXT NOT NULL DEFAULT '[]',
                    active_patch_id INTEGER,
                    active_selection_id TEXT NOT NULL DEFAULT '',
                    active_selection_text TEXT NOT NULL DEFAULT '',
                    active_selection_hash TEXT NOT NULL DEFAULT '',
                    active_paragraph_id TEXT NOT NULL DEFAULT '',
                    active_start_offset INTEGER,
                    active_end_offset INTEGER,
                    active_focus_reason TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS script_edit_patches (
                    patch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    generation_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL DEFAULT '',
                    selection_text TEXT NOT NULL,
                    replacement_text TEXT NOT NULL,
                    selection_hash TEXT NOT NULL DEFAULT '',
                    paragraph_id TEXT NOT NULL DEFAULT '',
                    start_offset INTEGER,
                    end_offset INTEGER,
                    article_version_hash TEXT NOT NULL DEFAULT '',
                    answer TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    applied_at TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS script_assistant_state (
                    generation_id TEXT PRIMARY KEY,
                    active_intent TEXT NOT NULL DEFAULT '',
                    active_selection_id TEXT NOT NULL DEFAULT '',
                    active_patch_id INTEGER,
                    article_version_hash TEXT NOT NULL DEFAULT '',
                    session_summary TEXT NOT NULL DEFAULT '',
                    style_preferences_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS visual_subjects (
                    subject_id TEXT PRIMARY KEY,
                    canonical_name TEXT NOT NULL,
                    pinyin_key TEXT NOT NULL DEFAULT '',
                    first_letter TEXT NOT NULL DEFAULT '',
                    subject_type TEXT NOT NULL DEFAULT '',
                    short_description TEXT NOT NULL DEFAULT '',
                    visual_identity_json TEXT NOT NULL DEFAULT '{}',
                    consistency_rules_json TEXT NOT NULL DEFAULT '{}',
                    negative_rules_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'draft',
                    anchor_asset_id TEXT NOT NULL DEFAULT '',
                    visual_prompt TEXT NOT NULL DEFAULT '',
                    negative_prompt TEXT NOT NULL DEFAULT '',
                    workflow_name TEXT NOT NULL DEFAULT '',
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS script_visual_subjects (
                    generation_id TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    role_in_script TEXT NOT NULL DEFAULT '',
                    importance INTEGER NOT NULL DEFAULT 0,
                    first_appearance TEXT NOT NULL DEFAULT '',
                    evidence_text TEXT NOT NULL DEFAULT '',
                    extraction_confidence TEXT NOT NULL DEFAULT '',
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(generation_id, subject_id),
                    FOREIGN KEY (subject_id) REFERENCES visual_subjects(subject_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_material_chapters_record
                    ON material_chapters(record_id, chapter_id);
                CREATE INDEX IF NOT EXISTS idx_timeline_events_record
                    ON timeline_events(record_id, time_start_year, event_id);
                CREATE INDEX IF NOT EXISTS idx_script_generations_created
                    ON script_generations(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_script_assistant_messages_generation
                    ON script_assistant_messages(generation_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_script_assistant_conversations_generation
                    ON script_assistant_conversations(generation_id, is_archived, updated_at);
                CREATE INDEX IF NOT EXISTS idx_script_edit_patches_generation
                    ON script_edit_patches(generation_id, status, created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_visual_subjects_canonical
                    ON visual_subjects(canonical_name);
                CREATE INDEX IF NOT EXISTS idx_visual_subjects_sort
                    ON visual_subjects(first_letter, pinyin_key, canonical_name);
                CREATE INDEX IF NOT EXISTS idx_script_visual_subjects_generation
                    ON script_visual_subjects(generation_id, importance DESC, subject_id);
                """
            )
            ensure_table_columns(
                connection,
                "script_assistant_messages",
                {
                    "conversation_id": "TEXT NOT NULL DEFAULT ''",
                    "reference_selection_text": "TEXT NOT NULL DEFAULT ''",
                    "intent": "TEXT NOT NULL DEFAULT ''",
                    "focus_action": "TEXT NOT NULL DEFAULT ''",
                    "patch_id": "INTEGER",
                    "selection_hash": "TEXT NOT NULL DEFAULT ''",
                    "reference_selection_hash": "TEXT NOT NULL DEFAULT ''",
                    "paragraph_id": "TEXT NOT NULL DEFAULT ''",
                    "start_offset": "INTEGER",
                    "end_offset": "INTEGER",
                },
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_script_assistant_messages_conversation
                    ON script_assistant_messages(conversation_id, message_id)
                """
            )
            ensure_table_columns(
                connection,
                "script_assistant_conversations",
                {
                    "title_manual": "INTEGER NOT NULL DEFAULT 0",
                    "active_selection_text": "TEXT NOT NULL DEFAULT ''",
                    "active_selection_hash": "TEXT NOT NULL DEFAULT ''",
                    "active_paragraph_id": "TEXT NOT NULL DEFAULT ''",
                    "active_start_offset": "INTEGER",
                    "active_end_offset": "INTEGER",
                    "active_focus_reason": "TEXT NOT NULL DEFAULT ''",
                },
            )
            ensure_table_columns(
                connection,
                "script_edit_patches",
                {
                    "conversation_id": "TEXT NOT NULL DEFAULT ''",
                    "selection_hash": "TEXT NOT NULL DEFAULT ''",
                    "paragraph_id": "TEXT NOT NULL DEFAULT ''",
                    "start_offset": "INTEGER",
                    "end_offset": "INTEGER",
                    "article_version_hash": "TEXT NOT NULL DEFAULT ''",
                },
            )
            ensure_table_columns(
                connection,
                "visual_subjects",
                {
                    "visual_prompt": "TEXT NOT NULL DEFAULT ''",
                    "negative_prompt": "TEXT NOT NULL DEFAULT ''",
                    "workflow_name": "TEXT NOT NULL DEFAULT ''",
                    "raw_json": "TEXT NOT NULL DEFAULT '{}'",
                },
            )
            self.ensure_legacy_script_assistant_conversations(connection)

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

    def save_visual_subject_extraction(
        self,
        generation_id: str,
        extraction: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not self.find_script_generation(generation_id):
            raise KeyError(generation_id)
        raw_subjects = extraction.get("subjects") if isinstance(extraction, dict) else []
        subjects = [normalize_visual_subject_payload(subject) for subject in raw_subjects or [] if isinstance(subject, dict)]
        with self.connect() as connection:
            connection.execute("DELETE FROM script_visual_subjects WHERE generation_id = ?", (generation_id,))
            for subject in subjects:
                existing = connection.execute(
                    "SELECT subject_id FROM visual_subjects WHERE canonical_name = ?",
                    (subject["canonical_name"],),
                ).fetchone()
                if existing:
                    subject["subject_id"] = existing["subject_id"]
                connection.execute(
                    """
                    INSERT INTO visual_subjects (
                        subject_id, canonical_name, pinyin_key, first_letter, subject_type,
                        short_description, visual_identity_json, consistency_rules_json,
                        negative_rules_json, status, anchor_asset_id, visual_prompt,
                        negative_prompt, workflow_name, raw_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(subject_id) DO UPDATE SET
                        canonical_name=excluded.canonical_name,
                        pinyin_key=excluded.pinyin_key,
                        first_letter=excluded.first_letter,
                        subject_type=excluded.subject_type,
                        short_description=excluded.short_description,
                        visual_identity_json=excluded.visual_identity_json,
                        consistency_rules_json=excluded.consistency_rules_json,
                        negative_rules_json=excluded.negative_rules_json,
                        status=excluded.status,
                        anchor_asset_id=excluded.anchor_asset_id,
                        visual_prompt=excluded.visual_prompt,
                        negative_prompt=excluded.negative_prompt,
                        workflow_name=excluded.workflow_name,
                        raw_json=excluded.raw_json,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    visual_subject_values(subject),
                )
                connection.execute(
                    """
                    INSERT INTO script_visual_subjects (
                        generation_id, subject_id, role_in_script, importance,
                        first_appearance, evidence_text, extraction_confidence, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(generation_id, subject_id) DO UPDATE SET
                        role_in_script=excluded.role_in_script,
                        importance=excluded.importance,
                        first_appearance=excluded.first_appearance,
                        evidence_text=excluded.evidence_text,
                        extraction_confidence=excluded.extraction_confidence,
                        raw_json=excluded.raw_json
                    """,
                    script_visual_subject_values(generation_id, subject),
                )
        return self.list_script_visual_subjects(generation_id)

    def list_visual_subjects(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    visual_subjects.*,
                    COUNT(DISTINCT script_visual_subjects.generation_id) AS script_count
                FROM visual_subjects
                LEFT JOIN script_visual_subjects
                    ON script_visual_subjects.subject_id = visual_subjects.subject_id
                GROUP BY visual_subjects.subject_id
                ORDER BY visual_subjects.first_letter, visual_subjects.pinyin_key, visual_subjects.canonical_name
                """
            ).fetchall()
        return [visual_subject_from_row(row) for row in rows]

    def find_visual_subject(self, subject_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    visual_subjects.*,
                    COUNT(DISTINCT script_visual_subjects.generation_id) AS script_count
                FROM visual_subjects
                LEFT JOIN script_visual_subjects
                    ON script_visual_subjects.subject_id = visual_subjects.subject_id
                WHERE visual_subjects.subject_id = ?
                GROUP BY visual_subjects.subject_id
                """,
                (subject_id,),
            ).fetchone()
            if not row:
                return None
            appearances = connection.execute(
                """
                SELECT
                    script_visual_subjects.*,
                    script_generations.topic,
                    script_generations.created_at,
                    script_generations.time_range
                FROM script_visual_subjects
                LEFT JOIN script_generations
                    ON script_generations.generation_id = script_visual_subjects.generation_id
                WHERE script_visual_subjects.subject_id = ?
                ORDER BY script_generations.created_at DESC, script_visual_subjects.importance DESC
                """,
                (subject_id,),
            ).fetchall()
        subject = visual_subject_from_row(row)
        subject["appearances"] = [visual_subject_appearance_from_row(appearance) for appearance in appearances]
        return subject

    def list_script_visual_subjects(self, generation_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    script_visual_subjects.generation_id,
                    script_visual_subjects.role_in_script,
                    script_visual_subjects.importance,
                    script_visual_subjects.first_appearance,
                    script_visual_subjects.evidence_text,
                    script_visual_subjects.extraction_confidence,
                    script_visual_subjects.raw_json AS script_subject_raw_json,
                    visual_subjects.*,
                    1 AS script_count
                FROM script_visual_subjects
                JOIN visual_subjects
                    ON visual_subjects.subject_id = script_visual_subjects.subject_id
                WHERE script_visual_subjects.generation_id = ?
                ORDER BY script_visual_subjects.importance DESC, visual_subjects.pinyin_key
                """,
                (generation_id,),
            ).fetchall()
        return [script_visual_subject_from_row(row) for row in rows]

    def update_visual_subject(self, subject_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        current = self.find_visual_subject(subject_id)
        if not current:
            raise KeyError(subject_id)
        merged = dict(current)
        for key in (
            "canonical_name",
            "subject_type",
            "short_description",
            "visual_identity",
            "consistency_rules",
            "negative_rules",
            "status",
            "anchor_asset_id",
            "visual_prompt",
            "negative_prompt",
            "workflow_name",
        ):
            if key in updates:
                merged[key] = updates[key]
        normalized = normalize_visual_subject_payload(merged)
        normalized["subject_id"] = subject_id
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE visual_subjects
                SET canonical_name = ?,
                    pinyin_key = ?,
                    first_letter = ?,
                    subject_type = ?,
                    short_description = ?,
                    visual_identity_json = ?,
                    consistency_rules_json = ?,
                    negative_rules_json = ?,
                    status = ?,
                    anchor_asset_id = ?,
                    visual_prompt = ?,
                    negative_prompt = ?,
                    workflow_name = ?,
                    raw_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE subject_id = ?
                """,
                (*visual_subject_values(normalized)[1:], subject_id),
            )
        subject = self.find_visual_subject(subject_id)
        if not subject:
            raise KeyError(subject_id)
        return subject

    def ensure_legacy_script_assistant_conversations(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT generation_id, COUNT(*) AS message_count, MAX(created_at) AS updated_at
            FROM script_assistant_messages
            WHERE conversation_id = ''
            GROUP BY generation_id
            """
        ).fetchall()
        for row in rows:
            generation_id = row["generation_id"]
            conversation_id = f"legacy-{generation_id}"
            latest = connection.execute(
                """
                SELECT content, created_at FROM script_assistant_messages
                WHERE generation_id = ? AND conversation_id = ''
                ORDER BY message_id DESC
                LIMIT 1
                """,
                (generation_id,),
            ).fetchone()
            first = connection.execute(
                """
                SELECT created_at FROM script_assistant_messages
                WHERE generation_id = ? AND conversation_id = ''
                ORDER BY message_id ASC
                LIMIT 1
                """,
                (generation_id,),
            ).fetchone()
            created_at = first["created_at"] if first else current_timestamp()
            updated_at = latest["created_at"] if latest else row["updated_at"] or created_at
            preview = preview_text(latest["content"] if latest else "")
            connection.execute(
                """
                INSERT INTO script_assistant_conversations (
                    conversation_id, generation_id, title, created_at, updated_at,
                    last_message_preview, message_count, is_archived
                ) VALUES (?, ?, '旧对话', ?, ?, ?, ?, 0)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    generation_id=excluded.generation_id,
                    updated_at=excluded.updated_at,
                    last_message_preview=excluded.last_message_preview,
                    message_count=excluded.message_count
                """,
                (conversation_id, generation_id, created_at, updated_at, preview, int(row["message_count"] or 0)),
            )
            connection.execute(
                """
                UPDATE script_assistant_messages
                SET conversation_id = ?
                WHERE generation_id = ? AND conversation_id = ''
                """,
                (conversation_id, generation_id),
            )

    def create_script_assistant_conversation(self, generation_id: str, *, title: str = "") -> dict[str, Any]:
        conversation_id = f"conv-{uuid4().hex}"
        now = current_timestamp()
        clean_title = title.strip() or "新对话"
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO script_assistant_conversations (
                    conversation_id, generation_id, title, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, generation_id, clean_title, now, now),
            )
        conversation = self.find_script_assistant_conversation(generation_id, conversation_id)
        if not conversation:
            raise KeyError(conversation_id)
        return conversation

    def list_script_assistant_conversations(
        self,
        generation_id: str,
        *,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        with self.connect() as connection:
            self.ensure_legacy_script_assistant_conversations(connection)
            if include_archived:
                rows = connection.execute(
                    """
                    SELECT * FROM script_assistant_conversations
                    WHERE generation_id = ?
                    ORDER BY updated_at DESC, conversation_id DESC
                    """,
                    (generation_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM script_assistant_conversations
                    WHERE generation_id = ? AND is_archived = 0
                    ORDER BY updated_at DESC, conversation_id DESC
                    """,
                    (generation_id,),
                ).fetchall()
        return [script_assistant_conversation_from_row(row) for row in rows]

    def find_script_assistant_conversation(
        self,
        generation_id: str,
        conversation_id: str,
        *,
        include_archived: bool = False,
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            self.ensure_legacy_script_assistant_conversations(connection)
            if include_archived:
                row = connection.execute(
                    """
                    SELECT * FROM script_assistant_conversations
                    WHERE generation_id = ? AND conversation_id = ?
                    """,
                    (generation_id, conversation_id),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT * FROM script_assistant_conversations
                    WHERE generation_id = ? AND conversation_id = ? AND is_archived = 0
                    """,
                    (generation_id, conversation_id),
                ).fetchone()
        return script_assistant_conversation_from_row(row) if row else None

    def archive_script_assistant_conversation(self, generation_id: str, conversation_id: str) -> dict[str, Any]:
        now = current_timestamp()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE script_assistant_conversations
                SET is_archived = 1,
                    updated_at = ?
                WHERE generation_id = ? AND conversation_id = ?
                """,
                (now, generation_id, conversation_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(conversation_id)
        conversation = self.find_script_assistant_conversation(
            generation_id,
            conversation_id,
            include_archived=True,
        )
        if not conversation:
            raise KeyError(conversation_id)
        return conversation

    def update_script_assistant_conversation_state(
        self,
        generation_id: str,
        conversation_id: str,
        *,
        session_summary: str = "",
        style_preferences: list[str] | None = None,
        active_patch_id: int | None = None,
        active_selection_id: str = "",
        active_selection_text: str = "",
        active_selection_hash: str = "",
        active_paragraph_id: str = "",
        active_start_offset: int | None = None,
        active_end_offset: int | None = None,
        active_focus_reason: str = "",
    ) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE script_assistant_conversations
                SET session_summary = ?,
                    style_preferences_json = ?,
                    active_patch_id = ?,
                    active_selection_id = ?,
                    active_selection_text = ?,
                    active_selection_hash = ?,
                    active_paragraph_id = ?,
                    active_start_offset = ?,
                    active_end_offset = ?,
                    active_focus_reason = ?,
                    updated_at = ?
                WHERE generation_id = ? AND conversation_id = ?
                """,
                (
                    session_summary,
                    json_dumps(style_preferences or []),
                    active_patch_id,
                    active_selection_id,
                    active_selection_text,
                    active_selection_hash,
                    active_paragraph_id,
                    active_start_offset,
                    active_end_offset,
                    active_focus_reason,
                    current_timestamp(),
                    generation_id,
                    conversation_id,
                ),
            )
        conversation = self.find_script_assistant_conversation(generation_id, conversation_id)
        if not conversation:
            raise KeyError(conversation_id)
        return conversation

    def update_script_assistant_conversation_title(
        self,
        generation_id: str,
        conversation_id: str,
        title: str,
    ) -> dict[str, Any]:
        clean_title = conversation_title_from_message(title, limit=40)
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE script_assistant_conversations
                SET title = ?,
                    title_manual = 1,
                    updated_at = ?
                WHERE generation_id = ? AND conversation_id = ? AND is_archived = 0
                """,
                (clean_title, current_timestamp(), generation_id, conversation_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(conversation_id)
        conversation = self.find_script_assistant_conversation(generation_id, conversation_id)
        if not conversation:
            raise KeyError(conversation_id)
        return conversation

    def add_script_assistant_message(
        self,
        *,
        generation_id: str,
        conversation_id: str = "",
        role: str,
        content: str,
        selection: str = "",
        reference_selection: str = "",
        intent: str = "",
        focus_action: str = "",
        patch_id: int | None = None,
        selection_hash: str = "",
        reference_selection_hash: str = "",
        paragraph_id: str = "",
        start_offset: int | None = None,
        end_offset: int | None = None,
        result: dict[str, Any] | None = None,
        contexts: list[dict[str, Any]] | None = None,
    ) -> int:
        now = current_timestamp()
        with self.connect() as connection:
            if conversation_id:
                conversation = connection.execute(
                    """
                    SELECT * FROM script_assistant_conversations
                    WHERE generation_id = ? AND conversation_id = ? AND is_archived = 0
                    """,
                    (generation_id, conversation_id),
                ).fetchone()
                if not conversation:
                    raise KeyError(conversation_id)
            else:
                conversation = None
            cursor = connection.execute(
                """
                INSERT INTO script_assistant_messages (
                    generation_id, conversation_id, role, content, selection_text, reference_selection_text,
                    intent, focus_action, patch_id, selection_hash, reference_selection_hash,
                    paragraph_id, start_offset, end_offset,
                    result_json, contexts_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    generation_id,
                    conversation_id,
                    role,
                    content,
                    selection,
                    reference_selection,
                    intent,
                    focus_action,
                    patch_id,
                    selection_hash,
                    reference_selection_hash,
                    paragraph_id,
                    start_offset,
                    end_offset,
                    json_dumps(result or {}),
                    json_dumps(contexts or []),
                    now,
                ),
            )
            if conversation_id:
                message_count = int(conversation["message_count"] or 0) + 1 if conversation else 1
                title = conversation["title"] if conversation else "新对话"
                title_manual = bool(conversation["title_manual"]) if conversation else False
                if role == "user" and not title_manual and title in {"", "新对话"}:
                    title = conversation_title_from_message(content, intent=intent, paragraph_id=paragraph_id, limit=40)
                connection.execute(
                    """
                    UPDATE script_assistant_conversations
                    SET title = ?,
                        updated_at = ?,
                        last_message_preview = ?,
                        message_count = ?
                    WHERE generation_id = ? AND conversation_id = ?
                    """,
                    (
                        title,
                        now,
                        preview_text(content),
                        message_count,
                        generation_id,
                        conversation_id,
                    ),
                )
            return int(cursor.lastrowid)

    def list_script_assistant_messages(
        self,
        generation_id: str,
        *,
        conversation_id: str | None = None,
        limit: int | None = 12,
    ) -> list[dict[str, Any]]:
        with self.connect() as connection:
            self.ensure_legacy_script_assistant_conversations(connection)
            if conversation_id is not None:
                query = """
                    SELECT * FROM script_assistant_messages
                    WHERE generation_id = ? AND conversation_id = ?
                    ORDER BY message_id DESC
                """
                params: tuple[Any, ...] = (generation_id, conversation_id)
            else:
                query = """
                    SELECT * FROM script_assistant_messages
                    WHERE generation_id = ?
                    ORDER BY message_id DESC
                """
                params = (generation_id,)
            if limit is not None:
                query = f"{query} LIMIT ?"
                params = (*params, limit)
            rows = connection.execute(query, params).fetchall()
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
            self.ensure_legacy_script_assistant_conversations(connection)
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
        conversation_id: str = "",
        selection: str,
        replacement: str,
        answer: str = "",
        selection_hash: str = "",
        paragraph_id: str = "",
        start_offset: int | None = None,
        end_offset: int | None = None,
        article_version_hash: str = "",
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO script_edit_patches (
                    generation_id, conversation_id, selection_text, replacement_text, selection_hash,
                    paragraph_id, start_offset, end_offset, article_version_hash, answer, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    generation_id,
                    conversation_id,
                    selection,
                    replacement,
                    selection_hash,
                    paragraph_id,
                    start_offset,
                    end_offset,
                    article_version_hash,
                    answer,
                ),
            )
            return int(cursor.lastrowid)

    def find_script_edit_patch(self, patch_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM script_edit_patches WHERE patch_id = ?",
                (patch_id,),
            ).fetchone()
        return script_edit_patch_from_row(row) if row else None

    def latest_pending_script_edit_patch(
        self,
        generation_id: str,
        *,
        conversation_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            if conversation_id is None:
                row = connection.execute(
                    """
                    SELECT * FROM script_edit_patches
                    WHERE generation_id = ? AND status = 'pending'
                    ORDER BY patch_id DESC
                    LIMIT 1
                    """,
                    (generation_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT * FROM script_edit_patches
                    WHERE generation_id = ? AND conversation_id = ? AND status = 'pending'
                    ORDER BY patch_id DESC
                    LIMIT 1
                    """,
                    (generation_id, conversation_id),
                ).fetchone()
        return script_edit_patch_from_row(row) if row else None

    def mark_generation_pending_patches_stale(self, generation_id: str, *, except_patch_id: int | None = None) -> None:
        with self.connect() as connection:
            if except_patch_id is None:
                connection.execute(
                    """
                    UPDATE script_edit_patches
                    SET status = 'stale'
                    WHERE generation_id = ? AND status = 'pending'
                    """,
                    (generation_id,),
                )
            else:
                connection.execute(
                    """
                    UPDATE script_edit_patches
                    SET status = 'stale'
                    WHERE generation_id = ? AND status = 'pending' AND patch_id <> ?
                    """,
                    (generation_id, except_patch_id),
                )

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

    def mark_script_edit_patch_status(self, patch_id: int, status: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE script_edit_patches
                SET status = ?,
                    applied_at = CASE WHEN ? = 'applied' THEN CURRENT_TIMESTAMP ELSE applied_at END
                WHERE patch_id = ?
                """,
                (status, status, patch_id),
            )

    def get_script_assistant_state(self, generation_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM script_assistant_state WHERE generation_id = ?",
                (generation_id,),
            ).fetchone()
        if row:
            return script_assistant_state_from_row(row)
        return {
            "generation_id": generation_id,
            "active_intent": "",
            "active_selection_id": "",
            "active_patch_id": None,
            "article_version_hash": "",
            "session_summary": "",
            "style_preferences": [],
            "updated_at": "",
        }

    def upsert_script_assistant_state(
        self,
        generation_id: str,
        *,
        active_intent: str = "",
        active_selection_id: str = "",
        active_patch_id: int | None = None,
        article_version_hash: str = "",
        session_summary: str = "",
        style_preferences: list[str] | None = None,
    ) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO script_assistant_state (
                    generation_id, active_intent, active_selection_id, active_patch_id,
                    article_version_hash, session_summary, style_preferences_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(generation_id) DO UPDATE SET
                    active_intent=excluded.active_intent,
                    active_selection_id=excluded.active_selection_id,
                    active_patch_id=excluded.active_patch_id,
                    article_version_hash=excluded.article_version_hash,
                    session_summary=excluded.session_summary,
                    style_preferences_json=excluded.style_preferences_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    generation_id,
                    active_intent,
                    active_selection_id,
                    active_patch_id,
                    article_version_hash,
                    session_summary,
                    json_dumps(style_preferences or []),
                ),
            )
        return self.get_script_assistant_state(generation_id)


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


def normalize_visual_subject_payload(subject: dict[str, Any]) -> dict[str, Any]:
    raw_name = str(subject.get("canonical_name") or subject.get("name") or "").strip()
    canonical_name = canonical_visual_subject_name(raw_name, subject.get("aliases") or [])
    visual_identity = subject.get("visual_identity")
    if not isinstance(visual_identity, dict):
        visual_identity = {}
    consistency_rules = subject.get("consistency_rules")
    if not isinstance(consistency_rules, dict):
        consistency_rules = {}
    negative_rules = subject.get("negative_rules")
    if negative_rules is None:
        negative_rules = consistency_rules.get("avoid") or []
    if not isinstance(negative_rules, list):
        negative_rules = [str(negative_rules)]
    subject_type = str(subject.get("subject_type") or subject.get("type") or "").strip()
    short_description = str(
        subject.get("short_description")
        or subject.get("description")
        or subject.get("role_in_script")
        or ""
    ).strip()
    pinyin_key = subject_pinyin_key(canonical_name)
    first_letter = (pinyin_key[:1] or "Z").upper()
    normalized = dict(subject)
    normalized.update(
        {
            "subject_id": str(subject.get("subject_id") or stable_visual_subject_id(canonical_name)),
            "canonical_name": canonical_name,
            "pinyin_key": pinyin_key,
            "first_letter": first_letter,
            "subject_type": subject_type,
            "short_description": short_description,
            "visual_identity": visual_identity,
            "consistency_rules": consistency_rules,
            "negative_rules": [str(item) for item in negative_rules if str(item).strip()],
            "status": str(subject.get("status") or "draft"),
            "anchor_asset_id": str(subject.get("anchor_asset_id") or ""),
            "visual_prompt": str(subject.get("visual_prompt") or build_visual_prompt(canonical_name, visual_identity)),
            "negative_prompt": str(
                subject.get("negative_prompt")
                or "，".join([str(item) for item in consistency_rules.get("avoid", []) if str(item).strip()])
            ),
            "workflow_name": str(subject.get("workflow_name") or "subject_anchor_v1"),
            "role_in_script": str(subject.get("role_in_script") or ""),
            "importance": int(subject.get("importance") or 0),
            "first_appearance": str(subject.get("first_appearance") or ""),
            "evidence_text": str(subject.get("evidence_text") or subject.get("first_appearance") or ""),
            "extraction_confidence": str(subject.get("extraction_confidence") or ""),
        }
    )
    return normalized


def visual_subject_values(subject: dict[str, Any]) -> tuple[Any, ...]:
    return (
        subject["subject_id"],
        subject["canonical_name"],
        subject["pinyin_key"],
        subject["first_letter"],
        subject["subject_type"],
        subject["short_description"],
        json_dumps(subject.get("visual_identity") or {}),
        json_dumps(subject.get("consistency_rules") or {}),
        json_dumps(subject.get("negative_rules") or []),
        subject.get("status", "draft"),
        subject.get("anchor_asset_id", ""),
        subject.get("visual_prompt", ""),
        subject.get("negative_prompt", ""),
        subject.get("workflow_name", ""),
        json_dumps(subject),
    )


def script_visual_subject_values(generation_id: str, subject: dict[str, Any]) -> tuple[Any, ...]:
    return (
        generation_id,
        subject["subject_id"],
        subject.get("role_in_script", ""),
        int(subject.get("importance") or 0),
        subject.get("first_appearance", ""),
        subject.get("evidence_text", ""),
        subject.get("extraction_confidence", ""),
        json_dumps(subject),
    )


def visual_subject_from_row(row: sqlite3.Row) -> dict[str, Any]:
    raw = json_loads(row["raw_json"], default={})
    visual_identity = json_loads(row["visual_identity_json"], default={})
    consistency_rules = json_loads(row["consistency_rules_json"], default={})
    negative_rules = json_loads(row["negative_rules_json"], default=[])
    subject = dict(raw)
    subject.update(
        {
            "subject_id": row["subject_id"],
            "canonical_name": row["canonical_name"],
            "pinyin_key": row["pinyin_key"],
            "first_letter": row["first_letter"],
            "subject_type": row["subject_type"],
            "short_description": row["short_description"],
            "visual_identity": visual_identity,
            "consistency_rules": consistency_rules,
            "negative_rules": negative_rules,
            "status": row["status"],
            "anchor_asset_id": row["anchor_asset_id"],
            "visual_prompt": row["visual_prompt"],
            "negative_prompt": row["negative_prompt"],
            "workflow_name": row["workflow_name"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "script_count": int(row["script_count"] or 0) if "script_count" in row.keys() else 0,
            "has_visual_identity": bool(visual_identity),
            "has_anchor_asset": bool(row["anchor_asset_id"]),
        }
    )
    return subject


def script_visual_subject_from_row(row: sqlite3.Row) -> dict[str, Any]:
    subject = visual_subject_from_row(row)
    raw = json_loads(row["script_subject_raw_json"], default={})
    subject.update(
        {
            "generation_id": row["generation_id"],
            "role_in_script": row["role_in_script"],
            "importance": row["importance"],
            "first_appearance": row["first_appearance"],
            "evidence_text": row["evidence_text"],
            "extraction_confidence": row["extraction_confidence"],
            "raw": raw,
            "is_global_subject": True,
        }
    )
    return subject


def visual_subject_appearance_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "generation_id": row["generation_id"],
        "topic": row["topic"] or "",
        "created_at": row["created_at"] or "",
        "time_range": row["time_range"] or "",
        "role_in_script": row["role_in_script"],
        "importance": row["importance"],
        "first_appearance": row["first_appearance"],
        "evidence_text": row["evidence_text"],
        "extraction_confidence": row["extraction_confidence"],
        "raw": json_loads(row["raw_json"], default={}),
    }


def stable_visual_subject_id(canonical_name: str) -> str:
    digest = hashlib.sha1(canonical_name.encode("utf-8")).hexdigest()[:12]
    return f"vs-{digest}"


def canonical_visual_subject_name(name: str, aliases: Any = None) -> str:
    clean_name = str(name or "").strip()
    alias_values = aliases if isinstance(aliases, list) else []
    values = {clean_name, *[str(alias).strip() for alias in alias_values]}
    sapiens_aliases = {"早期智人", "现代智人的祖先", "现代人类的祖先", "人类祖先"}
    if values & sapiens_aliases:
        return "智人"
    return clean_name


PINYIN_OVERRIDES = {
    "阿拉伯半岛智人迁徙群体": "alabobandaozhirenqianxiqunti",
    "丹尼索瓦人": "dannisuowaren",
    "尼安德特人": "niandeteren",
    "早期智人群体": "zaoqizhirenqunti",
    "智人": "zhiren",
    "智人部落老者": "zhirenbuluolaozhe",
    "智人猎人群体": "zhirenlierenqunti",
    "直立人": "zhiliren",
}


def subject_pinyin_key(name: str) -> str:
    clean_name = str(name or "").strip()
    if clean_name in PINYIN_OVERRIDES:
        return PINYIN_OVERRIDES[clean_name]
    if clean_name and clean_name[0].isascii():
        return re.sub(r"[^0-9a-z]+", "", clean_name.lower()) or "z"
    return f"z{clean_name}"


def build_visual_prompt(canonical_name: str, visual_identity: dict[str, Any]) -> str:
    parts = [
        canonical_name,
        str(visual_identity.get("era") or ""),
        str(visual_identity.get("region") or ""),
        str(visual_identity.get("appearance") or ""),
        str(visual_identity.get("clothing") or ""),
        str(visual_identity.get("body_language") or ""),
        str(visual_identity.get("group_composition") or ""),
    ]
    return "，".join(part for part in parts if part)


def script_assistant_message_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "message_id": row["message_id"],
        "generation_id": row["generation_id"],
        "conversation_id": row["conversation_id"],
        "role": row["role"],
        "content": row["content"],
        "selection": row["selection_text"],
        "reference_selection": row["reference_selection_text"],
        "intent": row["intent"],
        "focus_action": row["focus_action"],
        "patch_id": row["patch_id"],
        "selection_hash": row["selection_hash"],
        "reference_selection_hash": row["reference_selection_hash"],
        "paragraph_id": row["paragraph_id"],
        "start_offset": row["start_offset"],
        "end_offset": row["end_offset"],
        "result": json_loads(row["result_json"], default={}),
        "contexts": json_loads(row["contexts_json"], default=[]),
        "created_at": row["created_at"],
    }


def script_assistant_conversation_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "conversation_id": row["conversation_id"],
        "generation_id": row["generation_id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_message_preview": row["last_message_preview"],
        "message_count": row["message_count"],
        "title_manual": bool(row["title_manual"]),
        "is_archived": bool(row["is_archived"]),
        "session_summary": row["session_summary"],
        "style_preferences": json_loads(row["style_preferences_json"], default=[]),
        "active_patch_id": row["active_patch_id"],
        "active_selection_id": row["active_selection_id"],
        "active_selection_text": row["active_selection_text"],
        "active_selection_hash": row["active_selection_hash"],
        "active_paragraph_id": row["active_paragraph_id"],
        "active_start_offset": row["active_start_offset"],
        "active_end_offset": row["active_end_offset"],
        "active_focus_reason": row["active_focus_reason"],
    }


def script_edit_patch_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "patch_id": row["patch_id"],
        "generation_id": row["generation_id"],
        "conversation_id": row["conversation_id"],
        "selection": row["selection_text"],
        "replacement": row["replacement_text"],
        "selection_hash": row["selection_hash"],
        "paragraph_id": row["paragraph_id"],
        "start_offset": row["start_offset"],
        "end_offset": row["end_offset"],
        "article_version_hash": row["article_version_hash"],
        "answer": row["answer"],
        "status": row["status"],
        "created_at": row["created_at"],
        "applied_at": row["applied_at"],
    }


def script_assistant_state_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "generation_id": row["generation_id"],
        "active_intent": row["active_intent"],
        "active_selection_id": row["active_selection_id"],
        "active_patch_id": row["active_patch_id"],
        "article_version_hash": row["article_version_hash"],
        "session_summary": row["session_summary"],
        "style_preferences": json_loads(row["style_preferences_json"], default=[]),
        "updated_at": row["updated_at"],
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


def ensure_table_columns(connection: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, ddl in columns.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def current_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


def preview_text(value: str, *, limit: int = 80) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def conversation_title_from_message(content: str, *, intent: str = "", paragraph_id: str = "", limit: int = 40) -> str:
    paragraph = paragraph_number(paragraph_id)
    if intent == "EXPLAIN_SELECTION" and paragraph:
        return f"解释第 {paragraph} 段"
    if intent == "REVIEW_SELECTION" and paragraph:
        return f"评审第 {paragraph} 段"
    if intent in {"PROPOSE_EDIT", "REVISE_PENDING"} and paragraph:
        return f"修改第 {paragraph} 段"
    clean = preview_text(content, limit=limit)
    return clean or "新对话"


def paragraph_number(paragraph_id: str) -> str:
    match = re.search(r"(\d+)$", str(paragraph_id or ""))
    return match.group(1) if match else ""
