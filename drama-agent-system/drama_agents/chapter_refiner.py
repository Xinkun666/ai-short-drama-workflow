from __future__ import annotations

import html
import json
import os
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class RefinedChapter:
    chapter_id: str
    title: str
    status: str
    message: str
    reader_json_path: str
    reader_markdown_path: str
    reader_html_path: str


@dataclass
class RefinementResult:
    status: str
    message: str
    refined_count: int
    chapters: list[RefinedChapter]


class ChapterRefiner:
    def __init__(self, provider=None):
        self.provider = provider

    @classmethod
    def from_environment(cls):
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            return cls(provider=None)
        return cls(provider=DeepSeekProvider(api_key=api_key))

    def refine_book(self, split_result) -> RefinementResult:
        if not self.provider:
            return RefinementResult(
                status="skipped",
                message="未配置 DEEPSEEK_API_KEY，已跳过章节内容精提取。",
                refined_count=0,
                chapters=[],
            )

        output_dir = Path(split_result.output_dir)
        reader_dir = output_dir / "reader"
        reader_dir.mkdir(parents=True, exist_ok=True)
        max_workers = min(max(1, int(os.environ.get("DEEPSEEK_MAX_WORKERS", "3"))), len(split_result.chapters))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            refined = list(
                executor.map(
                    lambda chapter: self._refine_chapter(split_result, reader_dir, chapter),
                    split_result.chapters,
                )
            )

        failures = [f"{chapter.chapter_id}: {chapter.message}" for chapter in refined if chapter.status != "completed"]

        completed = [chapter for chapter in refined if chapter.status == "completed"]
        status = "completed" if len(completed) == len(refined) else "partial"
        message = "章节内容精提取完成。" if status == "completed" else "部分章节精提取失败，已保留失败说明。"
        if failures:
            message = f"{message} {'; '.join(failures[:3])}"
        return RefinementResult(
            status=status,
            message=message,
            refined_count=len(completed),
            chapters=refined,
        )

    def _refine_chapter(self, split_result, reader_dir: Path, chapter) -> RefinedChapter:
        try:
            raw_text = Path(chapter.text_path).read_text(encoding="utf-8")
            payload = self.provider.refine(
                book_title=Path(split_result.source_file).stem,
                chapter=chapter,
                raw_text=raw_text,
            )
            normalized = normalize_refined_payload(payload, chapter)
            paths = write_reader_artifacts(reader_dir, chapter, normalized)
            return RefinedChapter(
                chapter_id=chapter.chapter_id,
                title=normalized["title"],
                status="completed",
                message="completed",
                reader_json_path=str(paths["json"]),
                reader_markdown_path=str(paths["markdown"]),
                reader_html_path=str(paths["html"]),
            )
        except Exception as exc:
            paths = write_reader_artifacts(reader_dir, chapter, fallback_payload(chapter, str(exc)))
            return RefinedChapter(
                chapter_id=chapter.chapter_id,
                title=chapter.title,
                status="failed",
                message=str(exc),
                reader_json_path=str(paths["json"]),
                reader_markdown_path=str(paths["markdown"]),
                reader_html_path=str(paths["html"]),
            )


class DeepSeekProvider:
    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        base_url: str = "https://api.deepseek.com/chat/completions",
        max_chars: int = 28000,
    ):
        self.api_key = api_key
        self.model = model or os.environ.get("DEEPSEEK_REFINER_MODEL") or os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
        self.base_url = base_url
        self.max_chars = max_chars

    def refine(self, *, book_title, chapter, raw_text):
        source_text = normalize_source_text(raw_text)
        if len(source_text) > self.max_chars:
            source_text = source_text[: self.max_chars] + "\n\n[系统提示：原章节过长，本次先处理前半部分，后续版本将启用分块合并。]"
        prompt = build_refinement_prompt(book_title, chapter, source_text)
        try:
            return self._request_refinement(prompt)
        except json.JSONDecodeError:
            repair_prompt = (
                f"{prompt}\n\n"
                "重要：上一次输出不是合法 JSON。请重新输出，必须是严格合法 JSON："
                "所有字符串必须正确转义，不能有尾随逗号，不能有 Markdown 代码围栏。"
            )
            return self._request_refinement(repair_prompt)

    def _request_refinement(self, prompt: str):
        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是严谨的历史资料整理助手。只输出合法 JSON，不要输出 Markdown 代码围栏。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"DeepSeek HTTP {exc.code}: {detail[:300]}") from exc
        content = data["choices"][0]["message"]["content"]
        return parse_json_object(content)


def build_refinement_prompt(book_title: str, chapter, source_text: str) -> str:
    return f"""
请对下面这章历史材料进行精准内容提取与阅读化排版。

要求：
1. 保留原章节标题含义，必要时优化成自然中文阅读标题。
2. 按原文逻辑整理成多个小节，不要改写成短剧脚本。
3. 尽量保留图、表、地图、专名、页码提示、重要概念。
4. 不要虚构原文没有的信息；不确定时写入 visual_assets 或 summary 的提示。
5. 输出 JSON，字段必须包含：
   title, subtitle, summary, sections, visual_assets, key_concepts, drama_tags
6. sections 每项包含 heading, body, page_refs。
7. visual_assets 每项包含 type, title, description。

书名：{book_title}
章节：{chapter.chapter_id} {chapter.title}
页码：{chapter.start_page}-{chapter.end_page}

原始章节文本：
{source_text}
""".strip()


def normalize_refined_payload(payload: dict[str, Any], chapter) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("DeepSeek 返回不是 JSON object")
    sections = payload.get("sections") or []
    if not isinstance(sections, list) or not sections:
        sections = [
            {
                "heading": "正文整理",
                "body": payload.get("summary") or "DeepSeek 未返回有效小节正文。",
                "page_refs": [chapter.start_page, chapter.end_page],
            }
        ]
    return {
        "chapter_id": chapter.chapter_id,
        "source_title": chapter.title,
        "title": str(payload.get("title") or chapter.title),
        "subtitle": str(payload.get("subtitle") or f"页码 {chapter.start_page}-{chapter.end_page}"),
        "summary": str(payload.get("summary") or ""),
        "page_range": [chapter.start_page, chapter.end_page],
        "sections": [normalize_section(section, chapter) for section in sections],
        "visual_assets": normalize_assets(payload.get("visual_assets") or []),
        "key_concepts": normalize_string_list(payload.get("key_concepts") or []),
        "drama_tags": normalize_string_list(payload.get("drama_tags") or []),
        "status": "completed",
        "message": "completed",
    }


def normalize_section(section: dict[str, Any], chapter) -> dict[str, Any]:
    if not isinstance(section, dict):
        section = {"heading": "正文整理", "body": str(section)}
    page_refs = section.get("page_refs") or [chapter.start_page, chapter.end_page]
    if not isinstance(page_refs, list):
        page_refs = [page_refs]
    return {
        "heading": str(section.get("heading") or "正文整理"),
        "body": str(section.get("body") or ""),
        "page_refs": page_refs,
    }


def normalize_assets(items: list[Any]) -> list[dict[str, str]]:
    assets = []
    for item in items:
        if not isinstance(item, dict):
            continue
        assets.append(
            {
                "type": str(item.get("type") or "note"),
                "title": str(item.get("title") or "图表提示"),
                "description": str(item.get("description") or ""),
            }
        )
    return assets


def normalize_string_list(items: list[Any]) -> list[str]:
    return [str(item) for item in items if str(item).strip()]


def fallback_payload(chapter, message: str) -> dict[str, Any]:
    return {
        "chapter_id": chapter.chapter_id,
        "source_title": chapter.title,
        "title": chapter.title,
        "subtitle": "章节精提取失败",
        "summary": message,
        "page_range": [chapter.start_page, chapter.end_page],
        "sections": [
            {
                "heading": "需要重新提取",
                "body": f"本章 DeepSeek 精提取失败：{message}",
                "page_refs": [chapter.start_page, chapter.end_page],
            }
        ],
        "visual_assets": [],
        "key_concepts": [],
        "drama_tags": [],
        "status": "failed",
        "message": message,
    }


def write_reader_artifacts(reader_dir: Path, chapter, payload: dict[str, Any]) -> dict[str, Path]:
    base = f"{chapter.chapter_id}_reader"
    json_path = reader_dir / f"{base}.json"
    markdown_path = reader_dir / f"{base}.md"
    html_path = reader_dir / f"{base}.html"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_reader_markdown(payload), encoding="utf-8")
    html_path.write_text(render_reader_html(payload), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path, "html": html_path}


def render_reader_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# {payload['title']}",
        "",
        f"> {payload.get('subtitle', '')}",
        "",
        payload.get("summary", ""),
        "",
    ]
    for section in payload.get("sections", []):
        refs = ", ".join(str(page) for page in section.get("page_refs", []))
        lines.extend([f"## {section['heading']}", "", section.get("body", ""), "", f"页码：{refs}", ""])
    if payload.get("visual_assets"):
        lines.extend(["## 图表与视觉提示", ""])
        for asset in payload["visual_assets"]:
            lines.append(f"- {asset['type']}：{asset['title']} - {asset['description']}")
        lines.append("")
    if payload.get("key_concepts"):
        lines.extend(["## 关键概念", "", "、".join(payload["key_concepts"]), ""])
    return "\n".join(lines)


def render_reader_html(payload: dict[str, Any]) -> str:
    section_html = []
    for section in payload.get("sections", []):
        refs = " / ".join(str(page) for page in section.get("page_refs", []))
        section_html.append(
            f"""
            <section class="reader-section">
              <h2>{html.escape(section['heading'])}</h2>
              <p>{html.escape(section.get('body', '')).replace(chr(10), '<br>')}</p>
              <div class="page-ref">页码 {html.escape(refs)}</div>
            </section>
            """
        )
    assets = "".join(
        f"<li><strong>{html.escape(asset['title'])}</strong><span>{html.escape(asset['type'])}</span><p>{html.escape(asset['description'])}</p></li>"
        for asset in payload.get("visual_assets", [])
    )
    concepts = "".join(f"<span>{html.escape(item)}</span>" for item in payload.get("key_concepts", []))
    tags = "".join(f"<span>{html.escape(item)}</span>" for item in payload.get("drama_tags", []))
    return f"""
    <article class="reader-article">
      <header class="reader-article-header">
        <div class="eyebrow">章节精读</div>
        <h1>{html.escape(payload['title'])}</h1>
        <p>{html.escape(payload.get('subtitle', ''))}</p>
      </header>
      <section class="reader-summary">{html.escape(payload.get('summary', ''))}</section>
      {''.join(section_html)}
      <aside class="reader-assets"><h2>图表与视觉提示</h2><ul>{assets}</ul></aside>
      <aside class="reader-tags"><h2>关键概念</h2><div>{concepts}</div><h2>短剧标签</h2><div>{tags}</div></aside>
    </article>
    """.strip()


def normalize_source_text(text: str) -> str:
    text = re.sub(r"<!-- page:(\d+) -->", r"\n[page \1]\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_json_object(content: str) -> dict[str, Any]:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?", "", content).strip()
        content = re.sub(r"```$", "", content).strip()
    return json.loads(content)


def result_to_payload(result: RefinementResult) -> dict[str, Any]:
    return asdict(result)
