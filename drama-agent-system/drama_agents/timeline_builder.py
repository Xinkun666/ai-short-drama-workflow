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

from drama_agents.chapter_refiner import normalize_source_text, parse_json_object


@dataclass
class ChapterTimeline:
    chapter_id: str
    title: str
    status: str
    message: str
    event_count: int
    timeline_json_path: str


@dataclass
class TimelineResult:
    status: str
    message: str
    event_count: int
    timeline_json_path: str
    timeline_markdown_path: str
    chapters: list[ChapterTimeline]


class TimelineBuilder:
    def __init__(self, provider=None):
        self.provider = provider

    @classmethod
    def from_environment(cls):
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            return cls(provider=None)
        return cls(provider=DeepSeekTimelineProvider(api_key=api_key))

    def build_book(self, split_result, force: bool = False) -> TimelineResult:
        if not self.provider:
            return TimelineResult(
                status="skipped",
                message="未配置 DEEPSEEK_API_KEY，已跳过全书时间线生成。",
                event_count=0,
                timeline_json_path="",
                timeline_markdown_path="",
                chapters=[],
            )

        output_dir = Path(get_value(split_result, "output_dir"))
        timeline_dir = output_dir / "timeline"
        chapter_dir = timeline_dir / "chapters"
        chapter_dir.mkdir(parents=True, exist_ok=True)
        chapters = list(get_value(split_result, "chapters") or [])
        if not chapters:
            return TimelineResult(
                status="skipped",
                message="没有可用于生成时间线的章节。",
                event_count=0,
                timeline_json_path="",
                timeline_markdown_path="",
                chapters=[],
            )

        max_workers = min(max(1, int(os.environ.get("DEEPSEEK_MAX_WORKERS", "3"))), len(chapters))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            chapter_results = list(
                executor.map(
                    lambda chapter: self._build_chapter_timeline(
                        split_result,
                        output_dir,
                        chapter_dir,
                        chapter,
                        force=force,
                    ),
                    chapters,
                )
            )

        events: list[dict[str, Any]] = []
        for chapter_result in chapter_results:
            payload = read_json_file(Path(chapter_result.timeline_json_path), default={})
            events.extend(payload.get("events", []))
        events = sorted(events, key=event_sort_key)
        book_payload = {
            "book_id": get_value(split_result, "book_id"),
            "source_file": get_value(split_result, "source_file"),
            "status": "completed" if all(item.status == "completed" for item in chapter_results) else "partial",
            "message": "全书时间线生成完成。",
            "event_count": len(events),
            "time_span": time_span(events),
            "place_count": count_places(events),
            "events": events,
        }
        failures = [f"{item.chapter_id}: {item.message}" for item in chapter_results if item.status != "completed"]
        if failures:
            book_payload["message"] = "部分章节时间线生成失败，已保留失败说明。 " + "; ".join(failures[:3])
        paths = write_book_timeline_artifacts(timeline_dir, book_payload)
        return TimelineResult(
            status=book_payload["status"],
            message=book_payload["message"],
            event_count=len(events),
            timeline_json_path=str(paths["json"]),
            timeline_markdown_path=str(paths["markdown"]),
            chapters=chapter_results,
        )

    def _build_chapter_timeline(
        self,
        split_result,
        output_dir: Path,
        chapter_dir: Path,
        chapter,
        force: bool = False,
    ) -> ChapterTimeline:
        chapter_id = get_value(chapter, "chapter_id")
        title = get_value(chapter, "title")
        path = chapter_dir / f"{chapter_id}_timeline.json"
        existing = read_json_file(path, default={})
        if existing.get("status") == "completed" and not force:
            return ChapterTimeline(
                chapter_id=chapter_id,
                title=title,
                status="completed",
                message="completed",
                event_count=len(existing.get("events", [])),
                timeline_json_path=str(path),
            )
        try:
            raw_text = Path(get_value(chapter, "text_path")).read_text(encoding="utf-8")
            reader_payload = read_json_file(output_dir / "reader" / f"{chapter_id}_reader.json", default={})
            payload = self.provider.extract_timeline(
                book_title=Path(get_value(split_result, "source_file")).stem,
                chapter=chapter,
                raw_text=raw_text,
                reader_payload=reader_payload,
            )
            normalized = normalize_chapter_timeline_payload(payload, chapter)
            path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
            return ChapterTimeline(
                chapter_id=chapter_id,
                title=title,
                status="completed",
                message="completed",
                event_count=len(normalized["events"]),
                timeline_json_path=str(path),
            )
        except Exception as exc:
            fallback = {
                "chapter_id": chapter_id,
                "chapter_title": title,
                "status": "failed",
                "message": str(exc),
                "events": [],
            }
            path.write_text(json.dumps(fallback, ensure_ascii=False, indent=2), encoding="utf-8")
            return ChapterTimeline(
                chapter_id=chapter_id,
                title=title,
                status="failed",
                message=str(exc),
                event_count=0,
                timeline_json_path=str(path),
            )


class DeepSeekTimelineProvider:
    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        base_url: str = "https://api.deepseek.com/chat/completions",
        max_chars: int | None = None,
        timeout: int | None = None,
    ):
        self.api_key = api_key
        self.model = (
            model
            or os.environ.get("DEEPSEEK_TIMELINE_MODEL")
            or os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
        )
        self.base_url = base_url
        self.max_chars = max_chars or int(os.environ.get("DEEPSEEK_TIMELINE_MAX_CHARS", "18000"))
        self.timeout = timeout or int(os.environ.get("DEEPSEEK_TIMEOUT", "240"))

    def extract_timeline(self, *, book_title, chapter, raw_text, reader_payload):
        source_text = normalize_source_text(raw_text)
        if len(source_text) > self.max_chars:
            source_text = source_text[: self.max_chars] + "\n\n[系统提示：原章节过长，本次原文只提供前半部分，请优先结合章节阅读稿提取全章事件。]"
        prompt = build_timeline_prompt(book_title, chapter, source_text, reader_payload)
        try:
            return self._request_timeline(prompt)
        except json.JSONDecodeError:
            repair_prompt = (
                f"{prompt}\n\n"
                "重要：上一次输出不是合法 JSON。请重新输出严格 JSON，不要 Markdown 代码围栏，不要尾随逗号。"
            )
            return self._request_timeline(repair_prompt)

    def _request_timeline(self, prompt: str):
        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是严谨的历史时间线资料整理助手。只输出合法 JSON，不要输出 Markdown 代码围栏。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.15,
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
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"DeepSeek HTTP {exc.code}: {detail[:300]}") from exc
        return parse_json_object(data["choices"][0]["message"]["content"])


def build_timeline_prompt(book_title: str, chapter, source_text: str, reader_payload: dict[str, Any]) -> str:
    chapter_id = get_value(chapter, "chapter_id")
    title = get_value(chapter, "title")
    start_page = get_value(chapter, "start_page")
    end_page = get_value(chapter, "end_page")
    reader_text = compact_reader_payload(reader_payload)
    return f"""
请从下面这一章历史材料中提取“事件模块级”的时间线条目。

目标：
1. 每章提取 5-12 个值得进入全书时间线的历史事件、长期过程或文明变化节点；如果原文确实非常密集，最多 15 个。
2. 每个条目必须围绕“时间、地点、内容”三块展开。
3. 时间如果是范围，给出大致范围；如果是精确时间，给出精确时间。
4. 公元前年份必须写成负数，例如公元前 20000 年写为 -20000。
5. 地点可以是洲、地区、区域、路线或城市；如果涉及从 A 到 B 的移动，movement 写清楚 from 和 to。
6. content 必须基于原文尽量完整复述这个时间地点发生的事情，不要只写一句摘要。
7. 如果原文有多段都围绕同一个时间地点、同一个事件或同一个历史过程展开，就把这些段落里的故事、背景、过程、原因、影响和后续演化整合进 content。
8. content 写成连贯中文材料段，原文材料充足时尽量写 250-700 字；材料较少时也要写清楚上下文，不要只给关键词。
9. 不要新增 background、cause、process、impact、evolution 等字段；这些内容如果原文有，都放进 content 里。
10. 不得引入原文没有的外部常识、百科背景或你自己的历史知识；如果原文只给了很少材料，content 可以短，但必须诚实。
11. 理论性内容不要硬塞进时间线，除非它指向可定位的历史过程。
12. DeepSeek 可以对时间、地点做合理归纳，但必须用 confidence 与 evidence_note 说明推断程度；不能用推断来扩写事件细节。

confidence 判断：
- high：时间、地点、事件内容基本由原文明确给出。
- medium：核心事实明确，但时间或地点需要根据章节上下文合理归纳。
- low：原文只提供宏观过程，时间地点都比较笼统，需要明显推断。

输出严格 JSON，字段必须是：
{{
  "events": [
    {{
      "time_label": "中文时间表述",
      "time_start_year": -200000,
      "time_end_year": -50000,
      "time_precision": "exact|range|approximate|unknown",
      "place_label": "中文地点表述",
      "place_scope": "continent|region|subregion|route|city|global|unknown",
      "places": ["地点1", "地点2"],
      "movement": {{"from": "A", "to": "B"}} 或 null,
      "title": "事件短标题",
      "content": "基于原文完整复述这个时间地点发生的事情：包括原文围绕该点展开的故事、背景、过程、原因、影响和后续演化，但不要凭空补写",
      "source_pages": [123, 124],
      "importance": 1-5,
      "confidence": "high|medium|low",
      "evidence_note": "说明时间、地点、内容依据分别来自原文明确表述、章节上下文还是推断",
      "drama_potential": "这个节点对短剧有什么价值"
    }}
  ]
}}

书名：{book_title}
章节：{chapter_id} {title}
页码：{start_page}-{end_page}

已整理章节阅读稿：
{reader_text}

原始章节文本：
{source_text}
""".strip()


def compact_reader_payload(payload: dict[str, Any]) -> str:
    if not payload:
        return "无已整理章节阅读稿。"
    lines = [
        f"标题：{payload.get('title', '')}",
        f"摘要：{payload.get('summary', '')}",
    ]
    for section in payload.get("sections", [])[:12]:
        if not isinstance(section, dict):
            continue
        lines.append(f"小节：{section.get('heading', '')}")
        lines.append(str(section.get("body", ""))[:900])
    return "\n".join(lines)


def normalize_chapter_timeline_payload(payload: dict[str, Any], chapter) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("DeepSeek 返回不是 JSON object")
    raw_events = payload.get("events") or []
    if not isinstance(raw_events, list):
        raw_events = []
    chapter_id = get_value(chapter, "chapter_id")
    events = []
    for event in raw_events:
        if not isinstance(event, dict) or admits_external_knowledge(event):
            continue
        events.append(normalize_event(event, chapter, len(events) + 1))
    events = sorted(events, key=event_sort_key)
    return {
        "chapter_id": chapter_id,
        "chapter_title": get_value(chapter, "title"),
        "status": "completed",
        "message": "completed",
        "event_count": len(events),
        "events": events,
    }


def normalize_event(event: dict[str, Any], chapter, index: int) -> dict[str, Any]:
    chapter_id = get_value(chapter, "chapter_id")
    start_page = get_value(chapter, "start_page")
    end_page = get_value(chapter, "end_page")
    confidence = str(event.get("confidence") or "medium").lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"
    precision = str(event.get("time_precision") or "unknown").lower()
    if precision not in {"exact", "range", "approximate", "unknown"}:
        precision = "unknown"
    return {
        "event_id": f"{chapter_id}-e{index:03d}",
        "chapter_id": chapter_id,
        "chapter_title": get_value(chapter, "title"),
        "time_label": str(event.get("time_label") or "时间不明"),
        "time_start_year": normalize_year(event.get("time_start_year")),
        "time_end_year": normalize_year(event.get("time_end_year")),
        "time_precision": precision,
        "place_label": str(event.get("place_label") or "地点不明"),
        "place_scope": str(event.get("place_scope") or "unknown"),
        "places": normalize_string_list(event.get("places") or []),
        "movement": normalize_movement(event.get("movement")),
        "title": str(event.get("title") or "未命名事件"),
        "content": str(event.get("content") or ""),
        "source_pages": normalize_pages(event.get("source_pages") or [start_page, end_page], start_page, end_page),
        "importance": normalize_importance(event.get("importance")),
        "confidence": confidence,
        "evidence_note": str(event.get("evidence_note") or "DeepSeek 未提供证据说明，需人工复核。"),
        "drama_potential": str(event.get("drama_potential") or ""),
    }


def admits_external_knowledge(event: dict[str, Any]) -> bool:
    note = str(event.get("evidence_note") or "")
    content = str(event.get("content") or "")
    combined = f"{note}\n{content}"
    allowed_negations = ("未添加外部", "没有添加外部", "不含外部", "无外部")
    if any(marker in combined for marker in allowed_negations):
        return False
    forbidden_markers = (
        "一般世界史知识",
        "常识补充",
        "外部知识",
        "百科背景",
        "百科知识",
        "基于常识",
        "根据常识",
    )
    return any(marker in combined for marker in forbidden_markers)


def normalize_year(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"-?\d+", str(value).replace(",", ""))
    if not match:
        return None
    return int(match.group(0))


def normalize_pages(value: Any, start_page: int, end_page: int) -> list[int]:
    if not isinstance(value, list):
        value = [value]
    pages = []
    for item in value:
        try:
            page = int(item)
        except (TypeError, ValueError):
            continue
        if start_page <= page <= end_page:
            pages.append(page)
    return pages or [start_page, end_page]


def normalize_importance(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 3
    return min(5, max(1, number))


def normalize_string_list(items: Any) -> list[str]:
    if isinstance(items, str):
        items = [items]
    if not isinstance(items, list):
        return []
    return [str(item) for item in items if str(item).strip()]


def normalize_movement(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    start = str(value.get("from") or "").strip()
    end = str(value.get("to") or "").strip()
    if not start and not end:
        return None
    return {"from": start, "to": end}


def event_sort_key(event: dict[str, Any]) -> tuple[int, int, str]:
    year = event.get("time_start_year")
    if year is None:
        year = event.get("time_end_year")
    return (1 if year is None else 0, int(year or 0), str(event.get("event_id", "")))


def time_span(events: list[dict[str, Any]]) -> dict[str, Any]:
    years = []
    for event in events:
        for key in ("time_start_year", "time_end_year"):
            if isinstance(event.get(key), int):
                years.append(event[key])
    if not years:
        return {"start_year": None, "end_year": None, "label": "时间跨度不明"}
    return {
        "start_year": min(years),
        "end_year": max(years),
        "label": f"{format_year(min(years))} - {format_year(max(years))}",
    }


def count_places(events: list[dict[str, Any]]) -> int:
    places = set()
    for event in events:
        places.update(event.get("places") or [])
    return len(places)


def format_year(year: int | None) -> str:
    if year is None:
        return "未知"
    if year < 0:
        return f"公元前 {abs(year)} 年"
    return f"公元 {year} 年"


def write_book_timeline_artifacts(timeline_dir: Path, payload: dict[str, Any]) -> dict[str, Path]:
    timeline_dir.mkdir(parents=True, exist_ok=True)
    json_path = timeline_dir / "timeline.json"
    markdown_path = timeline_dir / "timeline.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_timeline_markdown(payload), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def render_timeline_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 全书时间线",
        "",
        f"- 事件数: {payload.get('event_count', 0)}",
        f"- 时间跨度: {payload.get('time_span', {}).get('label', '时间跨度不明')}",
        "",
    ]
    for event in payload.get("events", []):
        pages = ", ".join(str(page) for page in event.get("source_pages", []))
        lines.extend(
            [
                f"## {event['event_id']} {event['title']}",
                "",
                f"- 时间: {event['time_label']}",
                f"- 地点: {event['place_label']}",
                f"- 页码: {pages}",
                f"- 重要性: {event['importance']}",
                f"- 可信度: {event['confidence']}",
                "",
                event.get("content", ""),
                "",
                f"证据说明: {event.get('evidence_note', '')}",
                "",
            ]
        )
    return "\n".join(lines)


def read_json_file(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def get_value(obj, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def result_to_payload(result: TimelineResult) -> dict[str, Any]:
    return asdict(result)


def render_static_timeline_html(payload: dict[str, Any]) -> str:
    cards = []
    for event in payload.get("events", []):
        cards.append(
            f"""
            <article>
              <div>{html.escape(event.get('time_label', ''))}</div>
              <h2>{html.escape(event.get('title', ''))}</h2>
              <p>{html.escape(event.get('content', ''))}</p>
            </article>
            """
        )
    return "<main>" + "".join(cards) + "</main>"
