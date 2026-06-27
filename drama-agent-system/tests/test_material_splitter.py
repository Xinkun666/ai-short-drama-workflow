from pathlib import Path
import zipfile

from pypdf import PdfReader, PdfWriter

from drama_agents.material_splitter import MaterialSplitter


def create_outline_pdf(path: Path) -> None:
    writer = PdfWriter()
    for index in range(6):
        writer.add_blank_page(width=300, height=400)
        writer.add_metadata({"/PageLabel": str(index + 1)})
    writer.add_outline_item("Contents", 0)
    writer.add_outline_item("1 Introduction and overview", 1)
    writer.add_outline_item("2 Writing world history", 3)
    writer.add_outline_item("Index", 5)
    with path.open("wb") as handle:
        writer.write(handle)


def create_outline_pdf_with_unlisted_front_matter(path: Path) -> None:
    writer = PdfWriter()
    for _ in range(4):
        writer.add_blank_page(width=300, height=400)
    writer.add_outline_item("1 First chapter", 1)
    writer.add_outline_item("2 Second chapter", 3)
    with path.open("wb") as handle:
        writer.write(handle)


def create_handbook_style_outline_pdf(path: Path) -> None:
    writer = PdfWriter()
    for _ in range(7):
        writer.add_blank_page(width=300, height=400)
    writer.add_outline_item("UNTITLED", 0)
    writer.add_outline_item("Acknowledgements", 1)
    writer.add_outline_item("Contributors", 2)
    writer.add_outline_item("The_Task_of_World_History", 3)
    writer.add_outline_item("Theories_of_World_History_since_the_Enlightenment", 5)
    writer.add_outline_item("Index", 6)
    with path.open("wb") as handle:
        writer.write(handle)


def create_epub(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
            <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
              <rootfiles>
                <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
              </rootfiles>
            </container>
            """,
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0"?>
            <package xmlns="http://www.idpf.org/2007/opf" version="3.0">
              <manifest>
                <item id="ch1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
                <item id="ch2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
              </manifest>
              <spine>
                <itemref idref="ch1"/>
                <itemref idref="ch2"/>
              </spine>
            </package>
            """,
        )
        archive.writestr(
            "OEBPS/chapter1.xhtml",
            """<html xmlns="http://www.w3.org/1999/xhtml"><head><title>第一章</title></head>
            <body><h1>第一章 文明开始</h1><p>这里是第一章正文。</p></body></html>""",
        )
        archive.writestr(
            "OEBPS/chapter2.xhtml",
            """<html xmlns="http://www.w3.org/1999/xhtml"><head><title>第二章</title></head>
            <body><h1>第二章 城市出现</h1><p>这里是第二章正文。</p></body></html>""",
        )


def create_minimal_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p>
          <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
          <w:r><w:t>第一章 文明材料</w:t></w:r>
        </w:p>
        <w:p><w:r><w:t>这里是 Word 第一章正文。</w:t></w:r></w:p>
        <w:p>
          <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
          <w:r><w:t>第二章 迁徙材料</w:t></w:r>
        </w:p>
        <w:p><w:r><w:t>这里是 Word 第二章正文。</w:t></w:r></w:p>
      </w:body>
    </w:document>
    """
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document_xml)


def test_split_outline_pdf_creates_manifest_chapter_files_and_report(tmp_path):
    source = tmp_path / "source.pdf"
    output = tmp_path / "output"
    create_outline_pdf(source)

    result = MaterialSplitter(output_root=output).split_pdf(source)

    manifest_path = output / "manifest.json"
    report_path = output / "qa_report.md"
    review_path = output / "chapter_review.md"

    assert manifest_path.exists()
    assert report_path.exists()
    assert review_path.exists()
    assert result.book_id == "source"
    assert [chapter.chapter_id for chapter in result.chapters] == ["ch01", "ch02"]
    assert result.chapters[0].start_page == 2
    assert result.chapters[0].end_page == 3
    assert result.chapters[1].start_page == 4
    assert result.chapters[1].end_page == 5
    assert Path(result.chapters[0].pdf_path).exists()
    assert Path(result.chapters[0].text_path).exists()
    assert "1 Introduction and overview" in review_path.read_text(encoding="utf-8")
    assert "完整性检查" in report_path.read_text(encoding="utf-8")

    split_pdf = PdfReader(result.chapters[0].pdf_path)
    assert len(split_pdf.pages) == 2


def test_split_outline_pdf_accounts_for_pages_before_first_bookmark(tmp_path):
    source = tmp_path / "source.pdf"
    output = tmp_path / "output"
    create_outline_pdf_with_unlisted_front_matter(source)

    result = MaterialSplitter(output_root=output).split_pdf(source)

    assert result.excluded_sections[0].title == "Unlisted front matter"
    assert result.excluded_sections[0].start_page == 1
    assert result.excluded_sections[0].end_page == 1
    accounted_pages = sum(chapter.end_page - chapter.start_page + 1 for chapter in result.chapters)
    accounted_pages += sum(section.end_page - section.start_page + 1 for section in result.excluded_sections)
    assert accounted_pages == result.page_count


def test_split_outline_pdf_keeps_index_out_of_analysis(tmp_path):
    source = tmp_path / "source.pdf"
    output = tmp_path / "output"
    create_outline_pdf(source)

    result = MaterialSplitter(output_root=output).split_pdf(source)

    assert all("Index" not in chapter.title for chapter in result.chapters)
    index_sections = [section for section in result.excluded_sections if section.title == "Index"]
    assert len(index_sections) == 1
    assert index_sections[0].include_in_analysis is False


def test_split_handbook_style_outline_uses_unnumbered_top_level_titles_as_chapters(tmp_path):
    source = tmp_path / "source.pdf"
    output = tmp_path / "output"
    create_handbook_style_outline_pdf(source)

    result = MaterialSplitter(output_root=output).split_pdf(source)

    assert [chapter.title for chapter in result.chapters] == [
        "The_Task_of_World_History",
        "Theories_of_World_History_since_the_Enlightenment",
    ]
    assert result.chapters[0].start_page == 4
    assert result.chapters[0].end_page == 5
    assert result.chapters[1].start_page == 6
    assert result.chapters[1].end_page == 6
    assert all(section.title != "UNTITLED" for section in result.chapters)
    assert any(section.title == "Index" for section in result.excluded_sections)


def test_split_markdown_material_detects_heading_chapters(tmp_path):
    source = tmp_path / "source.md"
    output = tmp_path / "output"
    source.write_text(
        "\n".join(
            [
                "# 第一章 文明开始",
                "这里是第一章正文。",
                "",
                "# 第二章 城市出现",
                "这里是第二章正文。",
            ]
        ),
        encoding="utf-8",
    )

    result = MaterialSplitter(output_root=output).split_material(source)

    assert [chapter.title for chapter in result.chapters] == ["第一章 文明开始", "第二章 城市出现"]
    assert result.chapters[0].source_format == "md"
    assert result.chapters[0].pdf_path == ""
    assert "这里是第一章正文" in Path(result.chapters[0].text_path).read_text(encoding="utf-8")


def test_split_plain_text_material_falls_back_to_single_chapter(tmp_path):
    source = tmp_path / "source.txt"
    output = tmp_path / "output"
    source.write_text("没有标题的整本文本。\n第二段内容。", encoding="utf-8")

    result = MaterialSplitter(output_root=output).split_material(source)

    assert len(result.chapters) == 1
    assert result.chapters[0].title == "source"
    assert result.chapters[0].word_count > 0


def test_split_epub_material_uses_spine_documents_as_chapters(tmp_path):
    source = tmp_path / "source.epub"
    output = tmp_path / "output"
    create_epub(source)

    result = MaterialSplitter(output_root=output).split_material(source)

    assert [chapter.title for chapter in result.chapters] == ["第一章 文明开始", "第二章 城市出现"]
    assert result.chapters[0].source_format == "epub"
    assert "这里是第一章正文" in Path(result.chapters[0].text_path).read_text(encoding="utf-8")


def test_split_docx_material_uses_heading_paragraphs_as_chapters(tmp_path):
    source = tmp_path / "source.docx"
    output = tmp_path / "output"
    create_minimal_docx(source)

    result = MaterialSplitter(output_root=output).split_material(source)

    assert [chapter.title for chapter in result.chapters] == ["第一章 文明材料", "第二章 迁徙材料"]
    assert result.chapters[0].source_format == "docx"
    assert "这里是 Word 第一章正文" in Path(result.chapters[0].text_path).read_text(encoding="utf-8")
