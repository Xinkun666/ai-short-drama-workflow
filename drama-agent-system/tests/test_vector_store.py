import json
import sqlite3

from drama_agents.vector_store import LocalVectorStore, build_material_chunks


def test_local_vector_store_indexes_and_retrieves_relevant_chunks(tmp_path):
    store = LocalVectorStore(tmp_path / "rag.sqlite3")
    store.upsert_chunks(
        [
            {
                "chunk_id": "demo:timeline:fire",
                "record_id": "demo",
                "source_type": "timeline",
                "source_ref": "ch01-e001",
                "title": "火与烹饪",
                "text": "火和烹饪减少咀嚼时间，释放能量供给大脑。",
                "metadata": {"pages": [1, 2]},
            },
            {
                "chunk_id": "demo:timeline:farming",
                "record_id": "demo",
                "source_type": "timeline",
                "source_ref": "ch02-e001",
                "title": "农业",
                "text": "农业定居改变了粮食生产方式。",
                "metadata": {"pages": [9]},
            },
        ]
    )

    results = store.search("为什么火和烹饪会影响大脑能量", record_ids=["demo"], limit=1)

    assert results[0]["chunk_id"] == "demo:timeline:fire"
    assert results[0]["score"] > 0


def test_material_chunks_index_book_content_not_timeline_modules(tmp_path):
    outputs = tmp_path / "outputs"
    raw_text = outputs / "material_splits" / "demo" / "chapters_text" / "ch01.md"
    raw_text.parent.mkdir(parents=True)
    raw_text.write_text("原始章节正文：智人通过狩猎采集、火和协作逐步改变生态位置。", encoding="utf-8")
    reader_json = outputs / "material_splits" / "demo" / "reader" / "ch01_reader.json"
    reader_json.parent.mkdir(parents=True)
    reader_json.write_text(
        json.dumps(
            {
                "summary": "精读摘要：本章解释文化能力如何影响人类扩张。",
                "sections": [
                    {
                        "heading": "文化能力",
                        "body": "精读正文：文化能力和想象力让人类能组织更大的群体。",
                        "page_refs": [1, 2],
                    }
                ],
                "key_concepts": ["文化能力"],
                "drama_tags": ["人类扩张"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    database = FakeMaterialDatabase(
        chapters=[
            {
                "chapter_id": "ch01",
                "title": "第一章",
                "start_page": 1,
                "end_page": 2,
                "text_path": str(raw_text),
                "reader_json_link": "",
                "word_count": 24,
                "include_in_analysis": True,
            }
        ],
        events=[
            {
                "event_id": "ch01-e001",
                "title": "时间线模块",
                "content": "时间线独有术语不应该进入书本向量库。",
            }
        ],
    )

    chunks = build_material_chunks(database, outputs, ["demo"])

    assert chunks
    assert {chunk["source_type"] for chunk in chunks} == {"chapter", "reader"}
    assert any("原始章节正文" in chunk["text"] for chunk in chunks)
    assert any("精读正文" in chunk["text"] for chunk in chunks)
    assert all("时间线独有术语" not in chunk["text"] for chunk in chunks)


def test_replace_record_chunks_removes_stale_timeline_chunks(tmp_path):
    store = LocalVectorStore(tmp_path / "rag.sqlite3")
    store.upsert_chunks(
        [
            {
                "chunk_id": "demo:timeline:old",
                "record_id": "demo",
                "source_type": "timeline",
                "source_ref": "old",
                "title": "旧时间线",
                "text": "时间线独有术语",
                "metadata": {},
            },
            {
                "chunk_id": "other:chapter:1",
                "record_id": "other",
                "source_type": "chapter",
                "source_ref": "ch01",
                "title": "其他书",
                "text": "其他书正文内容",
                "metadata": {},
            },
        ]
    )

    count = store.replace_record_chunks(
        ["demo"],
        [
            {
                "chunk_id": "demo:chapter:ch01:1",
                "record_id": "demo",
                "source_type": "chapter",
                "source_ref": "ch01",
                "title": "第一章",
                "text": "书本正文内容",
                "metadata": {},
            }
        ],
    )

    assert count == 1
    with sqlite3.connect(store.path) as connection:
        stale_count = connection.execute(
            "SELECT COUNT(*) FROM rag_chunks WHERE chunk_id = ?",
            ("demo:timeline:old",),
        ).fetchone()[0]
    assert stale_count == 0
    assert store.search("其他书正文内容", record_ids=["other"])


class FakeMaterialDatabase:
    def __init__(self, *, chapters, events):
        self.chapters = chapters
        self.events = events

    def find_record(self, record_id):
        return {"record_id": record_id, "book_name": "测试书", "output_relative_path": "material_splits/demo"}

    def list_chapters(self, record_id):
        return self.chapters

    def list_timeline_events(self, record_id):
        return self.events
