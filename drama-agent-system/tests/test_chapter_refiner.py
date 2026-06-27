import json
from pathlib import Path

from tests.test_material_splitter import create_outline_pdf

from drama_agents.chapter_refiner import ChapterRefiner, DeepSeekProvider
from drama_agents.material_splitter import MaterialSplitter


class FakeDeepSeekProvider:
    def refine(self, *, book_title, chapter, raw_text):
        return {
            "title": f"精读：{chapter.title}",
            "subtitle": "章节内容整理",
            "summary": "这一章介绍世界史写作的核心问题。",
            "sections": [
                {
                    "heading": "核心内容",
                    "body": "这是经过 DeepSeek 精提取后的章节正文，保留标题、内容和自然段落。",
                    "page_refs": [chapter.start_page, chapter.end_page],
                }
            ],
            "visual_assets": [
                {
                    "type": "figure",
                    "title": "图示线索",
                    "description": "原文中的图、表、地图会在这里保留提示。",
                }
            ],
            "key_concepts": ["world history", "periodization"],
            "drama_tags": ["文明演化", "方法论"],
        }


def test_chapter_refiner_writes_reader_artifacts(tmp_path):
    source = tmp_path / "source.pdf"
    split_dir = tmp_path / "split"
    create_outline_pdf(source)
    split_result = MaterialSplitter(split_dir).split_pdf(source)

    result = ChapterRefiner(provider=FakeDeepSeekProvider()).refine_book(split_result)

    assert result.status == "completed"
    assert result.refined_count == 2
    first = result.chapters[0]
    assert first.chapter_id == "ch01"
    assert Path(first.reader_json_path).exists()
    assert Path(first.reader_markdown_path).exists()
    assert Path(first.reader_html_path).exists()

    payload = json.loads(Path(first.reader_json_path).read_text(encoding="utf-8"))
    assert payload["title"] == "精读：1 Introduction and overview"
    assert payload["sections"][0]["heading"] == "核心内容"
    assert payload["visual_assets"][0]["type"] == "figure"
    assert "world history" in payload["key_concepts"]

    markdown = Path(first.reader_markdown_path).read_text(encoding="utf-8")
    html = Path(first.reader_html_path).read_text(encoding="utf-8")
    assert "## 核心内容" in markdown
    assert "这是经过 DeepSeek 精提取后的章节正文" in html


def test_chapter_refiner_records_missing_provider_without_failing_split(tmp_path):
    source = tmp_path / "source.pdf"
    split_dir = tmp_path / "split"
    create_outline_pdf(source)
    split_result = MaterialSplitter(split_dir).split_pdf(source)

    result = ChapterRefiner(provider=None).refine_book(split_result)

    assert result.status == "skipped"
    assert result.refined_count == 0
    assert "DEEPSEEK_API_KEY" in result.message


def test_chapter_refiner_provider_defaults_to_deepseek_v4_pro():
    provider = DeepSeekProvider(api_key="test-key")

    assert provider.model == "deepseek-v4-pro"
