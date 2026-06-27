from pathlib import Path

from drama_agents.storage import MaterialDatabase


def demo_record():
    return {
        "record_id": "demo",
        "parsed_at": "2026-06-20 13:00:00",
        "book_name": "demo.pdf",
        "source_relative_path": "资料库/demo.pdf",
        "page_count": 12,
        "total_words": 3456,
        "chapter_count": 1,
        "excluded_count": 1,
        "refinement_status": "completed",
        "refinement_message": "章节内容精提取完成。",
        "refined_chapter_count": 1,
        "timeline_status": "not_run",
        "timeline_message": "",
        "timeline_event_count": 0,
        "timeline_url": "/materials/demo/timeline",
        "output_relative_path": "material_splits/demo",
        "detail_url": "/materials/demo",
        "links": {"manifest": "/outputs/material_splits/demo/manifest.json"},
    }


def demo_result_data():
    return {
        "warnings": ["章节边界请复核"],
        "chapters": [
            {
                "chapter_id": "ch01",
                "section_id": "sec001",
                "title": "1 First chapter",
                "start_page": 1,
                "end_page": 10,
                "kind": "chapter",
                "include_in_analysis": True,
                "pdf_path": "/tmp/ch01.pdf",
                "text_path": "/tmp/ch01.md",
                "word_count": 3456,
                "source_format": "pdf",
                "source_path": "/tmp/demo.pdf",
                "reader_status": "completed",
                "reader_message": "completed",
                "reader_json_link": "/outputs/reader/ch01.json",
            }
        ],
        "excluded_sections": [
            {
                "section_id": "sec002",
                "title": "Index",
                "start_page": 11,
                "end_page": 12,
                "kind": "reference",
                "include_in_analysis": False,
            }
        ],
    }


def demo_timeline_json(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """
        {
          "events": [
            {
              "event_id": "ch01-e001",
              "chapter_id": "ch01",
              "time_label": "公元前 10000 年",
              "time_start_year": -10000,
              "time_end_year": -10000,
              "time_precision": "year",
              "place_label": "西亚",
              "place_scope": "region",
              "places": ["西亚"],
              "title": "农业线索出现",
              "content": "原文围绕这个节点讲述农业线索。",
              "source_pages": [1, 3],
              "importance": 4,
              "confidence": "high",
              "evidence_note": "原文明确给出时间和地区。",
              "drama_potential": "适合作为文明转折点。"
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    return path


def test_material_database_persists_record_chapters_and_excluded_sections(tmp_path):
    db = MaterialDatabase(tmp_path / "materials.sqlite3")

    db.upsert_parse(demo_record(), demo_result_data())

    records = db.list_records()
    assert records[0]["record_id"] == "demo"
    assert records[0]["links"]["manifest"] == "/outputs/material_splits/demo/manifest.json"

    chapters = db.list_chapters("demo")
    assert chapters[0]["chapter_id"] == "ch01"
    assert chapters[0]["title"] == "1 First chapter"
    assert chapters[0]["reader_json_link"] == "/outputs/reader/ch01.json"

    excluded = db.list_excluded_sections("demo")
    assert excluded[0]["title"] == "Index"


def test_material_database_updates_timeline_and_events(tmp_path):
    db = MaterialDatabase(tmp_path / "materials.sqlite3")
    db.upsert_parse(demo_record(), demo_result_data())
    timeline_path = demo_timeline_json(tmp_path / "timeline" / "timeline.json")

    updated = db.update_timeline(
        "demo",
        {
            "status": "completed",
            "message": "全书时间线生成完成。",
            "event_count": 1,
            "timeline_json_path": str(timeline_path),
            "timeline_markdown_path": str(tmp_path / "timeline" / "timeline.md"),
        },
        links={"timeline_json": "/outputs/material_splits/demo/timeline/timeline.json"},
    )

    assert updated["timeline_status"] == "completed"
    assert updated["timeline_event_count"] == 1

    events = db.list_timeline_events("demo")
    assert events[0]["event_id"] == "ch01-e001"
    assert events[0]["title"] == "农业线索出现"
    assert events[0]["source_pages"] == [1, 3]


def test_material_database_delete_record_cascades_child_rows(tmp_path):
    db = MaterialDatabase(tmp_path / "materials.sqlite3")
    db.upsert_parse(demo_record(), demo_result_data())
    db.delete_record("demo")

    assert db.list_records() == []
    assert db.list_chapters("demo") == []
    assert db.list_excluded_sections("demo") == []


def test_material_database_updates_book_name_without_changing_record_id(tmp_path):
    db = MaterialDatabase(tmp_path / "materials.sqlite3")
    db.upsert_parse(demo_record(), demo_result_data())

    updated = db.update_book_name("demo", "世界史手册")

    assert updated["record_id"] == "demo"
    assert updated["book_name"] == "世界史手册"
    assert db.find_record("demo")["book_name"] == "世界史手册"
