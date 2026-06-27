from __future__ import annotations

import hashlib
import html
import json
import posixpath
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

from pypdf import PdfReader, PdfWriter


SUPPORTED_MATERIAL_EXTENSIONS = {".pdf", ".epub", ".txt", ".md", ".markdown", ".mobi", ".doc", ".docx"}
TEXT_MATERIAL_EXTENSIONS = {".txt", ".md", ".markdown"}
CHAPTER_TITLE_RE = re.compile(r"^\s*(\d{1,3})[\s.、:-]+(.+)")
TEXT_HEADING_RE = re.compile(
    r"^\s{0,3}(?:#{1,4}\s+)?((?:chapter\s+\d{1,3}|第[一二三四五六七八九十百千万0-9]+[章节卷部篇]|"
    r"\d{1,3}[\s.、:-]+).{0,120})$",
    re.IGNORECASE,
)
PART_TITLE_RE = re.compile(r"^\s*part\s+", re.IGNORECASE)
EXCLUDED_TITLE_RE = re.compile(
    r"^\s*(untitled|contents|figures|maps|tables?|contributors|preface|acknowledgements?|acknowledgments?|abstract|keywords|notes?|appendix|glossary|index|bibliography|references)\b",
    re.IGNORECASE,
)
MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,4}\s+(.+?)\s*#*\s*$")


@dataclass
class SplitSection:
    section_id: str
    title: str
    start_page: int
    end_page: int
    kind: str
    include_in_analysis: bool


@dataclass
class ChapterSection(SplitSection):
    chapter_id: str
    pdf_path: str
    text_path: str
    word_count: int
    source_format: str = "pdf"
    source_path: str = ""


@dataclass
class SplitResult:
    book_id: str
    source_file: str
    source_sha256: str
    page_count: int
    output_dir: str
    chapters: list[ChapterSection]
    excluded_sections: list[SplitSection]
    warnings: list[str]


@dataclass
class OutlineEntry:
    title: str
    page: int
    level: int


class MaterialSplitter:
    """Split source PDFs into reviewable chapter files and metadata."""

    def __init__(self, output_root: Path | str):
        self.output_root = Path(output_root)

    def split_material(self, source_path: Path | str) -> SplitResult:
        source = Path(source_path)
        suffix = source.suffix.lower()
        if suffix == ".pdf":
            return self.split_pdf(source)
        if suffix in TEXT_MATERIAL_EXTENSIONS:
            text = read_text_file(source)
            sections = text_to_sections(text, fallback_title=source.stem, markdown=suffix in {".md", ".markdown"})
            return self._split_text_sections(source, sections, suffix.lstrip("."), warnings=[])
        if suffix == ".epub":
            sections = epub_to_sections(source)
            return self._split_text_sections(source, sections, "epub", warnings=[])
        if suffix == ".docx":
            sections = docx_to_sections(source)
            return self._split_text_sections(source, sections, "docx", warnings=[])
        if suffix == ".doc":
            text = extract_text_with_textutil(source)
            sections = text_to_sections(text, fallback_title=source.stem, markdown=False)
            return self._split_text_sections(
                source,
                sections,
                "doc",
                warnings=["DOC 文件已通过系统 textutil 转为纯文本，章节边界请人工复核。"],
            )
        if suffix == ".mobi":
            text = extract_mobi_text(source)
            sections = text_to_sections(text, fallback_title=source.stem, markdown=False)
            return self._split_text_sections(
                source,
                sections,
                "mobi",
                warnings=["MOBI 文件已通过本机电子书转换工具转为纯文本，章节边界请人工复核。"],
            )
        raise ValueError(f"暂不支持的材料格式：{suffix}")

    def split_pdf(self, source_path: Path | str) -> SplitResult:
        source = Path(source_path)
        if not source.exists():
            raise FileNotFoundError(source)
        if source.suffix.lower() != ".pdf":
            raise ValueError("当前 PDF 拆分入口只支持 PDF 文件")

        book_id = slugify(source.stem) or "book"
        output_dir = self.output_root
        chapters_pdf_dir = output_dir / "chapters_pdf"
        chapters_text_dir = output_dir / "chapters_text"
        chunks_dir = output_dir / "chunks"
        for directory in (output_dir, chapters_pdf_dir, chapters_text_dir, chunks_dir):
            directory.mkdir(parents=True, exist_ok=True)

        reader = PdfReader(str(source))
        page_count = len(reader.pages)
        outline_entries = extract_outline_entries(reader)
        if not outline_entries:
            raise ValueError("没有在 PDF 中识别到目录书签，后续版本需要启用目录页/LLM 兜底")

        split_sections = build_sections(outline_entries, page_count)
        chapter_candidates = [section for section in split_sections if section.kind == "chapter"]
        warnings: list[str] = []
        if not chapter_candidates:
            raise ValueError("PDF 书签存在，但没有识别到可分析的章节")
        if not any(CHAPTER_TITLE_RE.match(entry.title) for entry in outline_entries):
            warnings.append("未发现编号章节，已按非前言/非索引书签推断章节，请人工复核章节边界。")

        chapters: list[ChapterSection] = []
        for index, section in enumerate(chapter_candidates, start=1):
            chapter_id = f"ch{index:02d}"
            file_slug = slugify(section.title) or chapter_id
            pdf_path = chapters_pdf_dir / f"{chapter_id}_{file_slug}.pdf"
            text_path = chapters_text_dir / f"{chapter_id}_{file_slug}.md"
            chapter_text = extract_page_text(reader, section.start_page, section.end_page)
            write_pdf_pages(reader, section.start_page, section.end_page, pdf_path)
            write_chapter_markdown(source.name, section, chapter_text, text_path)
            word_count = count_words(chapter_text)
            chapters.append(
                ChapterSection(
                    section_id=section.section_id,
                    title=section.title,
                    start_page=section.start_page,
                    end_page=section.end_page,
                    kind=section.kind,
                    include_in_analysis=True,
                    chapter_id=chapter_id,
                    pdf_path=str(pdf_path),
                    text_path=str(text_path),
                    word_count=word_count,
                    source_format="pdf",
                    source_path=str(source),
                )
            )
            write_chunk_markdown(chunks_dir, chapter_id, section, chapter_text)

        excluded_sections = [
            section for section in split_sections if section.kind != "chapter" or not section.include_in_analysis
        ]
        result = SplitResult(
            book_id=book_id,
            source_file=str(source),
            source_sha256=sha256_file(source),
            page_count=page_count,
            output_dir=str(output_dir),
            chapters=chapters,
            excluded_sections=excluded_sections,
            warnings=warnings,
        )
        write_manifest(result, output_dir / "manifest.json")
        write_chapter_review(result, output_dir / "chapter_review.md")
        write_qa_report(result, output_dir / "qa_report.md")
        return result

    def _split_text_sections(
        self,
        source: Path,
        sections: list[tuple[str, str]],
        source_format: str,
        warnings: list[str],
    ) -> SplitResult:
        if not source.exists():
            raise FileNotFoundError(source)

        book_id = slugify(source.stem) or "book"
        output_dir = self.output_root
        chapters_text_dir = output_dir / "chapters_text"
        chunks_dir = output_dir / "chunks"
        for directory in (output_dir, chapters_text_dir, chunks_dir):
            directory.mkdir(parents=True, exist_ok=True)

        chapters: list[ChapterSection] = []
        for index, (title, text) in enumerate(sections, start=1):
            clean_title = normalize_title(title) or f"Chapter {index}"
            chapter_id = f"ch{index:02d}"
            file_slug = slugify(clean_title) or chapter_id
            text_path = chapters_text_dir / f"{chapter_id}_{file_slug}.md"
            split_section = SplitSection(
                section_id=f"sec{index:03d}",
                title=clean_title,
                start_page=index,
                end_page=index,
                kind="chapter",
                include_in_analysis=True,
            )
            chapter_text = normalize_text_body(text) or clean_title
            write_chapter_markdown(source.name, split_section, chapter_text, text_path)
            word_count = count_words(chapter_text)
            chapters.append(
                ChapterSection(
                    section_id=split_section.section_id,
                    title=split_section.title,
                    start_page=split_section.start_page,
                    end_page=split_section.end_page,
                    kind=split_section.kind,
                    include_in_analysis=True,
                    chapter_id=chapter_id,
                    pdf_path="",
                    text_path=str(text_path),
                    word_count=word_count,
                    source_format=source_format,
                    source_path=str(source),
                )
            )
            write_chunk_markdown(chunks_dir, chapter_id, split_section, chapter_text)

        if not chapters:
            raise ValueError("没有从材料中识别到可分析内容")

        result = SplitResult(
            book_id=book_id,
            source_file=str(source),
            source_sha256=sha256_file(source),
            page_count=len(chapters),
            output_dir=str(output_dir),
            chapters=chapters,
            excluded_sections=[],
            warnings=warnings,
        )
        write_manifest(result, output_dir / "manifest.json")
        write_chapter_review(result, output_dir / "chapter_review.md")
        write_qa_report(result, output_dir / "qa_report.md")
        return result


def extract_outline_entries(reader: PdfReader) -> list[OutlineEntry]:
    entries: list[OutlineEntry] = []

    def walk(items: Iterable[object], level: int = 0) -> None:
        for item in items:
            if isinstance(item, list):
                walk(item, level + 1)
                continue
            title = getattr(item, "title", None)
            if not title:
                continue
            try:
                page = reader.get_destination_page_number(item) + 1
            except Exception:
                continue
            entries.append(OutlineEntry(title=normalize_title(str(title)), page=page, level=level))

    outline = getattr(reader, "outline", None) or getattr(reader, "outlines", None) or []
    walk(outline)
    entries.sort(key=lambda entry: (entry.page, entry.level, entry.title))
    deduped: list[OutlineEntry] = []
    seen: set[tuple[int, str]] = set()
    for entry in entries:
        key = (entry.page, entry.title)
        if key not in seen:
            deduped.append(entry)
            seen.add(key)
    return deduped


def build_sections(entries: list[OutlineEntry], page_count: int) -> list[SplitSection]:
    sections: list[SplitSection] = []
    fallback_level = infer_unnumbered_chapter_level(entries)
    if entries and entries[0].page > 1:
        sections.append(
            SplitSection(
                section_id="sec000",
                title="Unlisted front matter",
                start_page=1,
                end_page=entries[0].page - 1,
                kind="front_matter",
                include_in_analysis=False,
            )
        )
    for index, entry in enumerate(entries):
        next_page = entries[index + 1].page if index + 1 < len(entries) else page_count + 1
        end_page = max(entry.page, min(page_count, next_page - 1))
        kind, include = classify_title(
            entry.title,
            allow_unnumbered_chapter=fallback_level is not None and entry.level == fallback_level,
        )
        sections.append(
            SplitSection(
                section_id=f"sec{index + 1:03d}",
                title=entry.title,
                start_page=entry.page,
                end_page=end_page,
                kind=kind,
                include_in_analysis=include,
            )
        )
    return sections


def infer_unnumbered_chapter_level(entries: list[OutlineEntry]) -> int | None:
    if any(CHAPTER_TITLE_RE.match(entry.title) for entry in entries):
        return None
    candidates = [
        entry.level
        for entry in entries
        if not PART_TITLE_RE.match(entry.title) and not EXCLUDED_TITLE_RE.match(entry.title)
    ]
    return min(candidates) if candidates else None


def classify_title(title: str, allow_unnumbered_chapter: bool = False) -> tuple[str, bool]:
    if CHAPTER_TITLE_RE.match(title):
        return "chapter", True
    if PART_TITLE_RE.match(title):
        return "part", False
    if EXCLUDED_TITLE_RE.match(title):
        return "reference", False
    if allow_unnumbered_chapter:
        return "chapter", True
    return "front_matter", False


def read_text_file(source: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return source.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("无法识别文本编码")


def text_to_sections(text: str, fallback_title: str, markdown: bool = False) -> list[tuple[str, str]]:
    normalized = normalize_text_body(text)
    if not normalized:
        return []
    lines = normalized.splitlines()
    heading_indexes: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        title = detect_text_heading(line, markdown=markdown)
        if title:
            heading_indexes.append((index, title))

    if not heading_indexes:
        return [(normalize_title(fallback_title), normalized)]

    sections: list[tuple[str, str]] = []
    for position, (line_index, title) in enumerate(heading_indexes):
        next_index = heading_indexes[position + 1][0] if position + 1 < len(heading_indexes) else len(lines)
        body = "\n".join(lines[line_index:next_index]).strip()
        if body:
            sections.append((title, body))
    return sections or [(normalize_title(fallback_title), normalized)]


def detect_text_heading(line: str, markdown: bool) -> str | None:
    stripped = line.strip()
    if not stripped or len(stripped) > 140:
        return None
    if markdown:
        markdown_match = MARKDOWN_HEADING_RE.match(stripped)
        if markdown_match:
            return normalize_title(markdown_match.group(1))
    text_match = TEXT_HEADING_RE.match(stripped)
    if text_match:
        return normalize_title(text_match.group(1).lstrip("#").strip())
    return None


def epub_to_sections(source: Path) -> list[tuple[str, str]]:
    try:
        with zipfile.ZipFile(source) as archive:
            opf_path = epub_opf_path(archive)
            manifest, spine = epub_manifest_and_spine(archive, opf_path)
            base_dir = posixpath.dirname(opf_path)
            sections: list[tuple[str, str]] = []
            for item_id in spine:
                item = manifest.get(item_id)
                if not item:
                    continue
                href, media_type = item
                if "html" not in media_type and not href.lower().endswith((".xhtml", ".html", ".htm")):
                    continue
                item_path = posixpath.normpath(posixpath.join(base_dir, href))
                try:
                    raw = archive.read(item_path)
                except KeyError:
                    continue
                title, text = html_document_to_text(raw)
                if text:
                    sections.append((title or Path(href).stem, text))
            return sections
    except zipfile.BadZipFile as exc:
        raise ValueError("EPUB 文件不是有效的 zip/epub 结构") from exc


def epub_opf_path(archive: zipfile.ZipFile) -> str:
    try:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
    except KeyError as exc:
        raise ValueError("EPUB 缺少 META-INF/container.xml") from exc
    for element in container.iter():
        if local_name(element.tag) == "rootfile":
            full_path = element.attrib.get("full-path")
            if full_path:
                return full_path
    raise ValueError("EPUB 未找到 OPF rootfile")


def epub_manifest_and_spine(archive: zipfile.ZipFile, opf_path: str) -> tuple[dict[str, tuple[str, str]], list[str]]:
    root = ET.fromstring(archive.read(opf_path))
    manifest: dict[str, tuple[str, str]] = {}
    spine: list[str] = []
    for element in root.iter():
        name = local_name(element.tag)
        if name == "item":
            item_id = element.attrib.get("id")
            href = element.attrib.get("href")
            if item_id and href:
                manifest[item_id] = (href, element.attrib.get("media-type", ""))
        elif name == "itemref":
            item_id = element.attrib.get("idref")
            if item_id:
                spine.append(item_id)
    return manifest, spine


def html_document_to_text(raw: bytes) -> tuple[str, str]:
    parser = ET.XMLParser()
    try:
        root = ET.fromstring(raw, parser=parser)
    except ET.ParseError:
        decoded = raw.decode("utf-8", errors="ignore")
        stripped = re.sub(r"<script.*?</script>|<style.*?</style>", "", decoded, flags=re.IGNORECASE | re.DOTALL)
        text = normalize_text_body(re.sub(r"<[^>]+>", "\n", html.unescape(stripped)))
        return "", text

    heading_title = ""
    document_title = ""
    blocks: list[str] = []
    for element in root.iter():
        name = local_name(element.tag).lower()
        text = normalize_text_body(" ".join(part for part in element.itertext() if part))
        if not text:
            continue
        if name in {"h1", "h2", "h3"} and not heading_title:
            heading_title = text
        if name == "title" and not document_title:
            document_title = text
        if name in {"h1", "h2", "h3", "p", "li", "blockquote"}:
            if not blocks or blocks[-1] != text:
                blocks.append(text)
    return heading_title or document_title, "\n\n".join(blocks)


def docx_to_sections(source: Path) -> list[tuple[str, str]]:
    try:
        with zipfile.ZipFile(source) as archive:
            document_xml = archive.read("word/document.xml")
    except KeyError as exc:
        raise ValueError("DOCX 缺少 word/document.xml") from exc
    except zipfile.BadZipFile as exc:
        raise ValueError("DOCX 文件不是有效的 zip/docx 结构") from exc

    root = ET.fromstring(document_xml)
    paragraphs: list[tuple[str, str]] = []
    for paragraph in root.iter(docx_tag("p")):
        text_parts = [node.text or "" for node in paragraph.iter(docx_tag("t"))]
        text = normalize_text_body("".join(text_parts))
        if not text:
            continue
        style = ""
        style_node = paragraph.find(f".//{docx_tag('pStyle')}")
        if style_node is not None:
            style = style_node.attrib.get(docx_tag("val"), "")
        paragraphs.append((text, style))
    return styled_paragraphs_to_sections(paragraphs, fallback_title=source.stem)


def styled_paragraphs_to_sections(paragraphs: list[tuple[str, str]], fallback_title: str) -> list[tuple[str, str]]:
    heading_indexes: list[tuple[int, str]] = []
    for index, (text, style) in enumerate(paragraphs):
        if style.lower().startswith("heading") or detect_text_heading(text, markdown=False):
            heading_indexes.append((index, text))
    if not heading_indexes:
        body = "\n\n".join(text for text, _ in paragraphs)
        return [(normalize_title(fallback_title), body)] if body else []
    sections: list[tuple[str, str]] = []
    for position, (paragraph_index, title) in enumerate(heading_indexes):
        next_index = heading_indexes[position + 1][0] if position + 1 < len(heading_indexes) else len(paragraphs)
        body = "\n\n".join(text for text, _ in paragraphs[paragraph_index:next_index])
        if body:
            sections.append((title, body))
    return sections


def extract_text_with_textutil(source: Path) -> str:
    textutil = shutil.which("textutil")
    if not textutil:
        raise ValueError("DOC 解析需要 macOS textutil 或先转换为 DOCX/TXT")
    completed = subprocess.run(
        [textutil, "-convert", "txt", "-stdout", str(source)],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="ignore").strip()
        raise ValueError(f"DOC 转文本失败：{detail or 'textutil 返回错误'}")
    return completed.stdout.decode("utf-8", errors="ignore")


def extract_mobi_text(source: Path) -> str:
    converter = shutil.which("ebook-convert")
    if not converter:
        raise ValueError("MOBI 解析需要安装 Calibre 的 ebook-convert；也可以先转成 EPUB/TXT/MD 再上传。")
    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "converted.txt"
        completed = subprocess.run(
            [converter, str(source), str(output)],
            check=False,
            capture_output=True,
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="ignore").strip()
            raise ValueError(f"MOBI 转文本失败：{detail or 'ebook-convert 返回错误'}")
        return output.read_text(encoding="utf-8", errors="ignore")


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def docx_tag(name: str) -> str:
    return f"{{http://schemas.openxmlformats.org/wordprocessingml/2006/main}}{name}"


def normalize_text_body(text: str) -> str:
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def write_pdf_pages(reader: PdfReader, start_page: int, end_page: int, destination: Path) -> None:
    writer = PdfWriter()
    for page_number in range(start_page, end_page + 1):
        writer.add_page(reader.pages[page_number - 1])
    with destination.open("wb") as handle:
        writer.write(handle)


def extract_page_text(reader: PdfReader, start_page: int, end_page: int) -> str:
    parts: list[str] = []
    for page_number in range(start_page, end_page + 1):
        page = reader.pages[page_number - 1]
        text = page.extract_text() or ""
        parts.append(f"\n\n<!-- page:{page_number} -->\n\n{text.strip()}")
    return "\n".join(parts).strip()


def write_chapter_markdown(source_name: str, section: SplitSection, text: str, destination: Path) -> None:
    body = text if text else "> 本章节未抽取到文本，可能需要 OCR 或人工检查。"
    destination.write_text(
        "\n".join(
            [
                f"# {section.title}",
                "",
                f"- 来源文件: {source_name}",
                f"- 页码范围: {section.start_page}-{section.end_page}",
                "",
                body,
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_chunk_markdown(chunks_dir: Path, chapter_id: str, section: SplitSection, text: str) -> None:
    normalized = text.strip() or "本章节未抽取到文本，可能需要 OCR 或人工检查。"
    max_chars = 6000
    chunks = [normalized[index : index + max_chars] for index in range(0, len(normalized), max_chars)] or [normalized]
    for index, chunk in enumerate(chunks, start=1):
        path = chunks_dir / f"{chapter_id}_{index:04d}.md"
        path.write_text(
            "\n".join(
                [
                    f"# {section.title} / Chunk {index:04d}",
                    "",
                    f"- chapter_id: {chapter_id}",
                    f"- page_range: {section.start_page}-{section.end_page}",
                    "",
                    chunk,
                    "",
                ]
            ),
            encoding="utf-8",
        )


def write_manifest(result: SplitResult, destination: Path) -> None:
    destination.write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_chapter_review(result: SplitResult, destination: Path) -> None:
    lines = [
        f"# 章节拆分审查表 - {result.book_id}",
        "",
        "| 类型 | ID | 标题 | 起始页 | 结束页 | 进入分析 |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    for chapter in result.chapters:
        lines.append(
            f"| 正文章节 | {chapter.chapter_id} | {chapter.title} | {chapter.start_page} | {chapter.end_page} | 是 |"
        )
    for section in result.excluded_sections:
        lines.append(
            f"| {section.kind} | {section.section_id} | {section.title} | {section.start_page} | {section.end_page} | 否 |"
        )
    lines.append("")
    destination.write_text("\n".join(lines), encoding="utf-8")


def write_qa_report(result: SplitResult, destination: Path) -> None:
    covered_pages = sum(chapter.end_page - chapter.start_page + 1 for chapter in result.chapters)
    excluded_pages = sum(section.end_page - section.start_page + 1 for section in result.excluded_sections)
    lines = [
        f"# 素材拆分 QA 报告 - {result.book_id}",
        "",
        "## 完整性检查",
        "",
        f"- PDF 总页数: {result.page_count}",
        f"- 正文章节数: {len(result.chapters)}",
        f"- 正文章节覆盖页数: {covered_pages}",
        f"- 非正文/排除页数: {excluded_pages}",
        f"- 输出目录: {result.output_dir}",
        "",
        "## 风险提示",
        "",
    ]
    if result.warnings:
        lines.extend(f"- {warning}" for warning in result.warnings)
    else:
        lines.append("- 未发现结构性风险。")
    lines.append("")
    destination.write_text("\n".join(lines), encoding="utf-8")


def count_words(text: str) -> int:
    latin_words = re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)?", text)
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)
    return len(latin_words) + len(cjk_chars)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip()


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value[:80]
