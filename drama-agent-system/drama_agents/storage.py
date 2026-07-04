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
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y%m%d %H%M','now','localtime'))
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
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y%m%d %H%M','now','localtime'))
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
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y%m%d %H%M','now','localtime'))
                );

                CREATE TABLE IF NOT EXISTS script_assistant_conversations (
                    conversation_id TEXT PRIMARY KEY,
                    generation_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y%m%d %H%M','now','localtime')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y%m%d %H%M','now','localtime')),
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
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y%m%d %H%M','now','localtime')),
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
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y%m%d %H%M','now','localtime'))
                );

                CREATE TABLE IF NOT EXISTS visual_subjects (
                    subject_id TEXT PRIMARY KEY,
                    canonical_name TEXT NOT NULL,
                    visual_phase_key TEXT NOT NULL DEFAULT 'default',
                    visual_phase_label TEXT NOT NULL DEFAULT '默认阶段',
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
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y%m%d %H%M','now','localtime')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y%m%d %H%M','now','localtime'))
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

                CREATE TABLE IF NOT EXISTS visual_scenes (
                    scene_id TEXT PRIMARY KEY,
                    canonical_name TEXT NOT NULL,
                    visual_phase_key TEXT NOT NULL DEFAULT 'default',
                    visual_phase_label TEXT NOT NULL DEFAULT '默认阶段',
                    pinyin_key TEXT NOT NULL DEFAULT '',
                    first_letter TEXT NOT NULL DEFAULT '',
                    scene_type TEXT NOT NULL DEFAULT '',
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
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y%m%d %H%M','now','localtime')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y%m%d %H%M','now','localtime'))
                );

                CREATE TABLE IF NOT EXISTS script_visual_scenes (
                    generation_id TEXT NOT NULL,
                    scene_id TEXT NOT NULL,
                    role_in_script TEXT NOT NULL DEFAULT '',
                    importance INTEGER NOT NULL DEFAULT 0,
                    first_appearance TEXT NOT NULL DEFAULT '',
                    evidence_text TEXT NOT NULL DEFAULT '',
                    extraction_confidence TEXT NOT NULL DEFAULT '',
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(generation_id, scene_id),
                    FOREIGN KEY (scene_id) REFERENCES visual_scenes(scene_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS storyboards (
                    storyboard_id TEXT PRIMARY KEY,
                    generation_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    source_type TEXT NOT NULL DEFAULT 'script_generation',
                    source_filename TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'draft',
                    target_duration_sec INTEGER NOT NULL DEFAULT 0,
                    actual_duration_sec REAL,
                    shot_count INTEGER NOT NULL DEFAULT 0,
                    style_policy_json TEXT NOT NULL DEFAULT '{}',
                    missing_subject_candidates_json TEXT NOT NULL DEFAULT '[]',
                    missing_scene_candidates_json TEXT NOT NULL DEFAULT '[]',
                    review_notes_json TEXT NOT NULL DEFAULT '[]',
                    coverage_json TEXT NOT NULL DEFAULT '{}',
                    script_feedback_json TEXT NOT NULL DEFAULT '[]',
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y%m%d %H%M','now','localtime')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y%m%d %H%M','now','localtime')),
                    FOREIGN KEY (generation_id) REFERENCES script_generations(generation_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS storyboard_shots (
                    shot_id TEXT PRIMARY KEY,
                    storyboard_id TEXT NOT NULL,
                    generation_id TEXT NOT NULL,
                    shot_index INTEGER NOT NULL,
                    narration TEXT NOT NULL DEFAULT '',
                    subtitle_text TEXT NOT NULL DEFAULT '',
                    shot_type TEXT NOT NULL DEFAULT '',
                    visual_goal TEXT NOT NULL DEFAULT '',
                    scene_id TEXT NOT NULL DEFAULT '',
                    scene_name TEXT NOT NULL DEFAULT '',
                    subject_ids_json TEXT NOT NULL DEFAULT '[]',
                    subject_names_json TEXT NOT NULL DEFAULT '[]',
                    visual_elements_json TEXT NOT NULL DEFAULT '[]',
                    reference_assets_json TEXT NOT NULL DEFAULT '{}',
                    camera_json TEXT NOT NULL DEFAULT '{}',
                    duration_sec REAL NOT NULL DEFAULT 4,
                    keyframe_prompt TEXT NOT NULL DEFAULT '',
                    video_prompt TEXT NOT NULL DEFAULT '',
                    negative_prompt TEXT NOT NULL DEFAULT '',
                    fact_safety_note TEXT NOT NULL DEFAULT '',
                    asset_status TEXT NOT NULL DEFAULT 'missing_keyframe',
                    keyframe_asset_id TEXT NOT NULL DEFAULT '',
                    video_asset_id TEXT NOT NULL DEFAULT '',
                    needs_manual_review INTEGER NOT NULL DEFAULT 0,
                    source_paragraph_index INTEGER NOT NULL DEFAULT 0,
                    source_text_start INTEGER NOT NULL DEFAULT 0,
                    source_text_end INTEGER NOT NULL DEFAULT 0,
                    source_excerpt TEXT NOT NULL DEFAULT '',
                    is_supplemental INTEGER NOT NULL DEFAULT 0,
                    supplemental_reason TEXT NOT NULL DEFAULT '',
                    scene_block_id TEXT NOT NULL DEFAULT '',
                    scene_block_title TEXT NOT NULL DEFAULT '',
                    scene_block_index INTEGER NOT NULL DEFAULT 0,
                    sequence_id TEXT NOT NULL DEFAULT '',
                    sequence_title TEXT NOT NULL DEFAULT '',
                    beat_id TEXT NOT NULL DEFAULT '',
                    beat_title TEXT NOT NULL DEFAULT '',
                    prev_shot_id TEXT NOT NULL DEFAULT '',
                    next_shot_id TEXT NOT NULL DEFAULT '',
                    transition TEXT NOT NULL DEFAULT 'cut',
                    continuity_json TEXT NOT NULL DEFAULT '{}',
                    production_plan_json TEXT NOT NULL DEFAULT '{}',
                    prompt_parts_json TEXT NOT NULL DEFAULT '{}',
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y%m%d %H%M','now','localtime')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y%m%d %H%M','now','localtime')),
                    FOREIGN KEY (storyboard_id) REFERENCES storyboards(storyboard_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS storyboard_shot_asset_candidates (
                    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    storyboard_id TEXT NOT NULL,
                    shot_id TEXT NOT NULL,
                    candidate_type TEXT NOT NULL,
                    candidate_name TEXT NOT NULL,
                    candidate_status TEXT NOT NULL DEFAULT 'pending',
                    linked_entity_id TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'storyboard',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    confirmed_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y%m%d %H%M','now','localtime')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y%m%d %H%M','now','localtime')),
                    UNIQUE(shot_id, candidate_type, candidate_name),
                    FOREIGN KEY (storyboard_id) REFERENCES storyboards(storyboard_id) ON DELETE CASCADE
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
                CREATE INDEX IF NOT EXISTS idx_visual_subjects_sort
                    ON visual_subjects(first_letter, pinyin_key, canonical_name);
                CREATE INDEX IF NOT EXISTS idx_script_visual_subjects_generation
                    ON script_visual_subjects(generation_id, importance DESC, subject_id);
                CREATE INDEX IF NOT EXISTS idx_visual_scenes_sort
                    ON visual_scenes(first_letter, pinyin_key, canonical_name);
                CREATE INDEX IF NOT EXISTS idx_script_visual_scenes_generation
                    ON script_visual_scenes(generation_id, importance DESC, scene_id);
                CREATE INDEX IF NOT EXISTS idx_storyboards_generation
                    ON storyboards(generation_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_storyboard_shots_storyboard
                    ON storyboard_shots(storyboard_id, shot_index);
                CREATE INDEX IF NOT EXISTS idx_storyboard_shot_candidates_storyboard
                    ON storyboard_shot_asset_candidates(storyboard_id, shot_id, candidate_status);
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
                    "visual_phase_key": "TEXT NOT NULL DEFAULT 'default'",
                    "visual_phase_label": "TEXT NOT NULL DEFAULT '默认阶段'",
                    "visual_prompt": "TEXT NOT NULL DEFAULT ''",
                    "negative_prompt": "TEXT NOT NULL DEFAULT ''",
                    "workflow_name": "TEXT NOT NULL DEFAULT ''",
                    "raw_json": "TEXT NOT NULL DEFAULT '{}'",
                },
            )
            ensure_table_columns(
                connection,
                "visual_scenes",
                {
                    "visual_phase_key": "TEXT NOT NULL DEFAULT 'default'",
                    "visual_phase_label": "TEXT NOT NULL DEFAULT '默认阶段'",
                    "visual_prompt": "TEXT NOT NULL DEFAULT ''",
                    "negative_prompt": "TEXT NOT NULL DEFAULT ''",
                    "workflow_name": "TEXT NOT NULL DEFAULT ''",
                    "raw_json": "TEXT NOT NULL DEFAULT '{}'",
                },
            )
            ensure_table_columns(
                connection,
                "storyboards",
                {
                    "coverage_json": "TEXT NOT NULL DEFAULT '{}'",
                    "script_feedback_json": "TEXT NOT NULL DEFAULT '[]'",
                },
            )
            ensure_table_columns(
                connection,
                "storyboard_shots",
                {
                    "source_paragraph_index": "INTEGER NOT NULL DEFAULT 0",
                    "source_text_start": "INTEGER NOT NULL DEFAULT 0",
                    "source_text_end": "INTEGER NOT NULL DEFAULT 0",
                    "source_excerpt": "TEXT NOT NULL DEFAULT ''",
                    "is_supplemental": "INTEGER NOT NULL DEFAULT 0",
                    "supplemental_reason": "TEXT NOT NULL DEFAULT ''",
                    "scene_block_id": "TEXT NOT NULL DEFAULT ''",
                    "scene_block_title": "TEXT NOT NULL DEFAULT ''",
                    "scene_block_index": "INTEGER NOT NULL DEFAULT 0",
                    "sequence_id": "TEXT NOT NULL DEFAULT ''",
                    "sequence_title": "TEXT NOT NULL DEFAULT ''",
                    "beat_id": "TEXT NOT NULL DEFAULT ''",
                    "beat_title": "TEXT NOT NULL DEFAULT ''",
                    "prev_shot_id": "TEXT NOT NULL DEFAULT ''",
                    "next_shot_id": "TEXT NOT NULL DEFAULT ''",
                    "transition": "TEXT NOT NULL DEFAULT 'cut'",
                    "continuity_json": "TEXT NOT NULL DEFAULT '{}'",
                    "production_plan_json": "TEXT NOT NULL DEFAULT '{}'",
                    "prompt_parts_json": "TEXT NOT NULL DEFAULT '{}'",
                },
            )
            backfill_visual_phase_metadata(connection)
            connection.execute("DROP INDEX IF EXISTS idx_visual_subjects_canonical")
            connection.execute("DROP INDEX IF EXISTS idx_visual_scenes_canonical")
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_visual_subjects_identity
                    ON visual_subjects(canonical_name, visual_phase_key)
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_visual_scenes_identity
                    ON visual_scenes(canonical_name, visual_phase_key)
                """
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
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, (strftime('%Y%m%d %H%M','now','localtime')))
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
                    updated_at=(strftime('%Y%m%d %H%M','now','localtime'))
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
                    updated_at = (strftime('%Y%m%d %H%M','now','localtime'))
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
                    updated_at = (strftime('%Y%m%d %H%M','now','localtime'))
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
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, (strftime('%Y%m%d %H%M','now','localtime')))
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
                    updated_at=(strftime('%Y%m%d %H%M','now','localtime'))
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
                    updated_at = (strftime('%Y%m%d %H%M','now','localtime'))
                WHERE generation_id = ?
                """,
                (json_dumps(script), json_dumps(current), generation_id),
            )
        updated = self.find_script_generation(generation_id)
        if not updated:
            raise KeyError(generation_id)
        return updated

    def update_script_storyboard_script(self, generation_id: str, storyboard_script: dict[str, Any]) -> dict[str, Any]:
        current = self.find_script_generation(generation_id)
        if not current:
            raise KeyError(generation_id)
        script = dict(current.get("script") or {})
        script["storyboard_script"] = dict(storyboard_script or {})
        current["script"] = script
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE script_generations
                SET script_json = ?,
                    raw_json = ?,
                    updated_at = (strftime('%Y%m%d %H%M','now','localtime'))
                WHERE generation_id = ?
                """,
                (json_dumps(script), json_dumps(current), generation_id),
            )
        updated = self.find_script_generation(generation_id)
        if not updated:
            raise KeyError(generation_id)
        return updated

    def save_storyboard(self, generation_id: str, storyboard_payload: dict[str, Any]) -> dict[str, Any]:
        if not self.find_script_generation(generation_id):
            raise KeyError(generation_id)
        payload = dict(storyboard_payload or {})
        storyboard_id = str(payload.get("storyboard_id") or f"sb-{uuid4().hex}")
        shots = [
            normalize_storyboard_shot_payload(shot, generation_id=generation_id, storyboard_id=storyboard_id, index=index)
            for index, shot in enumerate(payload.get("shots") or [], start=1)
            if isinstance(shot, dict)
        ]
        actual_duration = sum(float(shot["duration_sec"]) for shot in shots)
        target_duration = safe_int(payload.get("target_duration_sec"), default=0)
        status = str(payload.get("status") or ("needs_review" if any(shot["needs_manual_review"] for shot in shots) else "completed"))
        raw_payload = payload.get("raw") if isinstance(payload.get("raw"), dict) else dict(payload)
        scene_blocks = payload.get("scene_blocks") if isinstance(payload.get("scene_blocks"), list) else []
        raw_payload["scene_blocks"] = scene_blocks
        normalized = {
            "storyboard_id": storyboard_id,
            "generation_id": generation_id,
            "title": str(payload.get("title") or "镜头生产结构"),
            "source_type": str(payload.get("source_type") or "script_generation"),
            "source_filename": str(payload.get("source_filename") or ""),
            "status": status,
            "target_duration_sec": target_duration if target_duration > 0 else round(actual_duration),
            "actual_duration_sec": actual_duration,
            "shot_count": len(shots),
            "style_policy": payload.get("style_policy") if isinstance(payload.get("style_policy"), dict) else {},
            "missing_subject_candidates": payload.get("missing_subject_candidates") if isinstance(payload.get("missing_subject_candidates"), list) else [],
            "missing_scene_candidates": payload.get("missing_scene_candidates") if isinstance(payload.get("missing_scene_candidates"), list) else [],
            "review_notes": payload.get("review_notes") if isinstance(payload.get("review_notes"), list) else [],
            "coverage": payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {},
            "script_feedback": payload.get("script_feedback") if isinstance(payload.get("script_feedback"), list) else [],
            "scene_blocks": scene_blocks,
            "raw": raw_payload,
        }
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO storyboards (
                    storyboard_id, generation_id, title, source_type, source_filename,
                    status, target_duration_sec, actual_duration_sec, shot_count,
                    style_policy_json, missing_subject_candidates_json,
                    missing_scene_candidates_json, review_notes_json,
                    coverage_json, script_feedback_json, raw_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, (strftime('%Y%m%d %H%M','now','localtime')), (strftime('%Y%m%d %H%M','now','localtime')))
                ON CONFLICT(storyboard_id) DO UPDATE SET
                    generation_id=excluded.generation_id,
                    title=excluded.title,
                    source_type=excluded.source_type,
                    source_filename=excluded.source_filename,
                    status=excluded.status,
                    target_duration_sec=excluded.target_duration_sec,
                    actual_duration_sec=excluded.actual_duration_sec,
                    shot_count=excluded.shot_count,
                    style_policy_json=excluded.style_policy_json,
                    missing_subject_candidates_json=excluded.missing_subject_candidates_json,
                    missing_scene_candidates_json=excluded.missing_scene_candidates_json,
                    review_notes_json=excluded.review_notes_json,
                    coverage_json=excluded.coverage_json,
                    script_feedback_json=excluded.script_feedback_json,
                    raw_json=excluded.raw_json,
                    updated_at=(strftime('%Y%m%d %H%M','now','localtime'))
                """,
                storyboard_values(normalized),
            )
            connection.execute("DELETE FROM storyboard_shots WHERE storyboard_id = ?", (storyboard_id,))
            for shot in shots:
                connection.execute(
                    """
                    INSERT INTO storyboard_shots (
                        shot_id, storyboard_id, generation_id, shot_index,
                        narration, subtitle_text, shot_type, visual_goal,
                        scene_id, scene_name, subject_ids_json, subject_names_json,
                        visual_elements_json, reference_assets_json, camera_json,
                        duration_sec, keyframe_prompt, video_prompt, negative_prompt,
                        fact_safety_note, asset_status, keyframe_asset_id, video_asset_id,
                        needs_manual_review, source_paragraph_index, source_text_start,
                        source_text_end, source_excerpt, is_supplemental,
                        supplemental_reason, scene_block_id, scene_block_title,
                        scene_block_index, sequence_id, sequence_title, beat_id,
                        beat_title, prev_shot_id, next_shot_id, transition,
                        continuity_json, production_plan_json, prompt_parts_json,
                        raw_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, (strftime('%Y%m%d %H%M','now','localtime')), (strftime('%Y%m%d %H%M','now','localtime')))
                    """,
                    storyboard_shot_values(shot),
                )
            sync_storyboard_shot_candidates(connection, storyboard_id, shots, replace_storyboard=True)
        saved = self.find_storyboard(storyboard_id)
        if not saved:
            raise KeyError(storyboard_id)
        return saved

    def list_storyboards(self, generation_id: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as connection:
            if generation_id:
                rows = connection.execute(
                    """
                    SELECT
                        storyboards.*,
                        script_generations.topic,
                        script_generations.script_json
                    FROM storyboards
                    LEFT JOIN script_generations
                        ON script_generations.generation_id = storyboards.generation_id
                    WHERE storyboards.generation_id = ?
                    ORDER BY storyboards.created_at DESC, storyboards.updated_at DESC
                    """,
                    (generation_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT
                        storyboards.*,
                        script_generations.topic,
                        script_generations.script_json
                    FROM storyboards
                    LEFT JOIN script_generations
                        ON script_generations.generation_id = storyboards.generation_id
                    ORDER BY storyboards.created_at DESC, storyboards.updated_at DESC
                    """
                ).fetchall()
        return [storyboard_from_row(row) for row in rows]

    def find_storyboard(self, storyboard_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    storyboards.*,
                    script_generations.topic,
                    script_generations.script_json
                FROM storyboards
                LEFT JOIN script_generations
                    ON script_generations.generation_id = storyboards.generation_id
                WHERE storyboards.storyboard_id = ?
                """,
                (storyboard_id,),
            ).fetchone()
        return storyboard_from_row(row) if row else None

    def delete_storyboard(self, storyboard_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            connection.execute("DELETE FROM storyboards WHERE storyboard_id = ?", (storyboard_id,))
        return self.list_storyboards()

    def list_storyboard_shots(self, storyboard_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM storyboard_shots WHERE storyboard_id = ? ORDER BY shot_index",
                (storyboard_id,),
            ).fetchall()
        return [storyboard_shot_from_row(row) for row in rows]

    def update_storyboard_shot(self, storyboard_id: str, shot_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        current = self.find_storyboard(storyboard_id)
        if not current:
            raise KeyError(storyboard_id)
        shots = self.list_storyboard_shots(storyboard_id)
        target = next((shot for shot in shots if shot["shot_id"] == shot_id), None)
        if not target:
            raise KeyError(shot_id)
        merged = dict(target)
        for key in (
            "narration",
            "subtitle_text",
            "shot_type",
            "visual_goal",
            "scene_id",
            "scene_name",
            "subject_ids",
            "subject_names",
            "visual_elements",
            "reference_assets",
            "camera",
            "duration_sec",
            "keyframe_prompt",
            "video_prompt",
            "negative_prompt",
            "fact_safety_note",
            "asset_status",
            "keyframe_asset_id",
            "video_asset_id",
            "needs_manual_review",
            "source_paragraph_index",
            "source_text_start",
            "source_text_end",
            "source_excerpt",
            "is_supplemental",
            "supplemental_reason",
            "scene_block_id",
            "scene_block_title",
            "scene_block_index",
            "sequence_id",
            "sequence_title",
            "beat_id",
            "beat_title",
            "prev_shot_id",
            "next_shot_id",
            "transition",
            "continuity",
            "production_plan",
            "prompt_parts",
        ):
            if key in updates:
                merged[key] = updates[key]
        raw = dict(target.get("raw") or {})
        raw.update({key: updates[key] for key in updates if key in merged})
        merged["raw"] = raw
        normalized = normalize_storyboard_shot_payload(
            merged,
            generation_id=current["generation_id"],
            storyboard_id=storyboard_id,
            index=int(target["shot_index"]),
        )
        normalized["shot_id"] = shot_id
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE storyboard_shots
                SET narration = ?,
                    subtitle_text = ?,
                    shot_type = ?,
                    visual_goal = ?,
                    scene_id = ?,
                    scene_name = ?,
                    subject_ids_json = ?,
                    subject_names_json = ?,
                    visual_elements_json = ?,
                    reference_assets_json = ?,
                    camera_json = ?,
                    duration_sec = ?,
                    keyframe_prompt = ?,
                    video_prompt = ?,
                    negative_prompt = ?,
                    fact_safety_note = ?,
                    asset_status = ?,
                    keyframe_asset_id = ?,
                    video_asset_id = ?,
                    needs_manual_review = ?,
                    source_paragraph_index = ?,
                    source_text_start = ?,
                    source_text_end = ?,
                    source_excerpt = ?,
                    is_supplemental = ?,
                    supplemental_reason = ?,
                    scene_block_id = ?,
                    scene_block_title = ?,
                    scene_block_index = ?,
                    sequence_id = ?,
                    sequence_title = ?,
                    beat_id = ?,
                    beat_title = ?,
                    prev_shot_id = ?,
                    next_shot_id = ?,
                    transition = ?,
                    continuity_json = ?,
                    production_plan_json = ?,
                    prompt_parts_json = ?,
                    raw_json = ?,
                    updated_at = (strftime('%Y%m%d %H%M','now','localtime'))
                WHERE storyboard_id = ? AND shot_id = ?
                """,
                (
                    normalized["narration"],
                    normalized["subtitle_text"],
                    normalized["shot_type"],
                    normalized["visual_goal"],
                    normalized["scene_id"],
                    normalized["scene_name"],
                    json_dumps(normalized.get("subject_ids") or []),
                    json_dumps(normalized.get("subject_names") or []),
                    json_dumps(normalized.get("visual_elements") or []),
                    json_dumps(normalized.get("reference_assets") or {}),
                    json_dumps(normalized.get("camera") or {}),
                    normalized["duration_sec"],
                    normalized["keyframe_prompt"],
                    normalized["video_prompt"],
                    normalized["negative_prompt"],
                    normalized["fact_safety_note"],
                    normalized["asset_status"],
                    normalized["keyframe_asset_id"],
                    normalized["video_asset_id"],
                    1 if normalized.get("needs_manual_review") else 0,
                    normalized["source_paragraph_index"],
                    normalized["source_text_start"],
                    normalized["source_text_end"],
                    normalized["source_excerpt"],
                    1 if normalized.get("is_supplemental") else 0,
                    normalized["supplemental_reason"],
                    normalized["scene_block_id"],
                    normalized["scene_block_title"],
                    normalized["scene_block_index"],
                    normalized["sequence_id"],
                    normalized["sequence_title"],
                    normalized["beat_id"],
                    normalized["beat_title"],
                    normalized["prev_shot_id"],
                    normalized["next_shot_id"],
                    normalized["transition"],
                    json_dumps(normalized.get("continuity") or {}),
                    json_dumps(normalized.get("production_plan") or {}),
                    json_dumps(normalized.get("prompt_parts") or {}),
                    json_dumps(normalized.get("raw") or {}),
                    storyboard_id,
                    shot_id,
                ),
            )
            sync_storyboard_shot_candidates(connection, storyboard_id, [normalized], replace_storyboard=False)
            refresh_storyboard_totals(connection, storyboard_id)
        updated = next((shot for shot in self.list_storyboard_shots(storyboard_id) if shot["shot_id"] == shot_id), None)
        if not updated:
            raise KeyError(shot_id)
        return updated

    def list_storyboard_shot_candidates(
        self,
        storyboard_id: str,
        shot_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.connect() as connection:
            if shot_id:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM storyboard_shot_asset_candidates
                    WHERE storyboard_id = ? AND shot_id = ?
                    ORDER BY candidate_type, candidate_id
                    """,
                    (storyboard_id, shot_id),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM storyboard_shot_asset_candidates
                    WHERE storyboard_id = ?
                    ORDER BY shot_id, candidate_type, candidate_id
                    """,
                    (storyboard_id,),
                ).fetchall()
        return [storyboard_shot_candidate_from_row(row) for row in rows]

    def mark_storyboard_shot_candidate_linked(
        self,
        storyboard_id: str,
        shot_id: str,
        candidate_id: int,
        *,
        linked_entity_id: str = "",
    ) -> dict[str, Any]:
        return self.update_storyboard_shot_candidate_status(
            storyboard_id,
            shot_id,
            candidate_id,
            candidate_status="linked",
            linked_entity_id=linked_entity_id,
        )

    def mark_storyboard_shot_candidate_ignored(
        self,
        storyboard_id: str,
        shot_id: str,
        candidate_id: int,
    ) -> dict[str, Any]:
        return self.update_storyboard_shot_candidate_status(
            storyboard_id,
            shot_id,
            candidate_id,
            candidate_status="ignored",
        )

    def update_storyboard_shot_candidate_status(
        self,
        storyboard_id: str,
        shot_id: str,
        candidate_id: int,
        *,
        candidate_status: str,
        linked_entity_id: str = "",
    ) -> dict[str, Any]:
        if candidate_status not in {"pending", "linked", "ignored"}:
            raise ValueError(candidate_status)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM storyboard_shot_asset_candidates
                WHERE storyboard_id = ? AND shot_id = ? AND candidate_id = ?
                """,
                (storyboard_id, shot_id, candidate_id),
            ).fetchone()
            if not row:
                raise KeyError(str(candidate_id))
            candidate = storyboard_shot_candidate_from_row(row)
            resolved_linked_entity_id = (
                str(linked_entity_id or candidate.get("linked_entity_id") or "")
                if candidate_status == "linked"
                else ""
            )
            connection.execute(
                """
                UPDATE storyboard_shot_asset_candidates
                SET candidate_status = ?,
                    linked_entity_id = ?,
                    confirmed_at = ?,
                    updated_at = (strftime('%Y%m%d %H%M','now','localtime'))
                WHERE candidate_id = ?
                """,
                (candidate_status, resolved_linked_entity_id, current_timestamp(), candidate_id),
            )
            if candidate_status == "linked" and resolved_linked_entity_id:
                apply_candidate_link_to_storyboard_shot(connection, candidate, resolved_linked_entity_id)
            updated = connection.execute(
                "SELECT * FROM storyboard_shot_asset_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        if not updated:
            raise KeyError(str(candidate_id))
        return storyboard_shot_candidate_from_row(updated)

    def build_storyboard_shot_preparation_state(self, storyboard_id: str, shot_id: str) -> dict[str, Any]:
        storyboard = self.find_storyboard(storyboard_id)
        if not storyboard:
            raise KeyError(storyboard_id)
        shot = next((item for item in self.list_storyboard_shots(storyboard_id) if item["shot_id"] == shot_id), None)
        if not shot:
            raise KeyError(shot_id)
        candidates = self.list_storyboard_shot_candidates(storyboard_id, shot_id)
        pending_candidates = [item for item in candidates if item["candidate_status"] == "pending"]
        linked_candidates = [item for item in candidates if item["candidate_status"] == "linked"]
        ignored_candidates = [item for item in candidates if item["candidate_status"] == "ignored"]
        basic_info_ready = bool((shot.get("narration") or shot.get("source_excerpt") or "").strip()) and safe_float(
            shot.get("duration_sec"),
            default=0.0,
        ) > 0
        prompt_ready = bool(str(shot.get("keyframe_prompt") or "").strip())
        reference_asset_count = count_reference_assets(shot.get("reference_assets") or {})
        missing_reference_asset_count = count_missing_reference_assets(shot.get("reference_assets") or {})
        ready_for_keyframe = basic_info_ready and prompt_ready and not pending_candidates
        return {
            "storyboard_id": storyboard_id,
            "shot_id": shot_id,
            "status": "ready" if ready_for_keyframe else "pending",
            "shot": shot,
            "candidates": candidates,
            "candidate_count": len(candidates),
            "pending_candidate_count": len(pending_candidates),
            "linked_candidate_count": len(linked_candidates),
            "ignored_candidate_count": len(ignored_candidates),
            "reference_asset_count": reference_asset_count,
            "missing_reference_asset_count": missing_reference_asset_count,
            "basic_info_ready": basic_info_ready,
            "prompt_ready": prompt_ready,
            "ready_for_keyframe": ready_for_keyframe,
        }

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
                    """
                    SELECT subject_id
                    FROM visual_subjects
                    WHERE canonical_name = ? AND visual_phase_key = ?
                    """,
                    (subject["canonical_name"], subject["visual_phase_key"]),
                ).fetchone()
                if existing:
                    subject["subject_id"] = existing["subject_id"]
                connection.execute(
                    """
                    INSERT INTO visual_subjects (
                        subject_id, canonical_name, visual_phase_key, visual_phase_label,
                        pinyin_key, first_letter, subject_type,
                        short_description, visual_identity_json, consistency_rules_json,
                        negative_rules_json, status, anchor_asset_id, visual_prompt,
                        negative_prompt, workflow_name, raw_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, (strftime('%Y%m%d %H%M','now','localtime')), (strftime('%Y%m%d %H%M','now','localtime')))
                    ON CONFLICT(subject_id) DO UPDATE SET
                        canonical_name=excluded.canonical_name,
                        visual_phase_key=excluded.visual_phase_key,
                        visual_phase_label=excluded.visual_phase_label,
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
                        updated_at=(strftime('%Y%m%d %H%M','now','localtime'))
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
            "visual_phase_key",
            "visual_phase_label",
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
                    visual_phase_key = ?,
                    visual_phase_label = ?,
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
                    updated_at = (strftime('%Y%m%d %H%M','now','localtime'))
                WHERE subject_id = ?
                """,
                (*visual_subject_values(normalized)[1:], subject_id),
            )
        subject = self.find_visual_subject(subject_id)
        if not subject:
            raise KeyError(subject_id)
        return subject

    def save_visual_scene_extraction(
        self,
        generation_id: str,
        extraction: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not self.find_script_generation(generation_id):
            raise KeyError(generation_id)
        raw_scenes = extraction.get("scenes") if isinstance(extraction, dict) else []
        scenes = [normalize_visual_scene_payload(scene) for scene in raw_scenes or [] if isinstance(scene, dict)]
        with self.connect() as connection:
            connection.execute("DELETE FROM script_visual_scenes WHERE generation_id = ?", (generation_id,))
            for scene in scenes:
                existing = connection.execute(
                    """
                    SELECT scene_id
                    FROM visual_scenes
                    WHERE canonical_name = ? AND visual_phase_key = ?
                    """,
                    (scene["canonical_name"], scene["visual_phase_key"]),
                ).fetchone()
                if existing:
                    scene["scene_id"] = existing["scene_id"]
                connection.execute(
                    """
                    INSERT INTO visual_scenes (
                        scene_id, canonical_name, visual_phase_key, visual_phase_label,
                        pinyin_key, first_letter, scene_type,
                        short_description, visual_identity_json, consistency_rules_json,
                        negative_rules_json, status, anchor_asset_id, visual_prompt,
                        negative_prompt, workflow_name, raw_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, (strftime('%Y%m%d %H%M','now','localtime')), (strftime('%Y%m%d %H%M','now','localtime')))
                    ON CONFLICT(scene_id) DO UPDATE SET
                        canonical_name=excluded.canonical_name,
                        visual_phase_key=excluded.visual_phase_key,
                        visual_phase_label=excluded.visual_phase_label,
                        pinyin_key=excluded.pinyin_key,
                        first_letter=excluded.first_letter,
                        scene_type=excluded.scene_type,
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
                        updated_at=(strftime('%Y%m%d %H%M','now','localtime'))
                    """,
                    visual_scene_values(scene),
                )
                connection.execute(
                    """
                    INSERT INTO script_visual_scenes (
                        generation_id, scene_id, role_in_script, importance,
                        first_appearance, evidence_text, extraction_confidence, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(generation_id, scene_id) DO UPDATE SET
                        role_in_script=excluded.role_in_script,
                        importance=excluded.importance,
                        first_appearance=excluded.first_appearance,
                        evidence_text=excluded.evidence_text,
                        extraction_confidence=excluded.extraction_confidence,
                        raw_json=excluded.raw_json
                    """,
                    script_visual_scene_values(generation_id, scene),
                )
        return self.list_script_visual_scenes(generation_id)

    def list_visual_scenes(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    visual_scenes.*,
                    COUNT(DISTINCT script_visual_scenes.generation_id) AS script_count
                FROM visual_scenes
                LEFT JOIN script_visual_scenes
                    ON script_visual_scenes.scene_id = visual_scenes.scene_id
                GROUP BY visual_scenes.scene_id
                ORDER BY visual_scenes.first_letter, visual_scenes.pinyin_key, visual_scenes.canonical_name
                """
            ).fetchall()
        return [visual_scene_from_row(row) for row in rows]

    def find_visual_scene(self, scene_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    visual_scenes.*,
                    COUNT(DISTINCT script_visual_scenes.generation_id) AS script_count
                FROM visual_scenes
                LEFT JOIN script_visual_scenes
                    ON script_visual_scenes.scene_id = visual_scenes.scene_id
                WHERE visual_scenes.scene_id = ?
                GROUP BY visual_scenes.scene_id
                """,
                (scene_id,),
            ).fetchone()
            if not row:
                return None
            appearances = connection.execute(
                """
                SELECT
                    script_visual_scenes.*,
                    script_generations.topic,
                    script_generations.created_at,
                    script_generations.time_range
                FROM script_visual_scenes
                LEFT JOIN script_generations
                    ON script_generations.generation_id = script_visual_scenes.generation_id
                WHERE script_visual_scenes.scene_id = ?
                ORDER BY script_generations.created_at DESC, script_visual_scenes.importance DESC
                """,
                (scene_id,),
            ).fetchall()
        scene = visual_scene_from_row(row)
        scene["appearances"] = [visual_scene_appearance_from_row(appearance) for appearance in appearances]
        return scene

    def list_script_visual_scenes(self, generation_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    script_visual_scenes.generation_id,
                    script_visual_scenes.role_in_script,
                    script_visual_scenes.importance,
                    script_visual_scenes.first_appearance,
                    script_visual_scenes.evidence_text,
                    script_visual_scenes.extraction_confidence,
                    script_visual_scenes.raw_json AS script_scene_raw_json,
                    visual_scenes.*,
                    1 AS script_count
                FROM script_visual_scenes
                JOIN visual_scenes
                    ON visual_scenes.scene_id = script_visual_scenes.scene_id
                WHERE script_visual_scenes.generation_id = ?
                ORDER BY script_visual_scenes.importance DESC, visual_scenes.pinyin_key
                """,
                (generation_id,),
            ).fetchall()
        return [script_visual_scene_from_row(row) for row in rows]

    def update_visual_scene(self, scene_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        current = self.find_visual_scene(scene_id)
        if not current:
            raise KeyError(scene_id)
        merged = dict(current)
        for key in (
            "canonical_name",
            "visual_phase_key",
            "visual_phase_label",
            "scene_type",
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
        normalized = normalize_visual_scene_payload(merged)
        normalized["scene_id"] = scene_id
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE visual_scenes
                SET canonical_name = ?,
                    visual_phase_key = ?,
                    visual_phase_label = ?,
                    pinyin_key = ?,
                    first_letter = ?,
                    scene_type = ?,
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
                    updated_at = (strftime('%Y%m%d %H%M','now','localtime'))
                WHERE scene_id = ?
                """,
                (*visual_scene_values(normalized)[1:], scene_id),
            )
        scene = self.find_visual_scene(scene_id)
        if not scene:
            raise KeyError(scene_id)
        return scene

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
                    ORDER BY updated_at DESC, message_count DESC, rowid DESC
                    """,
                    (generation_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM script_assistant_conversations
                    WHERE generation_id = ? AND is_archived = 0
                    ORDER BY updated_at DESC, message_count DESC, rowid DESC
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
                    applied_at = (strftime('%Y%m%d %H%M','now','localtime'))
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
                    applied_at = CASE WHEN ? = 'applied' THEN (strftime('%Y%m%d %H%M','now','localtime')) ELSE applied_at END
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
                ) VALUES (?, ?, ?, ?, ?, ?, ?, (strftime('%Y%m%d %H%M','now','localtime')))
                ON CONFLICT(generation_id) DO UPDATE SET
                    active_intent=excluded.active_intent,
                    active_selection_id=excluded.active_selection_id,
                    active_patch_id=excluded.active_patch_id,
                    article_version_hash=excluded.article_version_hash,
                    session_summary=excluded.session_summary,
                    style_preferences_json=excluded.style_preferences_json,
                    updated_at=(strftime('%Y%m%d %H%M','now','localtime'))
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


def storyboard_values(storyboard: dict[str, Any]) -> tuple[Any, ...]:
    actual_duration = safe_float(storyboard.get("actual_duration_sec"), default=0.0)
    target_duration = safe_int(storyboard.get("target_duration_sec"), default=0)
    return (
        storyboard["storyboard_id"],
        storyboard["generation_id"],
        storyboard.get("title", ""),
        storyboard.get("source_type", "script_generation"),
        storyboard.get("source_filename", ""),
        storyboard.get("status", "draft"),
        target_duration if target_duration > 0 else round(actual_duration),
        actual_duration,
        int(storyboard.get("shot_count") or 0),
        json_dumps(storyboard.get("style_policy") or {}),
        json_dumps(storyboard.get("missing_subject_candidates") or []),
        json_dumps(storyboard.get("missing_scene_candidates") or []),
        json_dumps(storyboard.get("review_notes") or []),
        json_dumps(storyboard.get("coverage") or {}),
        json_dumps(storyboard.get("script_feedback") or []),
        json_dumps(storyboard.get("raw") or storyboard),
    )


def storyboard_from_row(row: sqlite3.Row) -> dict[str, Any]:
    raw = json_loads(row["raw_json"], default={})
    script = json_loads(row["script_json"], default={}) if "script_json" in row.keys() else {}
    storyboard = dict(raw) if isinstance(raw, dict) else {}
    storyboard.update(
        {
            "storyboard_id": row["storyboard_id"],
            "generation_id": row["generation_id"],
            "title": row["title"],
            "source_type": row["source_type"],
            "source_filename": row["source_filename"],
            "status": row["status"],
            "target_duration_sec": row["target_duration_sec"],
            "actual_duration_sec": row["actual_duration_sec"] or 0,
            "shot_count": row["shot_count"],
            "style_policy": json_loads(row["style_policy_json"], default={}),
            "missing_subject_candidates": json_loads(row["missing_subject_candidates_json"], default=[]),
            "missing_scene_candidates": json_loads(row["missing_scene_candidates_json"], default=[]),
            "review_notes": json_loads(row["review_notes_json"], default=[]),
            "coverage": json_loads(row["coverage_json"], default={}) if "coverage_json" in row.keys() else {},
            "script_feedback": json_loads(row["script_feedback_json"], default=[]) if "script_feedback_json" in row.keys() else [],
            "scene_blocks": raw.get("scene_blocks") if isinstance(raw.get("scene_blocks"), list) else [],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "topic": row["topic"] if "topic" in row.keys() else "",
            "script_title": script.get("title") if isinstance(script, dict) else "",
            "raw": raw,
        }
    )
    reconstruction = raw.get("scene_reconstruction") if isinstance(raw.get("scene_reconstruction"), dict) else {}
    if reconstruction:
        storyboard["reconstruction_id"] = reconstruction.get("reconstruction_id") or storyboard.get("reconstruction_id", "")
        storyboard["scene_count"] = reconstruction.get("scene_count") or len(reconstruction.get("scenes") or [])
        storyboard["segment_count"] = reconstruction.get("segment_count") or sum(len(scene.get("segments") or []) for scene in reconstruction.get("scenes") or [])
        storyboard["keyframe_count"] = reconstruction.get("keyframe_count") or sum(len(scene.get("keyframes") or []) for scene in reconstruction.get("scenes") or [])
        storyboard["json_path"] = reconstruction.get("json_path") or storyboard.get("json_path", "")
        storyboard["markdown_path"] = reconstruction.get("markdown_path") or storyboard.get("markdown_path", "")
    if not storyboard["script_title"]:
        storyboard["script_title"] = storyboard.get("topic") or storyboard.get("title") or ""
    return storyboard


def normalize_storyboard_shot_payload(
    shot: dict[str, Any],
    *,
    generation_id: str,
    storyboard_id: str,
    index: int,
) -> dict[str, Any]:
    normalized = dict(shot)
    normalized.update(
        {
            "shot_id": str(shot.get("shot_id") or f"shot-{uuid4().hex}"),
            "storyboard_id": storyboard_id,
            "generation_id": generation_id,
            "shot_index": int(shot.get("shot_index") or index),
            "narration": str(shot.get("narration") or ""),
            "subtitle_text": str(shot.get("subtitle_text") or shot.get("narration") or ""),
            "shot_type": str(shot.get("shot_type") or "narrative_shot"),
            "visual_goal": str(shot.get("visual_goal") or ""),
            "scene_id": str(shot.get("scene_id") or ""),
            "scene_name": str(shot.get("scene_name") or ""),
            "subject_ids": normalize_json_list(shot.get("subject_ids")),
            "subject_names": normalize_json_list(shot.get("subject_names")),
            "visual_elements": normalize_json_list(shot.get("visual_elements")),
            "reference_assets": shot.get("reference_assets") if isinstance(shot.get("reference_assets"), dict) else {},
            "camera": shot.get("camera") if isinstance(shot.get("camera"), dict) else {},
            "duration_sec": safe_float(shot.get("duration_sec"), default=4.0),
            "keyframe_prompt": str(shot.get("keyframe_prompt") or ""),
            "video_prompt": str(shot.get("video_prompt") or ""),
            "negative_prompt": str(shot.get("negative_prompt") or ""),
            "fact_safety_note": str(shot.get("fact_safety_note") or ""),
            "asset_status": str(shot.get("asset_status") or "missing_keyframe"),
            "keyframe_asset_id": str(shot.get("keyframe_asset_id") or ""),
            "video_asset_id": str(shot.get("video_asset_id") or ""),
            "needs_manual_review": bool(shot.get("needs_manual_review")),
            "source_paragraph_index": safe_int(shot.get("source_paragraph_index"), default=0),
            "source_text_start": safe_int(shot.get("source_text_start"), default=0),
            "source_text_end": safe_int(shot.get("source_text_end"), default=0),
            "source_excerpt": str(shot.get("source_excerpt") or ""),
            "is_supplemental": bool(shot.get("is_supplemental")),
            "supplemental_reason": str(shot.get("supplemental_reason") or ""),
            "scene_block_id": str(shot.get("scene_block_id") or ""),
            "scene_block_title": str(shot.get("scene_block_title") or ""),
            "scene_block_index": safe_int(shot.get("scene_block_index"), default=0),
            "sequence_id": str(shot.get("sequence_id") or ""),
            "sequence_title": str(shot.get("sequence_title") or ""),
            "beat_id": str(shot.get("beat_id") or ""),
            "beat_title": str(shot.get("beat_title") or ""),
            "prev_shot_id": str(shot.get("prev_shot_id") or ""),
            "next_shot_id": str(shot.get("next_shot_id") or ""),
            "transition": str(shot.get("transition") or "cut"),
            "continuity": shot.get("continuity") if isinstance(shot.get("continuity"), dict) else {},
            "production_plan": shot.get("production_plan") if isinstance(shot.get("production_plan"), dict) else {},
            "prompt_parts": shot.get("prompt_parts") if isinstance(shot.get("prompt_parts"), dict) else {},
            "raw": shot.get("raw") if isinstance(shot.get("raw"), dict) else shot,
        }
    )
    return normalized


def storyboard_shot_values(shot: dict[str, Any]) -> tuple[Any, ...]:
    return (
        shot["shot_id"],
        shot["storyboard_id"],
        shot["generation_id"],
        int(shot["shot_index"]),
        shot.get("narration", ""),
        shot.get("subtitle_text", ""),
        shot.get("shot_type", ""),
        shot.get("visual_goal", ""),
        shot.get("scene_id", ""),
        shot.get("scene_name", ""),
        json_dumps(shot.get("subject_ids") or []),
        json_dumps(shot.get("subject_names") or []),
        json_dumps(shot.get("visual_elements") or []),
        json_dumps(shot.get("reference_assets") or {}),
        json_dumps(shot.get("camera") or {}),
        safe_float(shot.get("duration_sec"), default=4.0),
        shot.get("keyframe_prompt", ""),
        shot.get("video_prompt", ""),
        shot.get("negative_prompt", ""),
        shot.get("fact_safety_note", ""),
        shot.get("asset_status", "missing_keyframe"),
        shot.get("keyframe_asset_id", ""),
        shot.get("video_asset_id", ""),
        1 if shot.get("needs_manual_review") else 0,
        safe_int(shot.get("source_paragraph_index"), default=0),
        safe_int(shot.get("source_text_start"), default=0),
        safe_int(shot.get("source_text_end"), default=0),
        shot.get("source_excerpt", ""),
        1 if shot.get("is_supplemental") else 0,
        shot.get("supplemental_reason", ""),
        shot.get("scene_block_id", ""),
        shot.get("scene_block_title", ""),
        safe_int(shot.get("scene_block_index"), default=0),
        shot.get("sequence_id", ""),
        shot.get("sequence_title", ""),
        shot.get("beat_id", ""),
        shot.get("beat_title", ""),
        shot.get("prev_shot_id", ""),
        shot.get("next_shot_id", ""),
        shot.get("transition", "cut"),
        json_dumps(shot.get("continuity") or {}),
        json_dumps(shot.get("production_plan") or {}),
        json_dumps(shot.get("prompt_parts") or {}),
        json_dumps(shot.get("raw") or shot),
    )


def storyboard_shot_from_row(row: sqlite3.Row) -> dict[str, Any]:
    raw = json_loads(row["raw_json"], default={})
    shot = dict(raw) if isinstance(raw, dict) else {}
    shot.update(
        {
            "shot_id": row["shot_id"],
            "storyboard_id": row["storyboard_id"],
            "generation_id": row["generation_id"],
            "shot_index": row["shot_index"],
            "narration": row["narration"],
            "subtitle_text": row["subtitle_text"],
            "shot_type": row["shot_type"],
            "visual_goal": row["visual_goal"],
            "scene_id": row["scene_id"],
            "scene_name": row["scene_name"],
            "subject_ids": json_loads(row["subject_ids_json"], default=[]),
            "subject_names": json_loads(row["subject_names_json"], default=[]),
            "visual_elements": json_loads(row["visual_elements_json"], default=[]),
            "reference_assets": json_loads(row["reference_assets_json"], default={}),
            "camera": json_loads(row["camera_json"], default={}),
            "duration_sec": row["duration_sec"],
            "keyframe_prompt": row["keyframe_prompt"],
            "video_prompt": row["video_prompt"],
            "negative_prompt": row["negative_prompt"],
            "fact_safety_note": row["fact_safety_note"],
            "asset_status": row["asset_status"],
            "keyframe_asset_id": row["keyframe_asset_id"],
            "video_asset_id": row["video_asset_id"],
            "needs_manual_review": bool(row["needs_manual_review"]),
            "source_paragraph_index": row["source_paragraph_index"] if "source_paragraph_index" in row.keys() else 0,
            "source_text_start": row["source_text_start"] if "source_text_start" in row.keys() else 0,
            "source_text_end": row["source_text_end"] if "source_text_end" in row.keys() else 0,
            "source_excerpt": row["source_excerpt"] if "source_excerpt" in row.keys() else "",
            "is_supplemental": bool(row["is_supplemental"]) if "is_supplemental" in row.keys() else False,
            "supplemental_reason": row["supplemental_reason"] if "supplemental_reason" in row.keys() else "",
            "scene_block_id": row["scene_block_id"] if "scene_block_id" in row.keys() else str(shot.get("scene_block_id") or ""),
            "scene_block_title": row["scene_block_title"] if "scene_block_title" in row.keys() else str(shot.get("scene_block_title") or ""),
            "scene_block_index": row["scene_block_index"] if "scene_block_index" in row.keys() else safe_int(shot.get("scene_block_index"), default=0),
            "sequence_id": row["sequence_id"] if "sequence_id" in row.keys() else "",
            "sequence_title": row["sequence_title"] if "sequence_title" in row.keys() else "",
            "beat_id": row["beat_id"] if "beat_id" in row.keys() else "",
            "beat_title": row["beat_title"] if "beat_title" in row.keys() else "",
            "prev_shot_id": row["prev_shot_id"] if "prev_shot_id" in row.keys() else "",
            "next_shot_id": row["next_shot_id"] if "next_shot_id" in row.keys() else "",
            "transition": row["transition"] if "transition" in row.keys() else "cut",
            "continuity": json_loads(row["continuity_json"], default={}) if "continuity_json" in row.keys() else {},
            "production_plan": json_loads(row["production_plan_json"], default={}) if "production_plan_json" in row.keys() else {},
            "prompt_parts": json_loads(row["prompt_parts_json"], default={}) if "prompt_parts_json" in row.keys() else {},
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "raw": raw,
        }
    )
    return shot


def storyboard_shot_candidate_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "candidate_id": int(row["candidate_id"]),
        "storyboard_id": row["storyboard_id"],
        "shot_id": row["shot_id"],
        "candidate_type": row["candidate_type"],
        "candidate_name": row["candidate_name"],
        "candidate_status": row["candidate_status"],
        "linked_entity_id": row["linked_entity_id"],
        "source": row["source"],
        "payload": json_loads(row["payload_json"], default={}),
        "confirmed_at": row["confirmed_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def sync_storyboard_shot_candidates(
    connection: sqlite3.Connection,
    storyboard_id: str,
    shots: list[dict[str, Any]],
    *,
    replace_storyboard: bool,
) -> None:
    if not shots:
        if replace_storyboard:
            connection.execute(
                "DELETE FROM storyboard_shot_asset_candidates WHERE storyboard_id = ?",
                (storyboard_id,),
            )
        return
    desired = []
    for shot in shots:
        desired.extend(extract_storyboard_shot_candidates(shot))
    shot_ids = sorted({str(shot.get("shot_id") or "") for shot in shots if str(shot.get("shot_id") or "")})
    if replace_storyboard:
        rows = connection.execute(
            "SELECT * FROM storyboard_shot_asset_candidates WHERE storyboard_id = ?",
            (storyboard_id,),
        ).fetchall()
    else:
        placeholders = ",".join("?" for _item in shot_ids)
        rows = connection.execute(
            f"""
            SELECT *
            FROM storyboard_shot_asset_candidates
            WHERE storyboard_id = ? AND shot_id IN ({placeholders})
            """,
            (storyboard_id, *shot_ids),
        ).fetchall()
    existing = {
        (row["shot_id"], row["candidate_type"], row["candidate_name"]): storyboard_shot_candidate_from_row(row)
        for row in rows
    }
    desired_keys = set()
    for candidate in desired:
        key = (candidate["shot_id"], candidate["candidate_type"], candidate["candidate_name"])
        desired_keys.add(key)
        previous = existing.get(key)
        linked_entity_id = candidate.get("linked_entity_id", "")
        candidate_status = "linked" if linked_entity_id else "pending"
        confirmed_at = current_timestamp() if linked_entity_id else ""
        if previous:
            if previous["candidate_status"] in {"linked", "ignored"}:
                candidate_status = previous["candidate_status"]
                linked_entity_id = previous["linked_entity_id"]
                confirmed_at = previous["confirmed_at"]
            elif linked_entity_id:
                candidate_status = "linked"
            else:
                candidate_status = previous["candidate_status"]
                confirmed_at = previous["confirmed_at"]
        connection.execute(
            """
            INSERT INTO storyboard_shot_asset_candidates (
                storyboard_id, shot_id, candidate_type, candidate_name,
                candidate_status, linked_entity_id, source, payload_json,
                confirmed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, (strftime('%Y%m%d %H%M','now','localtime')), (strftime('%Y%m%d %H%M','now','localtime')))
            ON CONFLICT(shot_id, candidate_type, candidate_name) DO UPDATE SET
                storyboard_id=excluded.storyboard_id,
                candidate_status=excluded.candidate_status,
                linked_entity_id=excluded.linked_entity_id,
                source=excluded.source,
                payload_json=excluded.payload_json,
                confirmed_at=excluded.confirmed_at,
                updated_at=(strftime('%Y%m%d %H%M','now','localtime'))
            """,
            (
                storyboard_id,
                candidate["shot_id"],
                candidate["candidate_type"],
                candidate["candidate_name"],
                candidate_status,
                linked_entity_id,
                candidate["source"],
                json_dumps(candidate.get("payload") or {}),
                confirmed_at,
            ),
        )
    for key, candidate in existing.items():
        if key in desired_keys:
            continue
        if not replace_storyboard and candidate["shot_id"] not in shot_ids:
            continue
        connection.execute(
            "DELETE FROM storyboard_shot_asset_candidates WHERE candidate_id = ?",
            (candidate["candidate_id"],),
        )


def extract_storyboard_shot_candidates(shot: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add_candidate(
        candidate_type: str,
        candidate_name: str,
        *,
        linked_entity_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        name = str(candidate_name or "").strip()
        if not name:
            return
        key = (candidate_type, name)
        if key in seen:
            return
        seen.add(key)
        candidates.append(
            {
                "storyboard_id": str(shot.get("storyboard_id") or ""),
                "shot_id": str(shot.get("shot_id") or ""),
                "candidate_type": candidate_type,
                "candidate_name": name,
                "linked_entity_id": str(linked_entity_id or ""),
                "source": "storyboard",
                "payload": {
                    "shot_index": shot.get("shot_index"),
                    "source_excerpt": shot.get("source_excerpt", ""),
                    "scene_block_id": shot.get("scene_block_id", ""),
                    **(payload or {}),
                },
            }
        )

    scene_name = str(shot.get("scene_name") or "").strip()
    scene_id = str(shot.get("scene_id") or "").strip()
    if scene_name or scene_id:
        add_candidate(
            "scene",
            scene_name or scene_id,
            linked_entity_id=scene_id,
            payload={"scene_id": scene_id, "scene_name": scene_name},
        )

    subject_ids = normalize_json_list(shot.get("subject_ids"))
    subject_names = normalize_json_list(shot.get("subject_names"))
    for index, subject_name in enumerate(subject_names):
        subject_id = subject_ids[index] if index < len(subject_ids) else ""
        add_candidate(
            "subject",
            subject_name or subject_id,
            linked_entity_id=subject_id,
            payload={"subject_id": subject_id, "subject_name": subject_name},
        )
    if len(subject_ids) > len(subject_names):
        for subject_id in subject_ids[len(subject_names) :]:
            add_candidate("subject", subject_id, linked_entity_id=subject_id, payload={"subject_id": subject_id})

    excluded_names = {scene_name, *subject_names}
    for visual_element in normalize_json_list(shot.get("visual_elements")):
        if visual_element in excluded_names:
            continue
        add_candidate("visual_element", visual_element, payload={"visual_element": visual_element})
    return candidates


def apply_candidate_link_to_storyboard_shot(
    connection: sqlite3.Connection,
    candidate: dict[str, Any],
    linked_entity_id: str,
) -> None:
    candidate_type = candidate.get("candidate_type")
    if candidate_type == "scene":
        scene_row = connection.execute(
            "SELECT canonical_name FROM visual_scenes WHERE scene_id = ?",
            (linked_entity_id,),
        ).fetchone()
        scene_name = scene_row["canonical_name"] if scene_row else candidate["candidate_name"]
        connection.execute(
            """
            UPDATE storyboard_shots
            SET scene_id = ?,
                scene_name = ?,
                updated_at = (strftime('%Y%m%d %H%M','now','localtime'))
            WHERE storyboard_id = ? AND shot_id = ?
            """,
            (linked_entity_id, scene_name, candidate["storyboard_id"], candidate["shot_id"]),
        )
        return
    if candidate_type != "subject":
        return
    row = connection.execute(
        """
        SELECT subject_ids_json, subject_names_json
        FROM storyboard_shots
        WHERE storyboard_id = ? AND shot_id = ?
        """,
        (candidate["storyboard_id"], candidate["shot_id"]),
    ).fetchone()
    if not row:
        return
    subject_row = connection.execute(
        "SELECT canonical_name FROM visual_subjects WHERE subject_id = ?",
        (linked_entity_id,),
    ).fetchone()
    subject_name = subject_row["canonical_name"] if subject_row else candidate["candidate_name"]
    subject_ids = normalize_json_list(row["subject_ids_json"])
    subject_names = normalize_json_list(row["subject_names_json"])
    if linked_entity_id not in subject_ids:
        subject_ids.append(linked_entity_id)
    if subject_name not in subject_names:
        subject_names.append(subject_name)
    connection.execute(
        """
        UPDATE storyboard_shots
        SET subject_ids_json = ?,
            subject_names_json = ?,
            updated_at = (strftime('%Y%m%d %H%M','now','localtime'))
        WHERE storyboard_id = ? AND shot_id = ?
        """,
        (json_dumps(subject_ids), json_dumps(subject_names), candidate["storyboard_id"], candidate["shot_id"]),
    )


def count_reference_assets(reference_assets: dict[str, Any]) -> int:
    anchors = (reference_assets.get("subject_anchors") or []) + (reference_assets.get("scene_anchors") or [])
    return len([anchor for anchor in anchors if isinstance(anchor, dict)])


def count_missing_reference_assets(reference_assets: dict[str, Any]) -> int:
    anchors = (reference_assets.get("subject_anchors") or []) + (reference_assets.get("scene_anchors") or [])
    return len([anchor for anchor in anchors if isinstance(anchor, dict) and not anchor.get("asset_url")])


def refresh_storyboard_totals(connection: sqlite3.Connection, storyboard_id: str) -> None:
    row = connection.execute(
        """
        SELECT COUNT(*) AS shot_count,
               COALESCE(SUM(duration_sec), 0) AS actual_duration_sec,
               COALESCE(MAX(needs_manual_review), 0) AS needs_review
        FROM storyboard_shots
        WHERE storyboard_id = ?
        """,
        (storyboard_id,),
    ).fetchone()
    status = "needs_review" if row and int(row["needs_review"] or 0) else "completed"
    connection.execute(
        """
        UPDATE storyboards
        SET shot_count = ?,
            actual_duration_sec = ?,
            status = CASE WHEN status = 'failed' THEN status ELSE ? END,
            updated_at = (strftime('%Y%m%d %H%M','now','localtime'))
        WHERE storyboard_id = ?
        """,
        (
            int(row["shot_count"] or 0) if row else 0,
            float(row["actual_duration_sec"] or 0) if row else 0,
            status,
            storyboard_id,
        ),
    )


def normalize_json_list(value: Any) -> list[str]:
    if isinstance(value, str):
        parsed = json_loads(value, default=None)
        if isinstance(parsed, list):
            value = parsed
        elif value.strip():
            value = [item.strip() for item in value.split(",")]
        else:
            value = []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def safe_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_visual_subject_payload(subject: dict[str, Any]) -> dict[str, Any]:
    raw_name = str(subject.get("canonical_name") or subject.get("name") or "").strip()
    canonical_name = canonical_visual_subject_name(raw_name, subject.get("aliases") or [])
    visual_identity = subject.get("visual_identity")
    if not isinstance(visual_identity, dict):
        visual_identity = {}
    phase_key, phase_label = visual_subject_phase_identity(subject, visual_identity)
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
            "subject_id": str(subject.get("subject_id") or stable_visual_subject_id(canonical_name, phase_key)),
            "canonical_name": canonical_name,
            "visual_phase_key": phase_key,
            "visual_phase_label": phase_label,
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
        subject["visual_phase_key"],
        subject["visual_phase_label"],
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
            "visual_phase_key": row["visual_phase_key"],
            "visual_phase_label": row["visual_phase_label"],
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


DEFAULT_VISUAL_PHASE_KEY = "default"
DEFAULT_VISUAL_PHASE_LABEL = "默认阶段"


def visual_subject_phase_identity(subject: dict[str, Any], visual_identity: dict[str, Any]) -> tuple[str, str]:
    label = first_phase_label(
        subject,
        visual_identity,
        "visual_phase_label",
        "phase_label",
        "stage_label",
        "visual_phase",
        "lifestyle_stage",
        "era_stage",
    )
    phase_text = phase_evidence_text(
        subject,
        visual_identity,
        [
            "role_in_script",
            "short_description",
            "description",
            "why_consistency_needed",
            "visual_phase_label",
            "phase_label",
            "stage_label",
            "visual_phase",
        ],
        [
            "era",
            "region",
            "appearance",
            "clothing",
            "props",
            "body_language",
            "group_composition",
            "lifestyle_stage",
            "era_stage",
        ],
    )
    if not label:
        label = infer_visual_phase_label(phase_text)
    return visual_phase_key(subject, label, phase_text), label


def visual_scene_phase_identity(scene: dict[str, Any], visual_identity: dict[str, Any]) -> tuple[str, str]:
    label = first_phase_label(
        scene,
        visual_identity,
        "visual_phase_label",
        "phase_label",
        "stage_label",
        "visual_phase",
        "environment_stage",
        "lifestyle_stage",
        "civilization_stage",
        "era_stage",
    )
    phase_text = phase_evidence_text(
        scene,
        visual_identity,
        [
            "role_in_script",
            "short_description",
            "description",
            "why_consistency_needed",
            "visual_phase_label",
            "phase_label",
            "stage_label",
            "visual_phase",
        ],
        [
            "era",
            "region",
            "terrain",
            "weather",
            "lighting",
            "palette",
            "mood",
            "typical_elements",
            "environment_stage",
            "lifestyle_stage",
            "civilization_stage",
            "era_stage",
        ],
    )
    if not label:
        label = infer_visual_phase_label(phase_text)
    return visual_phase_key(scene, label, phase_text), label


def first_phase_label(item: dict[str, Any], visual_identity: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if not value and isinstance(visual_identity, dict):
            value = visual_identity.get(key)
        text = str(value or "").strip()
        if text and text not in {DEFAULT_VISUAL_PHASE_KEY, DEFAULT_VISUAL_PHASE_LABEL}:
            return text
    return ""


def phase_evidence_text(
    item: dict[str, Any],
    visual_identity: dict[str, Any],
    item_keys: list[str],
    identity_keys: list[str],
) -> str:
    values: list[str] = []
    for key in item_keys:
        append_phase_value(values, item.get(key))
    for key in identity_keys:
        append_phase_value(values, visual_identity.get(key) if isinstance(visual_identity, dict) else None)
    return " ".join(values)


def append_phase_value(values: list[str], value: Any) -> None:
    if isinstance(value, list):
        values.extend(str(item).strip() for item in value if str(item).strip())
    elif isinstance(value, dict):
        values.extend(str(item).strip() for item in value.values() if str(item).strip())
    else:
        text = str(value or "").strip()
        if text:
            values.append(text)


def infer_visual_phase_label(text: str) -> str:
    compact = str(text or "")
    if has_any(compact, ["现代", "都市", "城市人", "西装", "牛仔", "汽车", "高楼"]):
        return "现代阶段"
    if has_any(compact, ["农业", "农耕", "定居", "村落", "播种", "收割", "谷物", "陶罐", "田地"]):
        return "农业定居阶段"
    if has_any(compact, ["采集", "狩猎", "迁徙", "旧石器", "兽皮", "石器", "木矛", "游动部落"]):
        return "采集狩猎阶段"
    if has_any(compact, ["冰河", "严寒", "寒冷", "零下"]):
        return "冰河期阶段"
    return DEFAULT_VISUAL_PHASE_LABEL


def visual_phase_key(item: dict[str, Any], label: str, evidence_text: str = "") -> str:
    explicit_key = str(item.get("visual_phase_key") or item.get("phase_key") or "").strip()
    if explicit_key:
        return normalize_visual_phase_key(explicit_key)
    category = phase_category_key(" ".join([label, evidence_text]))
    if category:
        return category
    if not label or label == DEFAULT_VISUAL_PHASE_LABEL:
        return DEFAULT_VISUAL_PHASE_KEY
    digest = hashlib.sha1(label.encode("utf-8")).hexdigest()[:10]
    return f"phase-{digest}"


def phase_category_key(text: str) -> str:
    compact = str(text or "")
    if has_any(compact, ["现代", "都市", "城市人", "西装", "牛仔", "汽车", "高楼"]):
        return "modern"
    if has_any(compact, ["农业", "农耕", "定居", "村落", "播种", "收割", "谷物", "陶罐", "田地"]):
        return "agriculture_settlement"
    if has_any(compact, ["采集", "狩猎", "迁徙", "旧石器", "兽皮", "石器", "木矛", "游动部落"]):
        return "hunter_gatherer"
    if has_any(compact, ["冰河", "严寒", "寒冷", "零下"]):
        return "ice_age"
    return ""


def normalize_visual_phase_key(value: str) -> str:
    clean = re.sub(r"\s+", "_", str(value or "").strip().lower())
    clean = re.sub(r"[^0-9a-z_\-\u4e00-\u9fff]+", "", clean)
    return clean or DEFAULT_VISUAL_PHASE_KEY


def has_any(text: str, markers: list[str]) -> bool:
    return any(marker in text for marker in markers)


def normalize_visual_scene_payload(scene: dict[str, Any]) -> dict[str, Any]:
    raw_name = str(scene.get("canonical_name") or scene.get("name") or "").strip()
    canonical_name = canonical_visual_scene_name(raw_name, scene.get("aliases") or [])
    visual_identity = scene.get("visual_identity")
    if not isinstance(visual_identity, dict):
        visual_identity = {}
    raw_visual_identity = visual_identity
    visual_identity = {
        "era": str(visual_identity.get("era") or ""),
        "environment_stage": str(
            visual_identity.get("environment_stage")
            or visual_identity.get("lifestyle_stage")
            or visual_identity.get("civilization_stage")
            or ""
        ),
        "region": str(visual_identity.get("region") or ""),
        "terrain": str(visual_identity.get("terrain") or ""),
        "weather": str(visual_identity.get("weather") or ""),
        "lighting": str(visual_identity.get("lighting") or ""),
        "palette": str(visual_identity.get("palette") or ""),
        "mood": str(visual_identity.get("mood") or ""),
        "typical_elements": [
            str(item)
            for item in (
                visual_identity.get("typical_elements")
                if isinstance(visual_identity.get("typical_elements"), list)
                else []
            )
            if str(item).strip()
        ],
    }
    phase_key, phase_label = visual_scene_phase_identity(scene, visual_identity | raw_visual_identity)
    consistency_rules = scene.get("consistency_rules")
    if not isinstance(consistency_rules, dict):
        consistency_rules = {}
    negative_rules = scene.get("negative_rules")
    if negative_rules is None:
        negative_rules = consistency_rules.get("avoid") or []
    if not isinstance(negative_rules, list):
        negative_rules = [str(negative_rules)]
    scene_type = str(scene.get("scene_type") or scene.get("type") or "").strip()
    short_description = str(
        scene.get("short_description")
        or scene.get("description")
        or scene.get("role_in_script")
        or ""
    ).strip()
    pinyin_key = scene_pinyin_key(canonical_name)
    first_letter = (pinyin_key[:1] or "Z").upper()
    normalized = dict(scene)
    normalized.update(
        {
            "scene_id": str(scene.get("scene_id") or stable_visual_scene_id(canonical_name, phase_key)),
            "canonical_name": canonical_name,
            "visual_phase_key": phase_key,
            "visual_phase_label": phase_label,
            "pinyin_key": pinyin_key,
            "first_letter": first_letter,
            "scene_type": scene_type,
            "short_description": short_description,
            "visual_identity": visual_identity,
            "consistency_rules": consistency_rules,
            "negative_rules": [str(item) for item in negative_rules if str(item).strip()],
            "status": str(scene.get("status") or "draft"),
            "anchor_asset_id": str(scene.get("anchor_asset_id") or ""),
            "visual_prompt": str(scene.get("visual_prompt") or build_scene_visual_prompt(canonical_name, visual_identity)),
            "negative_prompt": str(
                scene.get("negative_prompt")
                or "，".join([str(item) for item in consistency_rules.get("avoid", []) if str(item).strip()])
            ),
            "workflow_name": str(scene.get("workflow_name") or "scene_anchor_v1"),
            "role_in_script": str(scene.get("role_in_script") or ""),
            "importance": int(scene.get("importance") or 0),
            "first_appearance": str(scene.get("first_appearance") or ""),
            "evidence_text": str(scene.get("evidence_text") or scene.get("first_appearance") or ""),
            "extraction_confidence": str(scene.get("extraction_confidence") or ""),
        }
    )
    return normalized


def visual_scene_values(scene: dict[str, Any]) -> tuple[Any, ...]:
    return (
        scene["scene_id"],
        scene["canonical_name"],
        scene["visual_phase_key"],
        scene["visual_phase_label"],
        scene["pinyin_key"],
        scene["first_letter"],
        scene["scene_type"],
        scene["short_description"],
        json_dumps(scene.get("visual_identity") or {}),
        json_dumps(scene.get("consistency_rules") or {}),
        json_dumps(scene.get("negative_rules") or []),
        scene.get("status", "draft"),
        scene.get("anchor_asset_id", ""),
        scene.get("visual_prompt", ""),
        scene.get("negative_prompt", ""),
        scene.get("workflow_name", ""),
        json_dumps(scene),
    )


def script_visual_scene_values(generation_id: str, scene: dict[str, Any]) -> tuple[Any, ...]:
    return (
        generation_id,
        scene["scene_id"],
        scene.get("role_in_script", ""),
        int(scene.get("importance") or 0),
        scene.get("first_appearance", ""),
        scene.get("evidence_text", ""),
        scene.get("extraction_confidence", ""),
        json_dumps(scene),
    )


def visual_scene_from_row(row: sqlite3.Row) -> dict[str, Any]:
    raw = json_loads(row["raw_json"], default={})
    visual_identity = json_loads(row["visual_identity_json"], default={})
    consistency_rules = json_loads(row["consistency_rules_json"], default={})
    negative_rules = json_loads(row["negative_rules_json"], default=[])
    scene = dict(raw)
    scene.update(
        {
            "scene_id": row["scene_id"],
            "canonical_name": row["canonical_name"],
            "visual_phase_key": row["visual_phase_key"],
            "visual_phase_label": row["visual_phase_label"],
            "pinyin_key": row["pinyin_key"],
            "first_letter": row["first_letter"],
            "scene_type": row["scene_type"],
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
    return scene


def script_visual_scene_from_row(row: sqlite3.Row) -> dict[str, Any]:
    scene = visual_scene_from_row(row)
    raw = json_loads(row["script_scene_raw_json"], default={})
    scene.update(
        {
            "generation_id": row["generation_id"],
            "role_in_script": row["role_in_script"],
            "importance": row["importance"],
            "first_appearance": row["first_appearance"],
            "evidence_text": row["evidence_text"],
            "extraction_confidence": row["extraction_confidence"],
            "raw": raw,
            "is_global_scene": True,
        }
    )
    return scene


def visual_scene_appearance_from_row(row: sqlite3.Row) -> dict[str, Any]:
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


def stable_visual_scene_id(canonical_name: str, visual_phase_key: str = DEFAULT_VISUAL_PHASE_KEY) -> str:
    digest = hashlib.sha1(f"{canonical_name}|{visual_phase_key}".encode("utf-8")).hexdigest()[:12]
    return f"vsc-{digest}"


def canonical_visual_scene_name(name: str, aliases: Any = None) -> str:
    clean_name = str(name or "").strip()
    alias_values = aliases if isinstance(aliases, list) else []
    values = {clean_name, *[str(alias).strip() for alias in alias_values]}
    if values & {"非洲东部稀树草原", "稀树草原", "非洲东部的稀树草原"}:
        return "东非稀树草原"
    if values & {"智人部落营地", "非洲部落营地"}:
        return "非洲智人部落营地"
    if values & {"红海渡口", "跨过红海"}:
        return "红海海口迁徙渡口"
    return clean_name


SCENE_PINYIN_OVERRIDES = {
    "布隆伯斯洞穴": "bulongbosidongxue",
    "冰河期欧洲尼安德特人营地": "bingheqiouzhouniandeterenyingdi",
    "东非稀树草原": "dongfeixishucaoyuan",
    "洞穴壁画与葬礼仪式空间": "dongxuebihuayuzangliyishikongjian",
    "非洲智人部落营地": "feizhouzhirenbuluoyingdi",
    "篝火烹饪营地": "gouhuopengrenyingdi",
    "红海海口迁徙渡口": "honghaihaikouqianxidukou",
    "黎凡特冰河期遭遇地带": "lifantebingheqizaoyudidai",
    "多巴火山灾变": "duobahuoshanzaibian",
    "印度洋海岸迁徙路线": "yinduyanghaianqianxiluxian",
    "巽他大陆尽头海峡": "xuntadalujintouhaixia",
}


def scene_pinyin_key(name: str) -> str:
    clean_name = str(name or "").strip()
    if clean_name in SCENE_PINYIN_OVERRIDES:
        return SCENE_PINYIN_OVERRIDES[clean_name]
    if clean_name and clean_name[0].isascii():
        return re.sub(r"[^0-9a-z]+", "", clean_name.lower()) or "z"
    return f"z{clean_name}"


def build_scene_visual_prompt(canonical_name: str, visual_identity: dict[str, Any]) -> str:
    parts = [
        canonical_name,
        str(visual_identity.get("era") or ""),
        str(visual_identity.get("region") or ""),
        str(visual_identity.get("terrain") or ""),
        str(visual_identity.get("weather") or ""),
        str(visual_identity.get("lighting") or ""),
        str(visual_identity.get("palette") or ""),
        str(visual_identity.get("mood") or ""),
        "、".join(str(item) for item in visual_identity.get("typical_elements") or []),
    ]
    return "，".join(part for part in parts if part)


def group_visual_scenes(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for scene in scenes:
        letter = str(scene.get("first_letter") or "#").upper()
        grouped.setdefault(letter, []).append(scene)
    return [
        {"letter": letter, "scenes": grouped[letter], "count": len(grouped[letter])}
        for letter in sorted(grouped)
    ]


def stable_visual_subject_id(canonical_name: str, visual_phase_key: str = DEFAULT_VISUAL_PHASE_KEY) -> str:
    digest = hashlib.sha1(f"{canonical_name}|{visual_phase_key}".encode("utf-8")).hexdigest()[:12]
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


def backfill_visual_phase_metadata(connection: sqlite3.Connection) -> None:
    backfill_visual_subject_phases(connection)
    backfill_visual_scene_phases(connection)


def backfill_visual_subject_phases(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT subject_id, canonical_name, subject_type, short_description,
               visual_identity_json, raw_json, visual_phase_key, visual_phase_label
        FROM visual_subjects
        """
    ).fetchall()
    for row in rows:
        current_key = str(row["visual_phase_key"] or "").strip()
        current_label = str(row["visual_phase_label"] or "").strip()
        if current_key not in {"", DEFAULT_VISUAL_PHASE_KEY} and current_label not in {"", DEFAULT_VISUAL_PHASE_LABEL}:
            continue
        raw = json_loads(row["raw_json"], default={})
        payload = raw if isinstance(raw, dict) else {}
        payload = dict(payload)
        payload.setdefault("canonical_name", row["canonical_name"])
        payload.setdefault("subject_type", row["subject_type"])
        payload.setdefault("short_description", row["short_description"])
        payload.setdefault("visual_identity", json_loads(row["visual_identity_json"], default={}))
        normalized = normalize_visual_subject_payload(payload)
        connection.execute(
            """
            UPDATE visual_subjects
            SET visual_phase_key = ?,
                visual_phase_label = ?,
                raw_json = ?
            WHERE subject_id = ?
            """,
            (
                normalized["visual_phase_key"],
                normalized["visual_phase_label"],
                json_dumps(normalized),
                row["subject_id"],
            ),
        )


def backfill_visual_scene_phases(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT scene_id, canonical_name, scene_type, short_description,
               visual_identity_json, raw_json, visual_phase_key, visual_phase_label
        FROM visual_scenes
        """
    ).fetchall()
    for row in rows:
        current_key = str(row["visual_phase_key"] or "").strip()
        current_label = str(row["visual_phase_label"] or "").strip()
        if current_key not in {"", DEFAULT_VISUAL_PHASE_KEY} and current_label not in {"", DEFAULT_VISUAL_PHASE_LABEL}:
            continue
        raw = json_loads(row["raw_json"], default={})
        payload = raw if isinstance(raw, dict) else {}
        payload = dict(payload)
        payload.setdefault("canonical_name", row["canonical_name"])
        payload.setdefault("scene_type", row["scene_type"])
        payload.setdefault("short_description", row["short_description"])
        payload.setdefault("visual_identity", json_loads(row["visual_identity_json"], default={}))
        normalized = normalize_visual_scene_payload(payload)
        connection.execute(
            """
            UPDATE visual_scenes
            SET visual_phase_key = ?,
                visual_phase_label = ?,
                raw_json = ?
            WHERE scene_id = ?
            """,
            (
                normalized["visual_phase_key"],
                normalized["visual_phase_label"],
                json_dumps(normalized),
                row["scene_id"],
            ),
        )


def current_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d %H%M")


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
