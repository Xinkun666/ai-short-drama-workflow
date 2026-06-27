import json
from pathlib import Path

from tests.test_material_splitter import create_outline_pdf

from drama_agents.material_splitter import MaterialSplitter
from drama_agents.timeline_builder import DeepSeekTimelineProvider, TimelineBuilder, build_timeline_prompt


class FakeTimelineProvider:
    def __init__(self):
        self.calls = 0

    def extract_timeline(self, *, book_title, chapter, raw_text, reader_payload):
        self.calls += 1
        return {
            "events": [
                {
                    "time_label": "约公元前 200000 年至公元前 50000 年",
                    "time_start_year": -200000,
                    "time_end_year": -50000,
                    "time_precision": "range",
                    "place_label": "非洲及早期智人扩散区域",
                    "place_scope": "region",
                    "places": ["非洲"],
                    "movement": None,
                    "title": f"{chapter.chapter_id} 智人文化能力逐步形成",
                    "content": "本事件模块说明智人出现后，文化能力、想象力和环境适应方式如何逐步形成。",
                    "source_pages": [chapter.start_page, chapter.end_page],
                    "importance": 4,
                    "confidence": "medium",
                    "evidence_note": "原文给出章节时间范围，地点依据章节主题概括为非洲及早期扩散区域。",
                    "drama_potential": "可作为文明能力觉醒的前史节点。",
                }
            ]
        }


class FailingTimelineProvider:
    def extract_timeline(self, *, book_title, chapter, raw_text, reader_payload):
        raise AssertionError("completed chapter timelines should be reused")


class ExternalKnowledgeTimelineProvider:
    def extract_timeline(self, *, book_title, chapter, raw_text, reader_payload):
        return {
            "events": [
                {
                    "time_label": "约公元前 200000 年至公元前 50000 年",
                    "time_start_year": -200000,
                    "time_end_year": -50000,
                    "time_precision": "range",
                    "place_label": "非洲",
                    "place_scope": "region",
                    "places": ["非洲"],
                    "movement": None,
                    "title": "原文事件",
                    "content": "这是完全来自原文的事件复述。",
                    "source_pages": [chapter.start_page],
                    "importance": 4,
                    "confidence": "high",
                    "evidence_note": "时间地点和内容均由原文明确给出。",
                    "drama_potential": "可用。",
                },
                {
                    "time_label": "青铜时代",
                    "time_start_year": -3000,
                    "time_end_year": -1200,
                    "time_precision": "range",
                    "place_label": "欧亚大陆",
                    "place_scope": "region",
                    "places": ["欧亚大陆"],
                    "movement": None,
                    "title": "外部补充事件",
                    "content": "这是根据一般世界史知识补充出来的内容。",
                    "source_pages": [chapter.start_page],
                    "importance": 2,
                    "confidence": "low",
                    "evidence_note": "此处具体时间范围依据一般世界史知识推断。",
                    "drama_potential": "不可用。",
                },
            ]
        }


def test_timeline_builder_writes_book_and_chapter_artifacts(tmp_path):
    source = tmp_path / "source.pdf"
    split_dir = tmp_path / "split"
    create_outline_pdf(source)
    split_result = MaterialSplitter(split_dir).split_pdf(source)

    result = TimelineBuilder(provider=FakeTimelineProvider()).build_book(split_result)

    assert result.status == "completed"
    assert result.event_count == 2
    assert Path(result.timeline_json_path).exists()
    assert Path(result.timeline_markdown_path).exists()
    assert len(result.chapters) == 2
    assert Path(result.chapters[0].timeline_json_path).exists()

    payload = json.loads(Path(result.timeline_json_path).read_text(encoding="utf-8"))
    first = payload["events"][0]
    assert first["event_id"] == "ch01-e001"
    assert first["chapter_id"] == "ch01"
    assert first["time_start_year"] == -200000
    assert first["time_end_year"] == -50000
    assert first["confidence"] == "medium"
    assert "章节时间范围" in first["evidence_note"]
    assert first["importance"] == 4

    markdown = Path(result.timeline_markdown_path).read_text(encoding="utf-8")
    assert "## ch01-e001" in markdown
    assert "非洲及早期智人扩散区域" in markdown


def test_timeline_builder_reuses_completed_chapter_artifacts(tmp_path):
    source = tmp_path / "source.pdf"
    split_dir = tmp_path / "split"
    create_outline_pdf(source)
    split_result = MaterialSplitter(split_dir).split_pdf(source)
    TimelineBuilder(provider=FakeTimelineProvider()).build_book(split_result)

    result = TimelineBuilder(provider=FailingTimelineProvider()).build_book(split_result)

    assert result.status == "completed"
    assert result.event_count == 2


def test_timeline_builder_can_force_rebuild_completed_chapter_artifacts(tmp_path):
    source = tmp_path / "source.pdf"
    split_dir = tmp_path / "split"
    create_outline_pdf(source)
    split_result = MaterialSplitter(split_dir).split_pdf(source)
    TimelineBuilder(provider=FakeTimelineProvider()).build_book(split_result)
    provider = FakeTimelineProvider()

    result = TimelineBuilder(provider=provider).build_book(split_result, force=True)

    assert result.status == "completed"
    assert result.event_count == 2
    assert provider.calls == 2


def test_timeline_prompt_requires_full_source_based_content_without_extra_fields(tmp_path):
    source = tmp_path / "source.pdf"
    split_dir = tmp_path / "split"
    create_outline_pdf(source)
    split_result = MaterialSplitter(split_dir).split_pdf(source)
    chapter = split_result.chapters[0]

    prompt = build_timeline_prompt(
        "Demo Book",
        chapter,
        "这是一段原文，围绕某个时间地点连续讲了背景、过程和影响。",
        {"title": "精读章节", "summary": "章节摘要", "sections": []},
    )

    assert "不要新增 background、cause、process、impact、evolution" in prompt
    assert "content 必须基于原文尽量完整复述" in prompt
    assert "如果原文有多段都围绕同一个时间地点" in prompt
    assert "不要只写一句摘要" in prompt
    assert "不得引入原文没有的外部常识" in prompt


def test_timeline_provider_defaults_to_deepseek_v4_pro():
    provider = DeepSeekTimelineProvider(api_key="test-key")

    assert provider.model == "deepseek-v4-pro"


def test_timeline_builder_filters_events_that_admit_external_knowledge(tmp_path):
    source = tmp_path / "source.pdf"
    split_dir = tmp_path / "split"
    create_outline_pdf(source)
    split_result = MaterialSplitter(split_dir).split_pdf(source)

    result = TimelineBuilder(provider=ExternalKnowledgeTimelineProvider()).build_book(split_result)

    payload = json.loads(Path(result.timeline_json_path).read_text(encoding="utf-8"))
    assert result.event_count == 2
    assert all(event["title"] == "原文事件" for event in payload["events"])


def test_timeline_builder_records_missing_provider_without_failing(tmp_path):
    source = tmp_path / "source.pdf"
    split_dir = tmp_path / "split"
    create_outline_pdf(source)
    split_result = MaterialSplitter(split_dir).split_pdf(source)

    result = TimelineBuilder(provider=None).build_book(split_result)

    assert result.status == "skipped"
    assert result.event_count == 0
    assert "DEEPSEEK_API_KEY" in result.message
