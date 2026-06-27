# AI短剧工作站

本地 Web 工作站第一版聚焦“素材准备 / 素材拆分”。

## 启动

```bash
cd /Users/liuxinkun/Downloads/projects/AI短剧工作流/drama-agent-system
/Users/liuxinkun/opt/anaconda3/bin/python run_workbench.py --host 127.0.0.1 --port 8765 --workspace .. --outputs outputs
```

打开：

```text
http://127.0.0.1:8765
```

## 当前能力

- 扫描 `../资料库` 和 `../uploads` 中的 PDF、EPUB、TXT、Markdown、MOBI、Word。
- 上传 PDF、EPUB、TXT、MD、MOBI、DOC、DOCX 到 `../uploads`。
- PDF 使用书签识别正文章节；EPUB 使用 spine 文档；TXT/MD/DOCX 使用标题识别章节，无标题时作为单章处理。
- DOC 通过 macOS `textutil` 转为纯文本；MOBI 需要本机安装 Calibre 的 `ebook-convert`，否则会提示先转换为 EPUB/TXT/MD。
- 输出章节 Markdown、chunk、`manifest.json`、`chapter_review.md`、`qa_report.md`；PDF 额外输出章节 PDF。

## 输出位置

```text
drama-agent-system/outputs/material_splits/<book_id>/
```

## 数据库

结构化数据会写入本地 SQLite：

```text
drama-agent-system/outputs/material_workstation.sqlite3
```

核心表：

- `material_records`: 每本材料的解析记录、状态、统计、输出路径。
- `material_chapters`: 每章标题、页码/单元、字数、章节 Markdown/PDF 路径、阅读器状态。
- `material_excluded_sections`: 前言、索引等被排除的段落。
- `timeline_events`: 每本书的时间线事件模块，包含时间、地点、内容、证据说明、重要性等字段。

`outputs/material_records.json` 会继续作为兼容快照写出，方便直接查看；批量查询建议用 SQLite。

第一版优先保证可审查和可复跑。没有 PDF 书签或标题结构很弱的材料，后续再接目录页识别和 LLM 兜底。

## Natural Earth 地图 API

下载 Natural Earth 数据并启动本地地图 API：

```bash
cd /Users/liuxinkun/Downloads/projects/AI短剧工作流/drama-agent-system
/Users/liuxinkun/opt/anaconda3/bin/python run_map_api.py --host 127.0.0.1 --port 8770
```

只下载数据、不启动服务：

```bash
/Users/liuxinkun/opt/anaconda3/bin/python run_map_api.py --download-only
```

常用接口：

```text
GET http://127.0.0.1:8770/health
GET http://127.0.0.1:8770/api/maps/regions
GET http://127.0.0.1:8770/api/maps/render?region=china&title=中国区域底图&cities=1
GET http://127.0.0.1:8770/api/maps/render?bbox=25,10,75,45&title=西亚底图&rivers=1&lakes=1
```

输出 PNG 会临时写入：

```text
drama-agent-system/outputs/maps/
```

Natural Earth 只提供现代地理底图；历史疆域、迁徙路线、古城点位需要后续在这个底图 API 上叠加。
