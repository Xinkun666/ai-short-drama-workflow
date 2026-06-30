from pathlib import Path
from io import BytesIO
import re
import sqlite3

from pypdf import PdfWriter

from drama_agents.storage import MaterialDatabase
from drama_agents.storyboard_agent import RuleBasedStoryboardProvider
from drama_agents.vector_store import LocalVectorStore
from drama_agents.visual_scene_agent import (
    RuleBasedVisualSceneProvider,
    build_visual_scene_prompt,
    normalize_scene_extraction_payload,
)
from drama_agents.visual_subject_agent import (
    RuleBasedVisualSubjectProvider,
    build_visual_subject_prompt,
    normalize_extraction_payload,
)
from drama_agents.webapp.app import create_app


def create_pdf(path: Path) -> None:
    writer = PdfWriter()
    for _ in range(4):
        writer.add_blank_page(width=300, height=400)
    writer.add_outline_item("1 First chapter", 0)
    writer.add_outline_item("2 Second chapter", 2)
    with path.open("wb") as handle:
        writer.write(handle)


class FakeDeepSeekProvider:
    def refine(self, *, book_title, chapter, raw_text):
        return {
            "title": f"精读：{chapter.title}",
            "subtitle": "阅读器内容",
            "summary": "章节摘要内容。",
            "sections": [
                {
                    "heading": "自然小节",
                    "body": "这里是 DeepSeek 整理后的自然正文。",
                    "page_refs": [chapter.start_page],
                }
            ],
            "visual_assets": [
                {"type": "map", "title": "地图提示", "description": "这里保留地图或图表提示。"}
            ],
            "key_concepts": ["文明", "迁徙"],
            "drama_tags": ["文明跃迁"],
        }


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
                    "title": f"{chapter['chapter_id']} 智人文化能力逐步形成",
                    "content": "本事件模块说明智人出现后，文化能力、想象力和环境适应方式如何逐步形成。",
                    "source_pages": [chapter["start_page"], chapter["end_page"]],
                    "importance": 4,
                    "confidence": "medium",
                    "evidence_note": "原文给出章节时间范围，地点依据章节主题概括为非洲及早期扩散区域。",
                    "drama_potential": "可作为文明能力觉醒的前史节点。",
                }
            ]
        }


class FakeScriptProvider:
    def __init__(self):
        self.edit_payload = None
        self.adapt_payload = None

    def generate_script(self, payload):
        return {
            "title": f"{payload['topic']} - 短剧稿",
            "logline": "用轻松好懂的方式讲清楚这个历史节点。",
            "fact_cards": [
                {
                    "id": "F1",
                    "fact": "智人相关事件",
                    "time": payload["time_range"],
                    "place": "非洲",
                    "source_basis": "测试材料",
                    "confidence": "高",
                    "drama_direction": "地图开场",
                    "do_not_overstate": "不要写死",
                }
            ],
            "causal_chain": ["智人开局 → 故事系统上线"],
            "outline": [
                {
                    "title": "地图开场",
                    "core_point": "智人开局",
                    "opening_image": "地图背景",
                    "human_action": "迁徙",
                    "conflict": "环境压力",
                    "change": "协作",
                    "cost": "风险",
                    "transition": "进入下一段",
                }
            ],
            "article": (
                "智人开局，装备一般，但故事系统已经上线。\n\n"
                "镜头从非洲东部的稀树草原拉开，开阔枯草地、低矮灌木和远处动物剪影形成本集的开场环境。\n\n"
                "非洲智人部落营地里，整个部落一起拉扯孩子、制作工具、分配食物。\n\n"
                "这波不是迁徙，是人类大型开图。\n\n"
                "早期智人群体围着火光讲故事，智人猎人群体在狮子和鬣狗的威胁下协作。"
                "他们在篝火烹饪营地围坐，烤糊的肉和跳动的火光成为群体协作的中心。"
                "他们拥有耗能巨大的大脑，也会使用贝壳、赭石板、弓箭和骨针。\n\n"
                "布隆伯斯洞穴里，贝壳珠子和赭石板被摆在岩壁旁，成为符号能力的稳定空间。\n\n"
                "远处的尼安德特人、直立人和丹尼索瓦人不是背景板，"
                "他们需要在多个镜头里保持清晰可辨的族群外观。\n\n"
                "黎凡特地区和地中海东部进入冰河期，智人与尼安德特人在寒冷遭遇地带擦肩而过。\n\n"
                "多巴火山喷发后，火山灰遮天蔽日，南亚暗无天日，全球气温骤降。\n\n"
                "索马里一侧的红海海口迁徙渡口前，智人跨过红海望向阿拉伯半岛。\n\n"
                "他们沿印度洋海岸线前进，经过印度河、恒河、湄公河和多个河口。\n\n"
                "巽他大陆尽头海峡前，100公里宽的汪洋隔开澳大利亚和巴布亚新几内亚。\n\n"
                "欧洲的尼安德特人在零下6°C的严冬里守着寒冷营地。\n\n"
                "最后，洞穴壁画与葬礼仪式空间里，岩壁、壁画、红花和葬礼仪式连接成共同想象。\n\n"
                "股票市场、CPU 和汽车油箱只作为现代比喻出现，不能进入古史场景池。"
            ),
            "fact_boundaries": {
                "explicitly_supported": ["测试材料明确支持"],
                "dramatized_inference": [],
                "needs_manual_check": [],
                "possible_overstatement": [],
                "suggested_sources": [],
            },
            "subjects": [
                {
                    "name": "智人",
                    "type": "人群",
                    "intro": "本集核心人群。",
                    "visual_modeling": "卡通人物群像。",
                    "script_usage": "承担主角视角。",
                }
            ],
            "map_shots": [
                {
                    "title": "非洲迁徙前景",
                    "region": "africa",
                    "places": ["非洲"],
                    "route": None,
                    "description": "展示故事发生区域。",
                    "script_scene": 1,
                }
            ],
        }

    def review_script(self, source_payload, draft):
        return {
            "passed": True,
            "score": 5,
            "verdict": "通过。",
            "theme_alignment": "贴合。",
            "story_completeness": "完整。",
            "continuity": "连贯。",
            "material_usage": "充分。",
            "key_node_depth": "充分。",
            "simplicity_risk": "不简陋。",
            "missing_content": [],
            "issues": [],
            "revision_brief": "",
        }

    def revise_script(self, source_payload, draft, review):
        return self.generate_script(source_payload)

    def adapt_script_for_storyboard(self, payload):
        self.adapt_payload = payload
        return {
            "title": "智人为什么从非洲走向世界 - 分镜剧本",
            "adapted_article": "分镜剧本旁白第一段。\n\n分镜剧本旁白第二段。",
            "adapted_segments": [
                {
                    "segment_id": "seg-001",
                    "voiceover": "分镜剧本旁白第一段。",
                    "dramatic_function": "开场钩子",
                    "visual_goal": "建立弱小智人的开场处境。",
                    "visual_progression": "从抽象主题进入东非草原具体画面。",
                    "scene_intent": "群像",
                    "continuity_hint": "用草原地平线承接下一段。",
                    "fact_boundary": "保留史实边界。",
                }
            ],
            "adaptation_notes": ["把文章式讲述改成可分镜段落。"],
            "review_notes": [],
        }

    def edit_selection(self, payload):
        self.edit_payload = payload
        intent = payload.get("intent")
        if intent in {"SMALLTALK", "EXPLAIN_SCRIPT"}:
            return {
                "answer": "你好，我是剧本对话助手，可以解释、评审和生成候选修改。",
                "replacement": "",
                "used_context_ids": [],
            }
        if intent == "EXPLAIN_SELECTION":
            return {
                "answer": "这段在说明智人能力起点普通，但叙事能力已经出现。",
                "replacement": "",
                "used_context_ids": [],
            }
        if intent == "REVIEW_SELECTION":
            return {
                "answer": "这段有钩子，但可以补一个更具体的历史因果。",
                "replacement": "",
                "used_context_ids": [],
            }
        if intent == "ASK_SOURCE":
            return {
                "answer": "这个说法需要结合材料依据判断，当前资料片段支持谨慎表述。",
                "replacement": "",
                "used_context_ids": [payload["contexts"][0]["chunk_id"]] if payload.get("contexts") else [],
            }
        return {
            "answer": "你好，我可以阅读、评审和局部修改当前剧本。" if not payload.get("selection") else "这段可以补充火与烹饪如何释放能量。",
            "replacement": "" if not payload.get("selection") else "火和烹饪让食物更容易消化，也减少了咀嚼时间，于是更多能量可以供给大脑。",
            "used_context_ids": [payload["contexts"][0]["chunk_id"]] if payload.get("contexts") else [],
        }


class FakeVisualAnchorProvider:
    def __init__(self):
        self.calls = []

    def generate_image(self, *, prompt, negative_prompt):
        self.calls.append({"prompt": prompt, "negative_prompt": negative_prompt})
        return {
            "image_bytes": b"fake-anchor-image",
            "mime_type": "image/png",
            "model": "fake-seeddream",
            "provider": "ark",
        }


class PlanningScriptProvider(FakeScriptProvider):
    def __init__(self, plan):
        super().__init__()
        self.plan = plan
        self.plan_payload = None

    def plan_assistant_action(self, payload):
        self.plan_payload = payload
        return self.plan


class CapturingStoryboardProvider(RuleBasedStoryboardProvider):
    def __init__(self):
        self.payload = None

    def generate_storyboard(self, payload):
        self.payload = payload
        return super().generate_storyboard(payload)


def test_homepage_renders_material_prep_workspace(tmp_path):
    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", refiner_provider=FakeDeepSeekProvider())
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "AI短剧工作站" in html
    assert "材料准备" in html
    assert "上传" in html
    assert "解析" in html
    assert "搜索书名、查看解析状态" in html


def test_homepage_renders_script_generation_workspace(tmp_path):
    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", refiner_provider=FakeDeepSeekProvider())
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "材料准备" in html
    assert "剧本生成" in html
    assert "输入短剧主题" in html
    assert "时间范围" in html
    assert "勾选时间线" in html


def test_homepage_renders_scene_builder_workspace(tmp_path):
    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", refiner_provider=FakeDeepSeekProvider())
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "场景搭建" in html
    assert "主体池" in html
    assert "场景池" in html
    assert "剧本分镜" in html
    assert "选择已有剧本" in html
    assert "解析主体" in html
    assert "管理短剧中需要保持视觉一致的环境空间" in html
    assert "解析场景" in html
    assert "storyboardScriptInput" in html
    assert "storyboardUploadButton" in html
    assert "storyboardSelectButton" in html
    assert "storyboardGenerateButton" in html
    assert "分镜记录" in html


def test_subject_pool_renders_asset_workbench_layout(tmp_path):
    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", refiner_provider=FakeDeepSeekProvider())
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "visual-subject-toolbar" in html
    assert "visual-mode-tabs" in html
    assert 'data-visual-mode="all">所有主体' in html
    assert 'data-visual-mode="scripts">剧本主体' in html
    assert "visual-workbench-grid" in html
    assert "visual-script-list" in html
    assert "visualSubjectSearchInput" in html
    assert "搜索主体，例如：智人、尼安德特人" in html
    assert "当前剧本主体" in html
    assert "解析主体" in html
    assert 'id="visualExtractButton" type="button">解析主体' in html
    assert 'id="visualExtractButton" type="button">开始解析' not in html
    assert 'aria-label="主体详情"' not in html
    assert 'id="visualAnchorButton"' not in html
    assert "请选择一个主体查看视觉身份、一致性规则和锚点图生成状态。" not in html
    assert "识别并管理短剧中需要保持视觉一致的主体，为后续图片和视频生成做准备。" not in html


def test_subject_pool_frontend_links_to_dedicated_detail_page_before_anchor_generation():
    app_js = (Path(__file__).parents[1] / "drama_agents" / "webapp" / "static" / "app.js").read_text(
        encoding="utf-8"
    )

    assert 'href="${visualSubjectDetailUrl(subject.subject_id)}">详情</a>' in app_js
    assert "function visualSubjectDetailUrl" in app_js
    assert "function renderVisualSubjectInlineDetail" not in app_js
    assert "function showVisualSubjectDetail" not in app_js
    assert "selectedVisualSubjectDetail" not in app_js
    assert 'data-visual-anchor="${escapeHtml(subject.subject_id)}">生成锚点图</button>' not in app_js
    assert 'visualSubjectDetail: document.querySelector("#visualSubjectDetail")' not in app_js


def test_subject_pool_frontend_maps_status_to_chinese():
    app_js = (Path(__file__).parents[1] / "drama_agents" / "webapp" / "static" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "function visualStatusLabel" in app_js
    assert "not_parsed" in app_js
    assert "未解析" in app_js
    assert "parsing" in app_js
    assert "解析中" in app_js
    assert "parsed" in app_js
    assert "已解析" in app_js
    assert "failed" in app_js
    assert "解析失败" in app_js
    assert 'subjects.length ? "parsed" : "not_parsed"' not in app_js


def test_subject_pool_frontend_has_two_display_modes():
    app_js = (Path(__file__).parents[1] / "drama_agents" / "webapp" / "static" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "visualMode: \"all\"" in app_js
    assert "function setVisualSubjectMode" in app_js
    assert "data-visual-mode" in app_js
    assert "visual-script-open" in app_js
    assert ">主体</button>" in app_js


def test_script_subject_frontend_drills_into_sorted_subject_rows():
    app_js = (Path(__file__).parents[1] / "drama_agents" / "webapp" / "static" / "app.js").read_text(
        encoding="utf-8"
    )
    styles_css = (Path(__file__).parents[1] / "drama_agents" / "webapp" / "static" / "styles.css").read_text(
        encoding="utf-8"
    )

    assert 'visualScriptStage: "list"' in app_js
    assert "function setVisualScriptStage" in app_js
    assert "function showVisualScriptList" in app_js
    assert "function sortVisualScriptSubjectsByImportance" in app_js
    assert "sortVisualScriptSubjectsByImportance(state.visualScriptSubjects || [])" in app_js
    assert 'data-visual-script-back' in app_js
    assert 'href="${visualSubjectDetailUrl(subject.subject_id)}">详情</a>' in app_js
    assert 'dataset.visualScriptStage = nextStage' in app_js
    assert 'data-visual-script-stage="list"' in styles_css
    assert 'data-visual-script-stage="subjects"' in styles_css
    assert 'grid-template-columns: minmax(0, 1fr);' in styles_css


def test_visual_subject_detail_page_renders_subject_and_anchor_action(tmp_path):
    _app, client, generation = app_with_generated_script(tmp_path)
    extraction = client.post(
        "/api/visual/subjects/extract-from-script",
        json={"generation_id": generation["generation_id"]},
    ).get_json()
    sapiens = next(subject for subject in extraction["subjects"] if subject["canonical_name"] == "智人")

    response = client.get(f"/visual/subjects/{sapiens['subject_id']}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "主体详情" in html
    assert "智人" in html
    assert "视觉身份" in html
    assert "一致性规则" in html
    assert "主体图构建" in html
    assert "视觉阶段" in html
    assert "获取提示词" in html
    assert "生成主体图" in html
    assert "data-subject-prompt-textarea" in html
    assert f"/api/visual/subjects/{sapiens['subject_id']}/anchor" in html


def test_visual_subject_prompt_api_returns_default_cartoon_prompt(tmp_path):
    _app, client, generation = app_with_generated_script(tmp_path)
    extraction = client.post(
        "/api/visual/subjects/extract-from-script",
        json={"generation_id": generation["generation_id"]},
    ).get_json()
    sapiens = next(subject for subject in extraction["subjects"] if subject["canonical_name"] == "智人")

    response = client.get(f"/api/visual/subjects/{sapiens['subject_id']}/anchor-prompt")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["subject_id"] == sapiens["subject_id"]
    assert "智人" in payload["prompt"]
    assert "历史科普卡通短剧" in payload["prompt"]
    assert "只生成一个主体" in payload["prompt"]
    assert "不要多个主体" in payload["negative_prompt"]


def test_sources_api_lists_workspace_pdfs(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    pdf_path = library / "demo.pdf"
    create_pdf(pdf_path)

    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", refiner_provider=FakeDeepSeekProvider())
    client = app.test_client()

    response = client.get("/api/sources")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["sources"][0]["name"] == "demo.pdf"
    assert payload["sources"][0]["relative_path"] == "资料库/demo.pdf"


def test_sources_api_lists_supported_text_and_ebook_materials(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    (library / "demo.md").write_text("# 第一章\n正文", encoding="utf-8")
    (library / "notes.txt").write_text("纯文本正文", encoding="utf-8")
    (library / "book.epub").write_bytes(b"placeholder")
    (library / "book.mobi").write_bytes(b"placeholder")
    (library / "book.docx").write_bytes(b"placeholder")

    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", refiner_provider=FakeDeepSeekProvider())
    client = app.test_client()

    response = client.get("/api/sources")

    assert response.status_code == 200
    names = {source["name"] for source in response.get_json()["sources"]}
    assert {"demo.md", "notes.txt", "book.epub", "book.mobi", "book.docx"}.issubset(names)


def test_upload_api_accepts_markdown_material(tmp_path):
    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", refiner_provider=FakeDeepSeekProvider())
    client = app.test_client()

    response = client.post(
        "/api/upload",
        data={"file": (BytesIO("# 第一章\n正文".encode("utf-8")), "demo.md")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"]["name"] == "demo.md"
    assert (tmp_path / "uploads" / "demo.md").exists()


def test_split_api_processes_selected_pdf(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    pdf_path = library / "demo.pdf"
    create_pdf(pdf_path)

    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", refiner_provider=FakeDeepSeekProvider())
    client = app.test_client()

    response = client.post("/api/split", json={"relative_path": "资料库/demo.pdf"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["book_id"] == "demo"
    assert len(payload["chapters"]) == 2
    assert (tmp_path / "outputs" / "material_splits" / "demo" / "manifest.json").exists()


def test_split_api_processes_selected_markdown(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    md_path = library / "demo.md"
    md_path.write_text("# 第一章 文明开始\n这里是正文。", encoding="utf-8")

    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", refiner_provider=FakeDeepSeekProvider())
    client = app.test_client()

    response = client.post("/api/split", json={"relative_path": "资料库/demo.md"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["book_id"] == "demo"
    assert payload["chapters"][0]["source_format"] == "md"
    assert payload["chapters"][0]["pdf_link"] == ""


def test_parse_api_creates_record_with_book_metrics(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    pdf_path = library / "demo.pdf"
    create_pdf(pdf_path)

    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", refiner_provider=FakeDeepSeekProvider())
    client = app.test_client()

    response = client.post("/api/parse", json={"relative_path": "资料库/demo.pdf"})

    assert response.status_code == 200
    payload = response.get_json()
    record = payload["record"]
    assert record["book_name"] == "demo.pdf"
    assert record["chapter_count"] == 2
    assert record["refinement_status"] == "completed"
    assert record["refined_chapter_count"] == 2
    assert record["total_words"] >= 0
    assert record["detail_url"] == "/materials/demo"
    assert record["parsed_at"]
    assert re.fullmatch(r"\d{8} \d{4}", record["parsed_at"])

    records_response = client.get("/api/records")
    records_payload = records_response.get_json()
    assert records_payload["records"][0]["record_id"] == "demo"


def test_parse_api_persists_record_and_chapters_to_sqlite(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    pdf_path = library / "demo.pdf"
    create_pdf(pdf_path)

    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", refiner_provider=FakeDeepSeekProvider())
    client = app.test_client()

    response = client.post("/api/parse", json={"relative_path": "资料库/demo.pdf"})

    assert response.status_code == 200
    db_path = tmp_path / "outputs" / "material_workstation.sqlite3"
    assert db_path.exists()
    database = MaterialDatabase(db_path)
    assert database.find_record("demo")["book_name"] == "demo.pdf"
    assert [chapter["chapter_id"] for chapter in database.list_chapters("demo")] == ["ch01", "ch02"]


def test_delete_parse_record_removes_record_from_list(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    pdf_path = library / "demo.pdf"
    create_pdf(pdf_path)

    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", refiner_provider=FakeDeepSeekProvider())
    client = app.test_client()
    client.post("/api/parse", json={"relative_path": "资料库/demo.pdf"})

    response = client.delete("/api/records/demo")

    assert response.status_code == 200
    assert client.get("/api/records").get_json()["records"] == []


def test_update_record_book_name_persists_to_database_and_snapshot(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    pdf_path = library / "demo.pdf"
    create_pdf(pdf_path)

    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", refiner_provider=FakeDeepSeekProvider())
    client = app.test_client()
    client.post("/api/parse", json={"relative_path": "资料库/demo.pdf"})

    response = client.patch("/api/records/demo", json={"book_name": "我的世界史材料"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["record"]["book_name"] == "我的世界史材料"
    assert MaterialDatabase(tmp_path / "outputs" / "material_workstation.sqlite3").find_record("demo")[
        "book_name"
    ] == "我的世界史材料"
    assert client.get("/api/records").get_json()["records"][0]["book_name"] == "我的世界史材料"


def test_material_detail_page_renders_parse_result(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    pdf_path = library / "demo.pdf"
    create_pdf(pdf_path)

    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", refiner_provider=FakeDeepSeekProvider())
    client = app.test_client()
    client.post("/api/parse", json={"relative_path": "资料库/demo.pdf"})

    response = client.get("/materials/demo")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "demo.pdf" in html
    assert "章节详情" in html
    assert "/materials/demo/chapters/ch01" in html


def test_chapter_reader_page_renders_refined_content(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    pdf_path = library / "demo.pdf"
    create_pdf(pdf_path)

    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", refiner_provider=FakeDeepSeekProvider())
    client = app.test_client()
    client.post("/api/parse", json={"relative_path": "资料库/demo.pdf"})

    response = client.get("/materials/demo/chapters/ch01")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "精读：1 First chapter" in html
    assert "这里是 DeepSeek 整理后的自然正文" in html
    assert "地图提示" in html
    assert "原始 PDF" in html


def test_timeline_api_creates_timeline_record_and_artifacts(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    pdf_path = library / "demo.pdf"
    create_pdf(pdf_path)

    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
    )
    client = app.test_client()
    client.post("/api/parse", json={"relative_path": "资料库/demo.pdf"})

    response = client.post("/api/materials/demo/timeline")

    assert response.status_code == 200
    payload = response.get_json()
    record = payload["record"]
    assert record["timeline_status"] == "completed"
    assert record["timeline_event_count"] == 2
    assert record["timeline_url"] == "/materials/demo/timeline"
    assert (tmp_path / "outputs" / "material_splits" / "demo" / "timeline" / "timeline.json").exists()

    database = MaterialDatabase(tmp_path / "outputs" / "material_workstation.sqlite3")
    events = database.list_timeline_events("demo")
    assert len(events) == 2
    assert events[0]["title"] == "ch01 智人文化能力逐步形成"


def test_script_timeline_sources_api_lists_completed_timelines(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    pdf_path = library / "demo.pdf"
    create_pdf(pdf_path)

    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
    )
    client = app.test_client()
    client.post("/api/parse", json={"relative_path": "资料库/demo.pdf"})
    client.post("/api/materials/demo/timeline")

    response = client.get("/api/script/timeline-sources")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["timeline_sources"] == [
        {
            "record_id": "demo",
            "book_name": "demo.pdf",
            "timeline_event_count": 2,
            "timeline_url": "/materials/demo/timeline",
        }
    ]


def test_script_generate_api_returns_script_only_with_deferred_subjects_and_map_shots(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    pdf_path = library / "demo.pdf"
    create_pdf(pdf_path)

    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
        subject_provider=RuleBasedVisualSubjectProvider(),
        scene_provider=RuleBasedVisualSceneProvider(),
    )
    client = app.test_client()
    client.post("/api/parse", json={"relative_path": "资料库/demo.pdf"})
    client.post("/api/materials/demo/timeline")

    response = client.post(
        "/api/script/generate",
        json={
            "topic": "智人为什么从非洲走向世界",
            "time_range": "约 20 万年前 — 5 万年前",
            "timeline_record_ids": ["demo"],
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result"]["status"] == "completed"
    assert payload["result"]["matched_event_count"] == 2
    assert payload["result"]["script"]["title"] == "智人为什么从非洲走向世界 - 短剧稿"
    assert payload["result"]["subjects"] == []
    assert payload["result"]["map_shots"] == []


def test_script_generate_api_accepts_numeric_year_inputs(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    pdf_path = library / "demo.pdf"
    create_pdf(pdf_path)

    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
        subject_provider=RuleBasedVisualSubjectProvider(),
        scene_provider=RuleBasedVisualSceneProvider(),
    )
    client = app.test_client()
    client.post("/api/parse", json={"relative_path": "资料库/demo.pdf"})
    client.post("/api/materials/demo/timeline")

    response = client.post(
        "/api/script/generate",
        json={
            "topic": "智人为什么从非洲走向世界",
            "time_start_year": -200000,
            "time_end_year": -50000,
            "timeline_record_ids": ["demo"],
        },
    )

    assert response.status_code == 200
    result = response.get_json()["result"]
    assert result["time_range"] == "20万年前 — 5万年前"
    assert result["time_start_year"] == -200000
    assert result["time_end_year"] == -50000
    assert result["matched_event_count"] == 2


def test_script_generations_api_lists_and_deletes_generated_scripts(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    pdf_path = library / "demo.pdf"
    create_pdf(pdf_path)

    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    client.post("/api/parse", json={"relative_path": "资料库/demo.pdf"})
    client.post("/api/materials/demo/timeline")
    generated = client.post(
        "/api/script/generate",
        json={
            "topic": "智人为什么从非洲走向世界",
            "time_range": "约 20 万年前 — 5 万年前",
            "timeline_record_ids": ["demo"],
        },
    ).get_json()["result"]

    response = client.get("/api/script/generations")

    assert response.status_code == 200
    records = response.get_json()["generations"]
    assert records[0]["generation_id"] == generated["generation_id"]
    assert records[0]["topic"] == "智人为什么从非洲走向世界"
    assert records[0]["script_title"] == "智人为什么从非洲走向世界 - 短剧稿"
    assert records[0]["time_range"] == "约 20 万年前 — 5 万年前"
    assert records[0]["subject_count"] == 0
    assert records[0]["map_shot_count"] == 0
    assert records[0]["places"] == []

    delete_response = client.delete(f"/api/script/generations/{generated['generation_id']}")

    assert delete_response.status_code == 200
    assert delete_response.get_json()["generations"] == []


def test_script_generation_record_actions_show_script_and_delete_only():
    app_js = Path(__file__).parents[1] / "drama_agents" / "webapp" / "static" / "app.js"
    render_script_records = app_js.read_text(encoding="utf-8").split("function renderScriptGenerations()", 1)[1].split(
        "async function uploadSelectedFile",
        1,
    )[0]

    action_labels = re.findall(r'<a class="record-action"[^>]*>([^<]+)</a>', render_script_records)

    assert action_labels == ["剧本"]
    assert "data-script-delete" in render_script_records
    assert "/subjects" not in render_script_records
    assert "/maps" not in render_script_records


def test_script_generation_dedicated_view_pages_render_saved_sections(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    pdf_path = library / "demo.pdf"
    create_pdf(pdf_path)

    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    client.post("/api/parse", json={"relative_path": "资料库/demo.pdf"})
    client.post("/api/materials/demo/timeline")
    generation = client.post(
        "/api/script/generate",
        json={
            "topic": "智人为什么从非洲走向世界",
            "time_start_year": -200000,
            "time_end_year": -50000,
            "timeline_record_ids": ["demo"],
        },
    ).get_json()["result"]
    generation_id = generation["generation_id"]

    script_response = client.get(f"/script-generations/{generation_id}/script")
    subjects_response = client.get(f"/script-generations/{generation_id}/subjects")
    maps_response = client.get(f"/script-generations/{generation_id}/maps")

    assert script_response.status_code == 200
    script_html = script_response.get_data(as_text=True)
    tabs_html = script_html[
        script_html.index('<nav class="script-page-tabs"') : script_html.index("</nav>", script_html.index('<nav class="script-page-tabs"'))
    ]
    assert "剧本阅读器" in script_html
    assert 'data-script-reader="' in script_html
    assert "剧本改造" in tabs_html
    assert "分镜剧本" in tabs_html
    assert 'data-storyboard-script-link aria-disabled="true"' in tabs_html
    assert "data-script-adapt" in tabs_html
    assert "script-storyboard-panel" in script_html
    assert "编辑" in script_html
    assert "保存" in script_html
    assert ">主体</a>" not in tabs_html
    assert ">地点</a>" not in tabs_html
    assert "剧本对话助手" not in script_html
    assert "RAG 助手" not in script_html
    assert "构建向量库" not in script_html
    assert "data-rag-" not in script_html
    assert "data-conversation-" not in script_html
    assert "data-selection-" not in script_html
    assert 'data-script-editor hidden' in script_html
    assert "script_reader.js" in script_html
    assert "已选内容" not in script_html
    assert "你的要求" not in script_html
    assert "Agent 建议" not in script_html
    assert "智人开局，装备一般" in script_html
    assert "推导链条" in script_html
    assert "史实出处" in script_html
    assert script_html.index("完整剧本") < script_html.index("推导链条") < script_html.index("史实出处")
    assert '<sup class="script-citation-wrap"><a class="script-citation" href="#source-1"' in script_html
    assert 'id="script-paragraph-1"' in script_html
    assert 'id="source-1"' in script_html
    assert 'href="#script-paragraph-1"' in script_html
    assert "返回正文" in script_html

    assert subjects_response.status_code == 404
    assert maps_response.status_code == 404


def test_script_adaptation_generates_storyboard_script_and_updates_reader(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    provider = FakeScriptProvider()
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=provider,
    )
    client = app.test_client()
    generation = create_generated_script(client)

    response = client.post(f"/api/script/generations/{generation['generation_id']}/storyboard-script/adapt")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["storyboard_script"]["adapted_article"].startswith("分镜剧本旁白第一段")
    assert payload["storyboard_script"]["adapted_segments"][0]["visual_progression"]
    assert provider.adapt_payload["script"]["article"]
    updated = MaterialDatabase(tmp_path / "outputs" / "material_workstation.sqlite3").find_script_generation(
        generation["generation_id"]
    )
    assert updated["script"]["storyboard_script"]["adapted_article"].startswith("分镜剧本旁白第一段")

    html = client.get(f"/script-generations/{generation['generation_id']}/script").get_data(as_text=True)
    tabs_html = html[html.index('<nav class="script-page-tabs"') : html.index("</nav>", html.index('<nav class="script-page-tabs"'))]
    assert "再次改造" in html
    assert "再次改造" in tabs_html
    assert 'data-storyboard-script-link aria-disabled="false"' in tabs_html
    assert "分镜剧本旁白第一段" in html
    assert "开场钩子" in html


def test_storyboard_generation_uses_adapted_storyboard_script(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    script_provider = FakeScriptProvider()
    storyboard_provider = CapturingStoryboardProvider()
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=script_provider,
        storyboard_provider=storyboard_provider,
    )
    client = app.test_client()
    generation = create_generated_script(client)
    client.post(f"/api/script/generations/{generation['generation_id']}/storyboard-script/adapt")

    response = client.post(f"/api/script/generations/{generation['generation_id']}/storyboard/generate")

    assert response.status_code == 200
    assert storyboard_provider.payload["full_script"].startswith("分镜剧本旁白第一段")
    assert storyboard_provider.payload["adapted_segments"][0]["segment_id"] == "seg-001"
    assert response.get_json()["storyboard"]["source_type"] == "storyboard_script"


def app_with_generated_script(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
        subject_provider=RuleBasedVisualSubjectProvider(),
        scene_provider=RuleBasedVisualSceneProvider(),
    )
    client = app.test_client()
    generation = create_generated_script(client)
    return app, client, generation


def test_extract_subjects_from_script_returns_core_visual_subjects(tmp_path):
    _app, client, generation = app_with_generated_script(tmp_path)

    response = client.post("/api/visual/subjects/extract-from-script", json={"generation_id": generation["generation_id"]})

    assert response.status_code == 200
    payload = response.get_json()
    names = {subject["canonical_name"] for subject in payload["subjects"]}
    assert {"智人", "早期智人群体", "尼安德特人", "直立人", "丹尼索瓦人"}.issubset(names)
    assert payload["script_subject_count"] >= 5


def test_extract_subjects_rejects_non_consistency_objects(tmp_path):
    _app, client, generation = app_with_generated_script(tmp_path)

    response = client.post("/api/visual/subjects/extract-from-script", json={"generation_id": generation["generation_id"]})

    assert response.status_code == 200
    payload = response.get_json()
    subject_names = {subject["canonical_name"] for subject in payload["subjects"]}
    rejected = {candidate["name"]: candidate["reason"] for candidate in payload["rejected_candidates"]}
    for name in ["大脑", "火", "狮子", "贝壳", "赭石板", "弓箭", "骨针"]:
        assert name not in subject_names
        assert rejected[name]


def test_visual_subject_prompt_requires_phase_reuse_decision():
    prompt = build_visual_subject_prompt(
        {
            "title": "智人阶段变化",
            "topic": "智人从采集狩猎到农业定居",
            "article": "智人先以采集狩猎生活，后来进入农业定居生活。",
        }
    )

    assert "visual_phase_label" in prompt
    assert "同名主体" in prompt
    assert "能复用" in prompt
    assert "不同阶段" in prompt


def test_subject_extraction_keeps_same_name_different_visual_phases():
    payload = normalize_extraction_payload(
        {
            "subjects": [
                {
                    "canonical_name": "智人",
                    "visual_phase_label": "采集狩猎阶段",
                    "visual_identity": {"clothing": "兽皮", "body_language": "迁徙狩猎"},
                },
                {
                    "canonical_name": "智人",
                    "visual_phase_label": "农业定居阶段",
                    "visual_identity": {"clothing": "粗布", "body_language": "播种收割"},
                },
            ]
        }
    )

    assert len(payload["subjects"]) == 2
    assert {subject["visual_phase_label"] for subject in payload["subjects"]} == {"采集狩猎阶段", "农业定居阶段"}


def test_subject_pool_sorted_by_pinyin(tmp_path):
    _app, client, generation = app_with_generated_script(tmp_path)
    client.post("/api/visual/subjects/extract-from-script", json={"generation_id": generation["generation_id"]})

    response = client.get("/api/visual/subjects")

    assert response.status_code == 200
    payload = response.get_json()
    group_letters = [group["letter"] for group in payload["groups"]]
    assert group_letters == sorted(group_letters)
    assert group_letters[:3] == ["D", "N", "Z"]
    names = [subject["canonical_name"] for subject in payload["subjects"]]
    assert names.index("丹尼索瓦人") < names.index("尼安德特人") < names.index("智人")


def test_script_subjects_are_linked_to_generation(tmp_path):
    _app, client, generation = app_with_generated_script(tmp_path)
    client.post("/api/visual/subjects/extract-from-script", json={"generation_id": generation["generation_id"]})

    response = client.get(f"/api/script/generations/{generation['generation_id']}/visual-subjects")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["generation"]["generation_id"] == generation["generation_id"]
    names = {subject["canonical_name"] for subject in payload["subjects"]}
    assert "智人" in names
    sapiens = next(subject for subject in payload["subjects"] if subject["canonical_name"] == "智人")
    assert sapiens["role_in_script"]
    assert sapiens["first_appearance"]
    assert sapiens["is_global_subject"] is True


def test_same_subject_is_reused_across_scripts(tmp_path):
    _app, client, first_generation = app_with_generated_script(tmp_path)
    client.post("/api/visual/subjects/extract-from-script", json={"generation_id": first_generation["generation_id"]})
    database = MaterialDatabase(tmp_path / "outputs" / "material_workstation.sqlite3")
    second_generation = dict(first_generation)
    second_generation["generation_id"] = "manual-second-script"
    second_generation["topic"] = "早期智人如何讲故事"
    second_generation["script"] = dict(first_generation["script"])
    second_generation["script"]["article"] = "早期智人在洞穴里讲述狩猎经历，现代人类的祖先开始形成共同想象。"
    database.save_script_generation(second_generation)

    response = client.post("/api/visual/subjects/extract-from-script", json={"generation_id": "manual-second-script"})

    assert response.status_code == 200
    pool = client.get("/api/visual/subjects").get_json()["subjects"]
    sapiens_subjects = [subject for subject in pool if subject["canonical_name"] == "智人"]
    assert len(sapiens_subjects) == 1
    assert sapiens_subjects[0]["script_count"] == 2


def test_subject_pool_splits_same_name_into_visual_phases(tmp_path):
    database = MaterialDatabase(tmp_path / "outputs" / "material_workstation.sqlite3")
    for generation_id, topic in [
        ("hunter-script", "采集狩猎阶段的智人"),
        ("farmer-script", "农业定居阶段的智人"),
    ]:
        database.save_script_generation(
            {
                "generation_id": generation_id,
                "created_at": "2026-06-28 10:00:00",
                "topic": topic,
                "time_range": "测试阶段",
                "script": {"article": topic},
                "status": "completed",
            }
        )

    hunter_subjects = database.save_visual_subject_extraction(
        "hunter-script",
        {
            "subjects": [
                {
                    "canonical_name": "智人",
                    "visual_phase_label": "采集狩猎阶段",
                    "subject_type": "species",
                    "role_in_script": "以迁徙和采集为主的早期智人。",
                    "importance": 5,
                    "visual_identity": {
                        "era": "旧石器时代",
                        "lifestyle_stage": "采集狩猎",
                        "appearance": "深色皮肤、粗糙黑发，身形灵活。",
                        "clothing": "简陋兽皮和植物纤维披挂。",
                        "props": ["石器", "木矛"],
                        "body_language": "警觉、迁徙、协作狩猎。",
                        "group_composition": "小型游动部落。",
                    },
                }
            ]
        },
    )
    farmer_subjects = database.save_visual_subject_extraction(
        "farmer-script",
        {
            "subjects": [
                {
                    "canonical_name": "智人",
                    "visual_phase_label": "农业定居阶段",
                    "subject_type": "species",
                    "role_in_script": "以农耕和定居村落为主的智人。",
                    "importance": 5,
                    "visual_identity": {
                        "era": "新石器时代",
                        "lifestyle_stage": "农业定居",
                        "appearance": "已经形成村落分工的早期农人。",
                        "clothing": "粗布、草编和更稳定的工具携带。",
                        "props": ["陶罐", "石镰", "谷物"],
                        "body_language": "播种、收割、村落协作。",
                        "group_composition": "定居家庭和村落群体。",
                    },
                }
            ]
        },
    )

    pool = [subject for subject in database.list_visual_subjects() if subject["canonical_name"] == "智人"]
    assert len(pool) == 2
    assert {subject["visual_phase_label"] for subject in pool} == {"采集狩猎阶段", "农业定居阶段"}
    assert hunter_subjects[0]["subject_id"] != farmer_subjects[0]["subject_id"]


def test_subject_detail_contains_visual_identity(tmp_path):
    _app, client, generation = app_with_generated_script(tmp_path)
    extraction = client.post(
        "/api/visual/subjects/extract-from-script",
        json={"generation_id": generation["generation_id"]},
    ).get_json()
    sapiens = next(subject for subject in extraction["subjects"] if subject["canonical_name"] == "智人")

    response = client.get(f"/api/visual/subjects/{sapiens['subject_id']}")

    assert response.status_code == 200
    detail = response.get_json()["subject"]
    assert detail["visual_identity"]["era"]
    assert detail["visual_identity"]["appearance"]
    assert detail["consistency_rules"]["must_keep"]
    assert detail["consistency_rules"]["avoid"]
    assert detail["visual_prompt"]
    assert detail["negative_prompt"]
    assert detail["appearances"][0]["generation_id"] == generation["generation_id"]


def test_subject_anchor_generation_uses_cartoon_style_and_saves_asset(tmp_path):
    provider = FakeVisualAnchorProvider()
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
        subject_provider=RuleBasedVisualSubjectProvider(),
        anchor_provider=provider,
    )
    client = app.test_client()
    generation = create_generated_script(client)
    extraction = client.post(
        "/api/visual/subjects/extract-from-script",
        json={"generation_id": generation["generation_id"]},
    ).get_json()
    sapiens = next(subject for subject in extraction["subjects"] if subject["canonical_name"] == "智人")

    response = client.post(f"/api/visual/subjects/{sapiens['subject_id']}/anchor")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "completed"
    assert payload["provider"] == "ark"
    assert payload["model"] == "fake-seeddream"
    assert payload["asset_url"].startswith("/outputs/visual_subject_anchors/")
    assert (tmp_path / "outputs" / payload["asset_url"].removeprefix("/outputs/")).read_bytes() == b"fake-anchor-image"

    prompt = provider.calls[0]["prompt"]
    assert "智人" in prompt
    assert "历史科普卡通短剧" in prompt
    assert "半扁平卡通人物" in prompt
    assert "只生成一个主体" in prompt
    assert "纯主体参考图" in prompt
    assert "干净纯色" in prompt
    assert "3-6 个代表性人物" not in prompt
    assert "地图+人物+场景" not in prompt
    assert "不要多人群像" in provider.calls[0]["negative_prompt"]
    assert "不要多个主体" in provider.calls[0]["negative_prompt"]
    assert "不要地图背景" in provider.calls[0]["negative_prompt"]
    assert "不要信息图文字" in provider.calls[0]["negative_prompt"]
    assert "不要太幼稚" in provider.calls[0]["negative_prompt"]

    detail = client.get(f"/api/visual/subjects/{sapiens['subject_id']}").get_json()["subject"]
    assert detail["has_anchor_asset"] is True
    assert detail["anchor_asset_id"] == payload["asset_url"]
    assert detail["workflow_name"] == "ark_seeddream_subject_anchor_v1"


def test_subject_anchor_generation_accepts_edited_prompt(tmp_path):
    provider = FakeVisualAnchorProvider()
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
        subject_provider=RuleBasedVisualSubjectProvider(),
        anchor_provider=provider,
    )
    client = app.test_client()
    generation = create_generated_script(client)
    extraction = client.post(
        "/api/visual/subjects/extract-from-script",
        json={"generation_id": generation["generation_id"]},
    ).get_json()
    sapiens = next(subject for subject in extraction["subjects"] if subject["canonical_name"] == "智人")

    response = client.post(
        f"/api/visual/subjects/{sapiens['subject_id']}/anchor",
        json={"prompt": "用户修改后的主体图 Prompt", "negative_prompt": "不要多人"},
    )

    assert response.status_code == 200
    assert provider.calls[0]["prompt"] == "用户修改后的主体图 Prompt"
    assert provider.calls[0]["negative_prompt"] == "不要多人"
    detail = client.get(f"/api/visual/subjects/{sapiens['subject_id']}").get_json()["subject"]
    assert detail["visual_prompt"] == "用户修改后的主体图 Prompt"
    assert detail["negative_prompt"] == "不要多人"


def test_subject_anchor_generation_reports_missing_ark_configuration(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
        subject_provider=RuleBasedVisualSubjectProvider(),
        anchor_provider=None,
    )
    client = app.test_client()
    generation = create_generated_script(client)
    extraction = client.post(
        "/api/visual/subjects/extract-from-script",
        json={"generation_id": generation["generation_id"]},
    ).get_json()
    sapiens = next(subject for subject in extraction["subjects"] if subject["canonical_name"] == "智人")

    response = client.post(f"/api/visual/subjects/{sapiens['subject_id']}/anchor")

    assert response.status_code == 500
    assert "ARK_API_KEY" in response.get_json()["error"]


def test_rejected_candidates_are_saved_or_returned(tmp_path):
    _app, client, generation = app_with_generated_script(tmp_path)

    response = client.post("/api/visual/subjects/extract-from-script", json={"generation_id": generation["generation_id"]})

    assert response.status_code == 200
    rejected = response.get_json()["rejected_candidates"]
    assert rejected
    assert all(candidate["name"] and candidate["reason"] for candidate in rejected)


def test_extract_scenes_from_script_returns_core_visual_scenes(tmp_path):
    _app, client, generation = app_with_generated_script(tmp_path)

    response = client.post("/api/visual/scenes/extract-from-script", json={"generation_id": generation["generation_id"]})

    assert response.status_code == 200
    payload = response.get_json()
    names = {scene["canonical_name"] for scene in payload["scenes"]}
    assert {
        "东非稀树草原",
        "非洲智人部落营地",
        "篝火烹饪营地",
        "布隆伯斯洞穴",
        "红海海口迁徙渡口",
    }.issubset(names)
    assert payload["script_scene_count"] >= 5


def test_extract_scenes_rejects_props_subjects_and_metaphors(tmp_path):
    _app, client, generation = app_with_generated_script(tmp_path)

    response = client.post("/api/visual/scenes/extract-from-script", json={"generation_id": generation["generation_id"]})

    assert response.status_code == 200
    payload = response.get_json()
    scene_names = {scene["canonical_name"] for scene in payload["scenes"]}
    rejected = {candidate["name"]: candidate["reason"] for candidate in payload["rejected_candidates"]}
    for name in ["大脑", "火", "贝壳", "弓箭", "智人", "尼安德特人", "股票市场"]:
        assert name not in scene_names
        assert rejected[name]


def test_visual_scene_api_returns_json_when_generation_is_missing(tmp_path):
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        scene_provider=RuleBasedVisualSceneProvider(),
    )
    client = app.test_client()

    response = client.post("/api/visual/scenes/extract-from-script", json={"generation_id": "missing-script"})

    assert response.status_code == 404
    assert response.is_json
    assert response.get_json()["error"]


def test_visual_scene_prompt_requires_phase_reuse_decision():
    prompt = build_visual_scene_prompt(
        {
            "title": "同一地区的阶段变化",
            "topic": "东非草原从迁徙通道到农业村落",
            "article": "东非稀树草原先是智人迁徙通道，后来出现农业村落和田地。",
        }
    )

    assert "visual_phase_label" in prompt
    assert "同名场景" in prompt
    assert "能复用" in prompt
    assert "不同阶段" in prompt


def test_scene_extraction_keeps_same_name_different_visual_phases():
    payload = normalize_scene_extraction_payload(
        {
            "scenes": [
                {
                    "canonical_name": "东非稀树草原",
                    "visual_phase_label": "采集狩猎迁徙阶段",
                    "visual_identity": {"terrain": "自然草原迁徙通道", "typical_elements": ["稀树"]},
                },
                {
                    "canonical_name": "东非稀树草原",
                    "visual_phase_label": "农业定居村落阶段",
                    "visual_identity": {"terrain": "田地和村落边缘", "typical_elements": ["田地"]},
                },
            ]
        }
    )

    assert len(payload["scenes"]) == 2
    assert {scene["visual_phase_label"] for scene in payload["scenes"]} == {"采集狩猎迁徙阶段", "农业定居村落阶段"}


def test_save_visual_scene_extraction_reuses_existing_scene(tmp_path):
    _app, client, first_generation = app_with_generated_script(tmp_path)
    client.post("/api/visual/scenes/extract-from-script", json={"generation_id": first_generation["generation_id"]})
    database = MaterialDatabase(tmp_path / "outputs" / "material_workstation.sqlite3")
    second_generation = dict(first_generation)
    second_generation["generation_id"] = "manual-second-scene-script"
    second_generation["topic"] = "智人第二集"
    second_generation["script"] = dict(first_generation["script"])
    second_generation["script"]["article"] = "镜头回到非洲东部的稀树草原，智人继续在开阔草地上迁徙。"
    database.save_script_generation(second_generation)

    response = client.post("/api/visual/scenes/extract-from-script", json={"generation_id": second_generation["generation_id"]})

    assert response.status_code == 200
    pool = client.get("/api/visual/scenes").get_json()["scenes"]
    savannas = [scene for scene in pool if scene["canonical_name"] == "东非稀树草原"]
    assert len(savannas) == 1
    assert savannas[0]["script_count"] == 2


def test_scene_pool_splits_same_name_into_visual_phases(tmp_path):
    database = MaterialDatabase(tmp_path / "outputs" / "material_workstation.sqlite3")
    for generation_id, topic in [
        ("savanna-hunter-script", "采集狩猎阶段的东非草原"),
        ("savanna-village-script", "农业村落阶段的东非草原"),
    ]:
        database.save_script_generation(
            {
                "generation_id": generation_id,
                "created_at": "2026-06-28 10:00:00",
                "topic": topic,
                "time_range": "测试阶段",
                "script": {"article": topic},
                "status": "completed",
            }
        )

    hunter_scenes = database.save_visual_scene_extraction(
        "savanna-hunter-script",
        {
            "scenes": [
                {
                    "canonical_name": "东非稀树草原",
                    "visual_phase_label": "采集狩猎迁徙阶段",
                    "scene_type": "natural_environment",
                    "role_in_script": "承载早期智人迁徙和采集狩猎。",
                    "importance": 5,
                    "visual_identity": {
                        "era": "旧石器时代",
                        "environment_stage": "采集狩猎迁徙",
                        "region": "非洲东部",
                        "terrain": "开阔稀树草原和自然动物迁徙通道。",
                        "weather": "干热微尘。",
                        "lighting": "强烈自然光。",
                        "palette": "黄褐色、暗绿色、土色。",
                        "mood": "危险、辽阔、生存压力强。",
                        "typical_elements": ["稀树", "枯草", "动物剪影"],
                    },
                }
            ]
        },
    )
    village_scenes = database.save_visual_scene_extraction(
        "savanna-village-script",
        {
            "scenes": [
                {
                    "canonical_name": "东非稀树草原",
                    "visual_phase_label": "农业定居村落阶段",
                    "scene_type": "settlement_edge",
                    "role_in_script": "同一地区进入农耕和村落定居后的空间。",
                    "importance": 5,
                    "visual_identity": {
                        "era": "新石器时代",
                        "environment_stage": "农业定居",
                        "region": "非洲东部",
                        "terrain": "草原边缘出现田地、围栏和土坯房。",
                        "weather": "干热但有耕作痕迹。",
                        "lighting": "白天柔和自然光。",
                        "palette": "土黄、草绿、陶土色。",
                        "mood": "定居、劳作、秩序形成。",
                        "typical_elements": ["田地", "围栏", "土坯房", "谷物堆"],
                    },
                }
            ]
        },
    )

    pool = [scene for scene in database.list_visual_scenes() if scene["canonical_name"] == "东非稀树草原"]
    assert len(pool) == 2
    assert {scene["visual_phase_label"] for scene in pool} == {"采集狩猎迁徙阶段", "农业定居村落阶段"}
    assert hunter_scenes[0]["scene_id"] != village_scenes[0]["scene_id"]


def test_list_visual_scenes_sorted_by_pinyin(tmp_path):
    _app, client, generation = app_with_generated_script(tmp_path)
    client.post("/api/visual/scenes/extract-from-script", json={"generation_id": generation["generation_id"]})

    response = client.get("/api/visual/scenes")

    assert response.status_code == 200
    payload = response.get_json()
    group_letters = [group["letter"] for group in payload["groups"]]
    assert group_letters == sorted(group_letters)
    names = [scene["canonical_name"] for scene in payload["scenes"]]
    assert names.index("布隆伯斯洞穴") < names.index("东非稀树草原") < names.index("红海海口迁徙渡口")


def test_script_visual_scenes_are_linked_to_generation(tmp_path):
    _app, client, generation = app_with_generated_script(tmp_path)
    client.post("/api/visual/scenes/extract-from-script", json={"generation_id": generation["generation_id"]})

    response = client.get(f"/api/script/generations/{generation['generation_id']}/visual-scenes")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["generation"]["generation_id"] == generation["generation_id"]
    names = {scene["canonical_name"] for scene in payload["scenes"]}
    assert "东非稀树草原" in names
    savanna = next(scene for scene in payload["scenes"] if scene["canonical_name"] == "东非稀树草原")
    assert savanna["role_in_script"]
    assert savanna["first_appearance"]
    assert savanna["is_global_scene"] is True


def test_visual_scene_detail_contains_visual_identity(tmp_path):
    _app, client, generation = app_with_generated_script(tmp_path)
    extraction = client.post(
        "/api/visual/scenes/extract-from-script",
        json={"generation_id": generation["generation_id"]},
    ).get_json()
    savanna = next(scene for scene in extraction["scenes"] if scene["canonical_name"] == "东非稀树草原")

    response = client.get(f"/api/visual/scenes/{savanna['scene_id']}")

    assert response.status_code == 200
    detail = response.get_json()["scene"]
    assert detail["visual_identity"]["terrain"]
    assert detail["visual_identity"]["lighting"]
    assert detail["visual_identity"]["typical_elements"]
    assert "appearance" not in detail["visual_identity"]
    assert detail["consistency_rules"]["must_keep"]
    assert detail["consistency_rules"]["avoid"]
    assert detail["negative_rules"]
    assert detail["appearances"][0]["generation_id"] == generation["generation_id"]


def test_visual_scene_detail_page_renders_scene_and_anchor_placeholder(tmp_path):
    _app, client, generation = app_with_generated_script(tmp_path)
    extraction = client.post(
        "/api/visual/scenes/extract-from-script",
        json={"generation_id": generation["generation_id"]},
    ).get_json()
    savanna = next(scene for scene in extraction["scenes"] if scene["canonical_name"] == "东非稀树草原")

    response = client.get(f"/visual/scenes/{savanna['scene_id']}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "场景详情" in html
    assert "东非稀树草原" in html
    assert "视觉身份" in html
    assert "一致性规则" in html
    assert "出现过的剧本" in html
    assert "场景图构建" in html
    assert "视觉阶段" in html
    assert "获取提示词" in html
    assert "生成场景图" in html
    assert "data-scene-prompt-textarea" in html
    assert f"/api/visual/scenes/{savanna['scene_id']}/anchor-prompt" in html
    assert f"/api/visual/scenes/{savanna['scene_id']}/anchor" in html
    assert "ARK 场景图生成" in html
    assert "ComfyUI 场景锚点图生成尚未配置" not in html


def test_visual_scene_detail_updates_preview_from_generated_asset(tmp_path):
    _app, client, generation = app_with_generated_script(tmp_path)
    extraction = client.post(
        "/api/visual/scenes/extract-from-script",
        json={"generation_id": generation["generation_id"]},
    ).get_json()
    savanna = next(scene for scene in extraction["scenes"] if scene["canonical_name"] == "东非稀树草原")

    response = client.get(f"/visual/scenes/{savanna['scene_id']}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'data-visual-scene-anchor-preview' in html
    assert 'data-visual-scene-anchor-label' in html
    assert 'payload.asset_url' in html
    assert 'sceneAnchorPreview.src = imageUrl' in html


def test_visual_scene_prompt_api_returns_environment_prompt(tmp_path):
    _app, client, generation = app_with_generated_script(tmp_path)
    extraction = client.post(
        "/api/visual/scenes/extract-from-script",
        json={"generation_id": generation["generation_id"]},
    ).get_json()
    savanna = next(scene for scene in extraction["scenes"] if scene["canonical_name"] == "东非稀树草原")

    response = client.get(f"/api/visual/scenes/{savanna['scene_id']}/anchor-prompt")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["scene_id"] == savanna["scene_id"]
    assert "东非稀树草原" in payload["prompt"]
    assert "历史科普卡通短剧" in payload["prompt"]
    assert "只生成环境空间" in payload["prompt"]
    assert "不要主体人物大特写" in payload["negative_prompt"]
    assert "不要单个道具特写" in payload["negative_prompt"]


def test_scene_anchor_generation_uses_ark_route_and_saves_asset(tmp_path):
    provider = FakeVisualAnchorProvider()
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
        scene_provider=RuleBasedVisualSceneProvider(),
        anchor_provider=provider,
    )
    client = app.test_client()
    generation = create_generated_script(client)
    extraction = client.post(
        "/api/visual/scenes/extract-from-script",
        json={"generation_id": generation["generation_id"]},
    ).get_json()
    savanna = next(scene for scene in extraction["scenes"] if scene["canonical_name"] == "东非稀树草原")

    response = client.post(f"/api/visual/scenes/{savanna['scene_id']}/anchor")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "completed"
    assert payload["provider"] == "ark"
    assert payload["model"] == "fake-seeddream"
    assert payload["asset_url"].startswith("/outputs/visual_scene_anchors/")
    assert (tmp_path / "outputs" / payload["asset_url"].removeprefix("/outputs/")).read_bytes() == b"fake-anchor-image"
    assert "场景图已生成" in payload["message"]

    prompt = provider.calls[0]["prompt"]
    assert "东非稀树草原" in prompt
    assert "只生成环境空间" in prompt
    assert "不要主体人物大特写" in prompt
    assert "不要单个道具特写" in provider.calls[0]["negative_prompt"]

    detail = client.get(f"/api/visual/scenes/{savanna['scene_id']}").get_json()["scene"]
    assert detail["has_anchor_asset"] is True
    assert detail["anchor_asset_id"] == payload["asset_url"]
    assert detail["workflow_name"] == "ark_seeddream_scene_anchor_v1"


def test_scene_anchor_generation_accepts_edited_prompt(tmp_path):
    provider = FakeVisualAnchorProvider()
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
        scene_provider=RuleBasedVisualSceneProvider(),
        anchor_provider=provider,
    )
    client = app.test_client()
    generation = create_generated_script(client)
    extraction = client.post(
        "/api/visual/scenes/extract-from-script",
        json={"generation_id": generation["generation_id"]},
    ).get_json()
    savanna = next(scene for scene in extraction["scenes"] if scene["canonical_name"] == "东非稀树草原")

    response = client.post(
        f"/api/visual/scenes/{savanna['scene_id']}/anchor",
        json={"prompt": "用户修改后的场景图 Prompt", "negative_prompt": "不要人物"},
    )

    assert response.status_code == 200
    assert provider.calls[0]["prompt"] == "用户修改后的场景图 Prompt"
    assert provider.calls[0]["negative_prompt"] == "不要人物"
    detail = client.get(f"/api/visual/scenes/{savanna['scene_id']}").get_json()["scene"]
    assert detail["visual_prompt"] == "用户修改后的场景图 Prompt"
    assert detail["negative_prompt"] == "不要人物"


def test_scene_anchor_generation_reports_missing_ark_configuration(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
        scene_provider=RuleBasedVisualSceneProvider(),
        anchor_provider=None,
    )
    client = app.test_client()
    generation = create_generated_script(client)
    extraction = client.post(
        "/api/visual/scenes/extract-from-script",
        json={"generation_id": generation["generation_id"]},
    ).get_json()
    savanna = next(scene for scene in extraction["scenes"] if scene["canonical_name"] == "东非稀树草原")

    response = client.post(f"/api/visual/scenes/{savanna['scene_id']}/anchor")

    assert response.status_code == 500
    assert "ARK_API_KEY" in response.get_json()["error"]


def test_rejected_scene_candidates_are_returned(tmp_path):
    _app, client, generation = app_with_generated_script(tmp_path)

    response = client.post("/api/visual/scenes/extract-from-script", json={"generation_id": generation["generation_id"]})

    assert response.status_code == 200
    rejected = response.get_json()["rejected_candidates"]
    assert rejected
    assert all(candidate["name"] and candidate["reason"] for candidate in rejected)


def test_visual_scene_api_extract_from_script(tmp_path):
    _app, client, generation = app_with_generated_script(tmp_path)

    response = client.post("/api/visual/scenes/extract-from-script", json={"generation_id": generation["generation_id"]})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["generation"]["generation_id"] == generation["generation_id"]
    assert payload["scenes"]
    assert payload["script_scene_count"] == len(payload["scenes"])


def test_scene_pool_renders_visual_workbench_layout(tmp_path):
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        scene_provider=RuleBasedVisualSceneProvider(),
    )
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "visual-scene-toolbar" in html
    assert 'data-visual-scene-mode="all">所有场景' in html
    assert 'data-visual-scene-mode="scripts">剧本场景' in html
    assert "sceneScriptSelect" in html
    assert "sceneExtractButton" in html
    assert "解析场景" in html
    assert "scenePool" in html
    assert "当前剧本场景" in html
    assert 'data-scene-script-back' in html
    assert 'id="sceneWorkbenchGrid" data-visual-current-mode="all" data-visual-script-stage="list"' in html
    assert "用于管理东非草原、黎凡特地区、布隆伯斯洞穴" not in html


def test_scene_pool_frontend_mirrors_subject_modes():
    app_js = (Path(__file__).parents[1] / "drama_agents" / "webapp" / "static" / "app.js").read_text(
        encoding="utf-8"
    )

    assert 'visualSceneMode: "all"' in app_js
    assert 'visualSceneScriptStage: "list"' in app_js
    assert "function setVisualSceneMode" in app_js
    assert "function setVisualSceneScriptStage" in app_js
    assert "function showVisualSceneScriptList" in app_js
    assert "setVisualSceneMode(\"scripts\", { scriptStage: \"subjects\" })" in app_js
    assert "elements.sceneModeTabs" in app_js
    assert "elements.sceneScriptBackButtons" in app_js


def test_scene_frontend_uses_safe_json_reader_for_scene_requests():
    app_js = (Path(__file__).parents[1] / "drama_agents" / "webapp" / "static" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "async function readJsonResponse" in app_js
    assert 'readJsonResponse(response, "场景池加载失败")' in app_js
    assert 'readJsonResponse(response, "场景解析失败")' in app_js
    assert 'readJsonResponse(response, "上传解析失败")' in app_js
    assert 'readJsonResponse(response, "剧本场景读取失败")' in app_js


def test_subject_pool_still_works_after_scene_changes(tmp_path):
    _app, client, generation = app_with_generated_script(tmp_path)
    scene_response = client.post("/api/visual/scenes/extract-from-script", json={"generation_id": generation["generation_id"]})
    subject_response = client.post("/api/visual/subjects/extract-from-script", json={"generation_id": generation["generation_id"]})

    assert scene_response.status_code == 200
    assert subject_response.status_code == 200
    subjects = client.get("/api/visual/subjects").get_json()["subjects"]
    subject_names = {subject["canonical_name"] for subject in subjects}
    assert "智人" in subject_names
    detail = client.get(f"/api/visual/subjects/{subjects[0]['subject_id']}")
    assert detail.status_code == 200


def create_generated_script(client):
    client.post("/api/parse", json={"relative_path": "资料库/demo.pdf"})
    client.post("/api/materials/demo/timeline")
    return client.post(
        "/api/script/generate",
        json={
            "topic": "智人为什么从非洲走向世界",
            "time_start_year": -200000,
            "time_end_year": -50000,
            "timeline_record_ids": ["demo"],
        },
    ).get_json()["result"]


def count_script_edit_patches(db_path: Path, generation_id: str) -> int:
    with sqlite3.connect(db_path) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM script_edit_patches WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()[0]


def find_script_edit_patch(db_path: Path, patch_id: int) -> dict:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM script_edit_patches WHERE patch_id = ?", (patch_id,)).fetchone()
    assert row is not None
    return dict(row)


def test_script_article_can_be_edited_and_saved(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    generation = create_generated_script(client)

    response = client.patch(
        f"/api/script/generations/{generation['generation_id']}/article",
        json={"article": "第一段修改后。\n\n第二段也修改。"},
    )

    assert response.status_code == 200
    updated = response.get_json()["generation"]
    assert updated["script"]["article"] == "第一段修改后。\n\n第二段也修改。"
    html = client.get(f"/script-generations/{generation['generation_id']}/script").get_data(as_text=True)
    assert "第一段修改后。" in html
    assert "第二段也修改。" in html


def test_script_rag_builds_vector_index_and_assists_rewrite(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    provider = FakeScriptProvider()
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=provider,
    )
    client = app.test_client()
    generation = create_generated_script(client)

    build_response = client.post(f"/api/script/generations/{generation['generation_id']}/rag/build")

    assert build_response.status_code == 200
    assert build_response.get_json()["chunk_count"] >= 1

    assist_response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={"selection": "智人开局，装备一般", "instruction": "参考文明迁徙内容重写"},
    )

    assert assist_response.status_code == 200
    payload = assist_response.get_json()
    assert "replacement" in payload["result"]
    assert payload["result"]["intent"] == "PROPOSE_EDIT"
    assert payload["result"]["patch_id"]
    assert payload["contexts"]
    assert "DeepSeek 整理后的自然正文" in provider.edit_payload["contexts"][0]["text"]
    assert provider.edit_payload["script"]["article"]
    assert provider.edit_payload["script"]["causal_chain"] == ["智人开局 → 故事系统上线"]

    chat_response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={"selection": "", "instruction": "你好"},
    )

    assert chat_response.status_code == 200
    chat_payload = chat_response.get_json()
    assert chat_payload["result"]["intent"] == "SMALLTALK"
    assert chat_payload["result"]["replacement"] == ""
    assert chat_payload["contexts"] == []

    confirm_response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={"selection": "", "instruction": "可以，就按这个改"},
    )

    assert confirm_response.status_code == 200
    confirm_payload = confirm_response.get_json()
    assert confirm_payload["result"]["applied"] is False
    assert confirm_payload["result"]["intent"] == "APPLY_PATCH"
    assert "patch_id" in confirm_payload["result"]["answer"]
    updated = MaterialDatabase(tmp_path / "outputs" / "material_workstation.sqlite3").find_script_generation(
        generation["generation_id"]
    )
    assert "火和烹饪让食物更容易消化" not in updated["script"]["article"]
    assert "这波不是迁徙，是人类大型开图。" in updated["script"]["article"]

    with sqlite3.connect(tmp_path / "outputs" / "material_workstation.sqlite3") as connection:
        message_count = connection.execute(
            "SELECT COUNT(*) FROM script_assistant_messages WHERE generation_id = ?",
            (generation["generation_id"],),
        ).fetchone()[0]
    assert message_count >= 6


def test_smalltalk_does_not_create_patch(tmp_path, monkeypatch):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    generation = create_generated_script(client)

    def fail_search(*args, **kwargs):
        raise AssertionError("SMALLTALK should not call RAG")

    monkeypatch.setattr(LocalVectorStore, "search", fail_search)

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "message": "你好",
            "selection": {
                "text": "智人开局，装备一般",
                "paragraph_id": "script-paragraph-1",
                "start_offset": 0,
                "end_offset": 8,
            },
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result"]["intent"] == "SMALLTALK"
    assert payload["result"]["replacement"] == ""
    assert payload["result"]["patch_id"] is None
    assert payload["result"]["needs_confirmation"] is False
    assert payload["contexts"] == []
    assert count_script_edit_patches(tmp_path / "outputs" / "material_workstation.sqlite3", generation["generation_id"]) == 0


def test_selection_explain_does_not_create_patch(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    generation = create_generated_script(client)

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "message": "这段什么意思",
            "selection": {"text": "智人开局，装备一般", "paragraph_id": "script-paragraph-1"},
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result"]["intent"] == "EXPLAIN_SELECTION"
    assert payload["result"]["replacement"] == ""
    assert payload["result"]["patch_id"] is None
    assert count_script_edit_patches(tmp_path / "outputs" / "material_workstation.sqlite3", generation["generation_id"]) == 0


def test_selection_look_at_this_uses_selected_context(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    provider = FakeScriptProvider()
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=provider,
    )
    client = app.test_client()
    generation = create_generated_script(client)

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "message": "你先看一下这一段",
            "selection": {"text": "智人开局，装备一般", "paragraph_id": "script-paragraph-1"},
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result"]["intent"] == "REVIEW_SELECTION"
    assert "你好，我在" not in payload["result"]["answer"]
    assert provider.edit_payload["selection"] == "智人开局，装备一般"
    assert provider.edit_payload["intent"] == "REVIEW_SELECTION"


def test_selection_general_quality_question_uses_selected_context(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    provider = FakeScriptProvider()
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=provider,
    )
    client = app.test_client()
    generation = create_generated_script(client)

    for message in ("这段怎么样？", "这段如何？"):
        provider.edit_payload = None
        response = client.post(
            f"/api/script/generations/{generation['generation_id']}/assist",
            json={
                "message": message,
                "selection": {"text": "智人开局，装备一般", "paragraph_id": "script-paragraph-1"},
            },
        )

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["result"]["intent"] == "REVIEW_SELECTION"
        assert "你好，我在" not in payload["result"]["answer"]
        assert provider.edit_payload["selection"] == "智人开局，装备一般"
        assert provider.edit_payload["intent"] == "REVIEW_SELECTION"


def test_planner_handles_natural_selection_chat_without_rag(tmp_path, monkeypatch):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    provider = PlanningScriptProvider(
        {
            "intent": "review_selection",
            "tool": "chat_with_selection",
            "needs_rag": False,
            "selection_policy": "use_current_selection",
            "reason": "用户在询问选中段落开头是否抓人，只需要理解和评价选区。",
        }
    )
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=provider,
    )
    client = app.test_client()
    generation = create_generated_script(client)

    def fail_search(*args, **kwargs):
        raise AssertionError("planner said this turn should not call RAG")

    monkeypatch.setattr(LocalVectorStore, "search", fail_search)

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "message": "你觉得这个开头够抓人吗？",
            "selection": {"text": "智人开局，装备一般", "paragraph_id": "script-paragraph-1"},
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result"]["intent"] == "REVIEW_SELECTION"
    assert payload["result"]["replacement"] == ""
    assert payload["result"]["patch_id"] is None
    assert payload["contexts"] == []
    assert provider.plan_payload["message"] == "你觉得这个开头够抓人吗？"
    assert provider.plan_payload["selection"]["text"] == "智人开局，装备一般"
    assert {tool["name"] for tool in provider.plan_payload["available_tools"]} == {
        "plain_chat",
        "chat_with_selection",
        "search_sources",
        "propose_edit",
        "apply_patch",
        "reject_patch",
    }
    assert provider.edit_payload["intent"] == "REVIEW_SELECTION"
    assert provider.edit_payload["contexts"] == []
    assert count_script_edit_patches(tmp_path / "outputs" / "material_workstation.sqlite3", generation["generation_id"]) == 0


def test_planner_edit_turn_uses_rag_and_creates_pending_patch(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    provider = PlanningScriptProvider(
        {
            "intent": "propose_edit",
            "tool": "propose_edit",
            "needs_rag": True,
            "selection_policy": "use_current_selection",
            "reason": "用户要求把选中段落改得更抓人，需要检索材料并生成候选修改。",
        }
    )
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=provider,
    )
    client = app.test_client()
    generation = create_generated_script(client)

    build_response = client.post(f"/api/script/generations/{generation['generation_id']}/rag/build")
    assert build_response.status_code == 200

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "message": "这里能不能更抓人一点？",
            "selection": {"text": "智人开局，装备一般", "paragraph_id": "script-paragraph-1"},
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result"]["intent"] == "PROPOSE_EDIT"
    assert payload["result"]["needs_confirmation"] is True
    assert payload["result"]["patch_id"]
    assert payload["contexts"]
    assert "DeepSeek 整理后的自然正文" in provider.edit_payload["contexts"][0]["text"]
    assert provider.edit_payload["intent"] == "PROPOSE_EDIT"
    assert provider.edit_payload["selection"] == "智人开局，装备一般"


def test_review_selection_does_not_create_patch(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    generation = create_generated_script(client)

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "message": "这段哪里不好",
            "selection": {"text": "智人开局，装备一般", "paragraph_id": "script-paragraph-1"},
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result"]["intent"] == "REVIEW_SELECTION"
    assert payload["result"]["replacement"] == ""
    assert payload["result"]["patch_id"] is None
    assert count_script_edit_patches(tmp_path / "outputs" / "material_workstation.sqlite3", generation["generation_id"]) == 0


def test_propose_edit_creates_pending_patch(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    provider = FakeScriptProvider()
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=provider,
    )
    client = app.test_client()
    generation = create_generated_script(client)

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "message": "帮我润色这段",
            "selection": {"text": "智人开局，装备一般", "paragraph_id": "script-paragraph-1"},
            "intent_hint": "edit",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    patch_id = payload["result"]["patch_id"]
    assert payload["result"]["intent"] == "PROPOSE_EDIT"
    assert payload["result"]["needs_confirmation"] is True
    assert payload["result"]["replacement"] == "火和烹饪让食物更容易消化，也减少了咀嚼时间，于是更多能量可以供给大脑。"
    patch = find_script_edit_patch(tmp_path / "outputs" / "material_workstation.sqlite3", patch_id)
    assert patch["status"] == "pending"
    assert patch["selection_hash"]
    assert patch["article_version_hash"]
    assert patch["paragraph_id"] == "script-paragraph-1"


def test_plain_key_word_can_not_apply_patch(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    generation = create_generated_script(client)
    proposal = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "message": "帮我润色这段",
            "selection": {"text": "智人开局，装备一般", "paragraph_id": "script-paragraph-1"},
        },
    )
    assert proposal.status_code == 200

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={"message": "可以"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result"]["intent"] == "APPLY_PATCH"
    assert payload["result"]["applied"] is False
    assert "patch_id" in payload["result"]["answer"]
    updated = MaterialDatabase(tmp_path / "outputs" / "material_workstation.sqlite3").find_script_generation(
        generation["generation_id"]
    )
    assert "火和烹饪让食物更容易消化" not in updated["script"]["article"]


def test_apply_patch_requires_patch_id(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    generation = create_generated_script(client)

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={"message": "按这个改", "intent_hint": "apply_patch"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result"]["intent"] == "APPLY_PATCH"
    assert payload["result"]["applied"] is False
    assert payload["result"]["patch_id"] is None
    assert "patch_id" in payload["result"]["answer"]


def test_apply_patch_checks_article_hash_or_unique_selection(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    generation = create_generated_script(client)
    proposal = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "message": "帮我润色这段",
            "selection": {"text": "智人开局，装备一般", "paragraph_id": "script-paragraph-1"},
        },
    )
    proposal_payload = proposal.get_json()
    patch_id = proposal_payload["result"]["patch_id"]
    conversation_id = proposal_payload["conversation"]["conversation_id"]
    client.patch(
        f"/api/script/generations/{generation['generation_id']}/article",
        json={"article": generation["script"]["article"] + "\n\n智人开局，装备一般"},
    )

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "conversation_id": conversation_id,
            "message": "应用这个修改",
            "intent_hint": "apply_patch",
            "patch_id": patch_id,
        },
    )

    assert response.status_code == 409
    payload = response.get_json()
    assert payload["result"]["applied"] is False
    assert payload["result"]["patch_id"] == patch_id
    assert "无法唯一定位" in payload["result"]["answer"]


def test_reject_patch_marks_rejected(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    generation = create_generated_script(client)
    proposal = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "message": "帮我润色这段",
            "selection": {"text": "智人开局，装备一般", "paragraph_id": "script-paragraph-1"},
        },
    )
    proposal_payload = proposal.get_json()
    patch_id = proposal_payload["result"]["patch_id"]
    conversation_id = proposal_payload["conversation"]["conversation_id"]

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "conversation_id": conversation_id,
            "message": "放弃这版",
            "intent_hint": "reject_patch",
            "patch_id": patch_id,
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result"]["intent"] == "REJECT_PATCH"
    assert payload["result"]["applied"] is False
    patch = find_script_edit_patch(tmp_path / "outputs" / "material_workstation.sqlite3", patch_id)
    assert patch["status"] == "rejected"


def test_rag_not_called_for_smalltalk(tmp_path, monkeypatch):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    generation = create_generated_script(client)
    calls = []

    def fake_search(*args, **kwargs):
        calls.append((args, kwargs))
        return []

    monkeypatch.setattr(LocalVectorStore, "search", fake_search)

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={"message": "这个功能怎么用"},
    )

    assert response.status_code == 200
    assert response.get_json()["result"]["intent"] == "SMALLTALK"
    assert calls == []


def test_rag_called_for_source_question(tmp_path, monkeypatch):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    provider = FakeScriptProvider()
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=provider,
    )
    client = app.test_client()
    generation = create_generated_script(client)
    calls = []

    def fake_search(self, query, *, record_ids=None, limit=6):
        calls.append({"query": query, "record_ids": record_ids, "limit": limit})
        return [{"chunk_id": "demo:1", "record_id": "demo", "text": "测试材料依据"}]

    monkeypatch.setattr(LocalVectorStore, "search", fake_search)

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={"message": "这个说法有史实依据吗"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result"]["intent"] == "ASK_SOURCE"
    assert calls
    assert payload["contexts"][0]["chunk_id"] == "demo:1"


def test_create_conversation(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    generation = create_generated_script(client)

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assistant/conversations",
        json={"title": "开头修改讨论"},
    )

    assert response.status_code == 200
    conversation = response.get_json()["conversation"]
    assert conversation["conversation_id"]
    assert conversation["title"] == "开头修改讨论"
    assert conversation["created_at"]
    assert conversation["updated_at"]
    assert conversation["message_count"] == 0
    assert conversation["last_message_preview"] == ""


def test_list_conversations_ordered_by_updated_at(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    generation = create_generated_script(client)
    older = client.post(
        f"/api/script/generations/{generation['generation_id']}/assistant/conversations",
        json={"title": "旧对话"},
    ).get_json()["conversation"]
    newer = client.post(
        f"/api/script/generations/{generation['generation_id']}/assistant/conversations",
        json={"title": "新对话"},
    ).get_json()["conversation"]

    client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={"conversation_id": older["conversation_id"], "message": "你好"},
    )

    response = client.get(f"/api/script/generations/{generation['generation_id']}/assistant/conversations")

    assert response.status_code == 200
    conversations = response.get_json()["conversations"]
    assert conversations[0]["conversation_id"] == older["conversation_id"]
    assert {conversation["conversation_id"] for conversation in conversations} == {
        older["conversation_id"],
        newer["conversation_id"],
    }


def test_assist_auto_creates_conversation_when_missing(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    generation = create_generated_script(client)

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={"message": "你好"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["conversation"]["conversation_id"]
    assert payload["conversation"]["message_count"] == 2
    assert payload["conversation"]["title"] == "你好"


def test_conversation_title_uses_first_user_message(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    generation = create_generated_script(client)
    conversation = client.post(
        f"/api/script/generations/{generation['generation_id']}/assistant/conversations",
        json={},
    ).get_json()["conversation"]

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "conversation_id": conversation["conversation_id"],
            "message": "   请解释这段开头为什么这样写，并指出有没有事实依据不足的地方。   ",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["conversation"]["title"] == "请解释这段开头为什么这样写，并指出有没有事实依据不足的地方。"
    assert payload["conversation"]["title_manual"] is False


def test_conversation_title_uses_paragraph_action_summary(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    generation = create_generated_script(client)
    conversation = client.post(
        f"/api/script/generations/{generation['generation_id']}/assistant/conversations",
        json={},
    ).get_json()["conversation"]

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "conversation_id": conversation["conversation_id"],
            "message": "解释这段",
            "intent_hint": "explain",
            "selection": {"text": "这波不是迁徙，是人类大型开图。", "paragraph_id": "script-paragraph-3"},
        },
    )

    assert response.status_code == 200
    assert response.get_json()["conversation"]["title"] == "解释第 3 段"


def test_manual_title_is_not_overwritten(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    generation = create_generated_script(client)
    conversation = client.post(
        f"/api/script/generations/{generation['generation_id']}/assistant/conversations",
        json={},
    ).get_json()["conversation"]
    update_response = client.patch(
        f"/api/script/generations/{generation['generation_id']}/assistant/conversations/{conversation['conversation_id']}",
        json={"title": "手动命名的开头讨论"},
    )

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "conversation_id": conversation["conversation_id"],
            "message": "这条消息不应该覆盖手动标题",
        },
    )

    assert update_response.status_code == 200
    assert update_response.get_json()["conversation"]["title_manual"] is True
    assert response.status_code == 200
    assert response.get_json()["conversation"]["title"] == "手动命名的开头讨论"


def test_assist_saves_messages_to_conversation(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    generation = create_generated_script(client)
    conversation = client.post(
        f"/api/script/generations/{generation['generation_id']}/assistant/conversations",
        json={"title": "阅读讨论"},
    ).get_json()["conversation"]

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={"conversation_id": conversation["conversation_id"], "message": "你好"},
    )
    detail = client.get(
        f"/api/script/generations/{generation['generation_id']}/assistant/conversations/{conversation['conversation_id']}"
    )

    assert response.status_code == 200
    assert detail.status_code == 200
    payload = detail.get_json()
    assert [message["role"] for message in payload["messages"]] == ["user", "assistant"]
    assert payload["messages"][0]["content"] == "你好"
    assert payload["messages"][0]["conversation_id"] == conversation["conversation_id"]
    assert payload["messages"][1]["intent"] == "SMALLTALK"
    assert payload["conversation"]["message_count"] == 2


def test_message_with_selection_stays_in_current_conversation(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    generation = create_generated_script(client)
    first = client.post(
        f"/api/script/generations/{generation['generation_id']}/assistant/conversations",
        json={"title": "当前对话"},
    ).get_json()["conversation"]
    second = client.post(
        f"/api/script/generations/{generation['generation_id']}/assistant/conversations",
        json={"title": "另一个对话"},
    ).get_json()["conversation"]

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "conversation_id": second["conversation_id"],
            "message": "解释这段",
            "intent_hint": "explain",
            "selection": {"text": "智人开局，装备一般", "paragraph_id": "script-paragraph-1"},
        },
    )
    detail = client.get(
        f"/api/script/generations/{generation['generation_id']}/assistant/conversations/{second['conversation_id']}"
    )
    list_response = client.get(f"/api/script/generations/{generation['generation_id']}/assistant/conversations")

    assert response.status_code == 200
    assert response.get_json()["conversation"]["conversation_id"] == second["conversation_id"]
    assert detail.get_json()["messages"][0]["conversation_id"] == second["conversation_id"]
    assert len(list_response.get_json()["conversations"]) == 2
    assert first["conversation_id"] in {item["conversation_id"] for item in list_response.get_json()["conversations"]}


def test_assist_uses_current_conversation_history_only(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    provider = FakeScriptProvider()
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=provider,
    )
    client = app.test_client()
    generation = create_generated_script(client)
    first = client.post(
        f"/api/script/generations/{generation['generation_id']}/assistant/conversations",
        json={"title": "第一轮"},
    ).get_json()["conversation"]
    second = client.post(
        f"/api/script/generations/{generation['generation_id']}/assistant/conversations",
        json={"title": "第二轮"},
    ).get_json()["conversation"]

    client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "conversation_id": first["conversation_id"],
            "selection": {"text": "智人开局，装备一般", "paragraph_id": "script-paragraph-1"},
            "message": "帮我润色这段",
        },
    )
    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "conversation_id": second["conversation_id"],
            "selection": {"text": "这波不是迁徙，是人类大型开图。", "paragraph_id": "script-paragraph-2"},
            "message": "帮我润色这段",
        },
    )

    assert response.status_code == 200
    conversation_payload = provider.edit_payload["conversation"]
    assert all("智人开局" not in item["content"] for item in conversation_payload)
    assert all(item.get("conversation_id") in {"", second["conversation_id"]} for item in conversation_payload)


def test_delete_conversation_archives_it(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    generation = create_generated_script(client)
    conversation = client.post(
        f"/api/script/generations/{generation['generation_id']}/assistant/conversations",
        json={"title": "待删除"},
    ).get_json()["conversation"]

    response = client.delete(
        f"/api/script/generations/{generation['generation_id']}/assistant/conversations/{conversation['conversation_id']}"
    )
    list_response = client.get(f"/api/script/generations/{generation['generation_id']}/assistant/conversations")

    assert response.status_code == 200
    assert list_response.status_code == 200
    assert list_response.get_json()["conversations"] == []
    with sqlite3.connect(tmp_path / "outputs" / "material_workstation.sqlite3") as connection:
        archived = connection.execute(
            "SELECT is_archived FROM script_assistant_conversations WHERE conversation_id = ?",
            (conversation["conversation_id"],),
        ).fetchone()[0]
    assert archived == 1


def test_selection_change_does_not_switch_conversation(tmp_path):
    reader_script = (Path(__file__).parent.parent / "drama_agents" / "webapp" / "static" / "script_reader.js").read_text(encoding="utf-8")

    assert "loadSelectionHistory(selectedText)" not in reader_script
    assert "loadConversation(" in reader_script
    assert "currentConversationId" in reader_script


def test_selection_history_is_manual_only(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    generation = create_generated_script(client)
    conversation = client.post(
        f"/api/script/generations/{generation['generation_id']}/assistant/conversations",
        json={"title": "选区讨论"},
    ).get_json()["conversation"]
    client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "conversation_id": conversation["conversation_id"],
            "selection": {"text": "智人开局，装备一般", "paragraph_id": "script-paragraph-1"},
            "message": "帮我润色这段",
        },
    )

    history_response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assistant/selection-history",
        json={"selection": " 智人开局，装备一般[1] "},
    )
    detail = client.get(
        f"/api/script/generations/{generation['generation_id']}/assistant/conversations/{conversation['conversation_id']}"
    )

    assert history_response.status_code == 200
    history_payload = history_response.get_json()
    assert history_payload["match_count"] >= 2
    assert history_payload["messages"][0]["selection"] == "智人开局，装备一般"
    assert len(detail.get_json()["messages"]) == 2


def test_patch_is_scoped_to_conversation(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    generation = create_generated_script(client)
    first = client.post(
        f"/api/script/generations/{generation['generation_id']}/assistant/conversations",
        json={"title": "第一轮"},
    ).get_json()["conversation"]
    second = client.post(
        f"/api/script/generations/{generation['generation_id']}/assistant/conversations",
        json={"title": "第二轮"},
    ).get_json()["conversation"]
    proposal = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "conversation_id": first["conversation_id"],
            "selection": {"text": "智人开局，装备一般", "paragraph_id": "script-paragraph-1"},
            "message": "帮我润色这段",
        },
    )
    patch_id = proposal.get_json()["result"]["patch_id"]

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "conversation_id": second["conversation_id"],
            "message": "应用这个修改",
            "intent_hint": "apply_patch",
            "patch_id": patch_id,
        },
    )

    assert response.status_code == 404
    assert response.get_json()["result"]["applied"] is False
    updated = MaterialDatabase(tmp_path / "outputs" / "material_workstation.sqlite3").find_script_generation(
        generation["generation_id"]
    )
    assert "火和烹饪让食物更容易消化" not in updated["script"]["article"]


def test_legacy_messages_have_fallback_conversation(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    generation = create_generated_script(client)
    database_path = tmp_path / "outputs" / "material_workstation.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO script_assistant_messages (
                generation_id, role, content, selection_text, result_json, contexts_json, conversation_id
            ) VALUES (?, 'user', '旧消息', '智人开局，装备一般', '{}', '[]', '')
            """,
            (generation["generation_id"],),
        )

    response = client.get(f"/api/script/generations/{generation['generation_id']}/assistant/conversations")
    conversation = response.get_json()["conversations"][0]
    detail = client.get(
        f"/api/script/generations/{generation['generation_id']}/assistant/conversations/{conversation['conversation_id']}"
    )

    assert response.status_code == 200
    assert conversation["title"] == "旧对话"
    assert conversation["message_count"] == 1
    assert detail.get_json()["messages"][0]["content"] == "旧消息"


def test_focus_switches_when_user_says_explain_this_selection(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    provider = FakeScriptProvider()
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=provider,
    )
    client = app.test_client()
    generation = create_generated_script(client)
    conversation = client.post(
        f"/api/script/generations/{generation['generation_id']}/assistant/conversations",
        json={},
    ).get_json()["conversation"]
    client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "conversation_id": conversation["conversation_id"],
            "message": "解释这段",
            "intent_hint": "explain",
            "selection": {"text": "智人开局，装备一般", "paragraph_id": "script-paragraph-1"},
        },
    )

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "conversation_id": conversation["conversation_id"],
            "message": "解释这段",
            "intent_hint": "explain",
            "selection": {"text": "这波不是迁徙，是人类大型开图。", "paragraph_id": "script-paragraph-2"},
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result"]["focus_action"] == "SWITCH_TO_NEW"
    assert payload["conversation"]["active_paragraph_id"] == "script-paragraph-2"
    assert provider.edit_payload["selection"] == "这波不是迁徙，是人类大型开图。"


def test_focus_keeps_current_when_user_says_continue(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    provider = FakeScriptProvider()
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=provider,
    )
    client = app.test_client()
    generation = create_generated_script(client)
    first = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "message": "帮我润色这段",
            "selection": {"text": "智人开局，装备一般", "paragraph_id": "script-paragraph-1"},
        },
    )
    conversation_id = first.get_json()["conversation"]["conversation_id"]

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "conversation_id": conversation_id,
            "message": "继续改短一点",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result"]["focus_action"] == "KEEP_CURRENT"
    assert provider.edit_payload["selection"] == "智人开局，装备一般"


def test_focus_uses_new_selection_as_reference(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    provider = FakeScriptProvider()
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=provider,
    )
    client = app.test_client()
    generation = create_generated_script(client)
    first = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "message": "解释这段",
            "intent_hint": "explain",
            "selection": {"text": "智人开局，装备一般", "paragraph_id": "script-paragraph-1"},
        },
    )
    conversation_id = first.get_json()["conversation"]["conversation_id"]

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "conversation_id": conversation_id,
            "message": "结合这段补一下前面",
            "selection": {"text": "这波不是迁徙，是人类大型开图。", "paragraph_id": "script-paragraph-2"},
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result"]["focus_action"] == "USE_NEW_AS_REFERENCE"
    assert provider.edit_payload["selection"] == "智人开局，装备一般"
    assert provider.edit_payload["reference_selection"]["text"] == "这波不是迁徙，是人类大型开图。"


def test_focus_compares_two_selections(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    provider = FakeScriptProvider()
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=provider,
    )
    client = app.test_client()
    generation = create_generated_script(client)
    first = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "message": "解释这段",
            "intent_hint": "explain",
            "selection": {"text": "智人开局，装备一般", "paragraph_id": "script-paragraph-1"},
        },
    )
    conversation_id = first.get_json()["conversation"]["conversation_id"]

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "conversation_id": conversation_id,
            "message": "这两段有什么区别",
            "selection": {"text": "这波不是迁徙，是人类大型开图。", "paragraph_id": "script-paragraph-2"},
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result"]["focus_action"] == "COMPARE_CURRENT_AND_NEW"
    assert provider.edit_payload["focus_action"] == "COMPARE_CURRENT_AND_NEW"
    assert provider.edit_payload["selection"] == "智人开局，装备一般"
    assert provider.edit_payload["reference_selection"]["text"] == "这波不是迁徙，是人类大型开图。"


def test_focus_asks_clarification_when_ambiguous(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    provider = FakeScriptProvider()
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=provider,
    )
    client = app.test_client()
    generation = create_generated_script(client)
    first = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "message": "帮我润色这段",
            "selection": {"text": "智人开局，装备一般", "paragraph_id": "script-paragraph-1"},
        },
    )
    conversation_id = first.get_json()["conversation"]["conversation_id"]
    before_patch_count = count_script_edit_patches(
        tmp_path / "outputs" / "material_workstation.sqlite3",
        generation["generation_id"],
    )

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "conversation_id": conversation_id,
            "message": "这样行吗",
            "selection": {"text": "这波不是迁徙，是人类大型开图。", "paragraph_id": "script-paragraph-2"},
        },
    )
    after_patch_count = count_script_edit_patches(
        tmp_path / "outputs" / "material_workstation.sqlite3",
        generation["generation_id"],
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result"]["focus_action"] == "ASK_CLARIFICATION"
    assert "刚才那段" in payload["result"]["answer"]
    assert after_patch_count == before_patch_count
    assert provider.edit_payload["selection"] == "智人开局，装备一般"


def test_script_assistant_keeps_conversation_context_between_turns(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    provider = FakeScriptProvider()
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=provider,
    )
    client = app.test_client()
    generation = create_generated_script(client)
    client.post(f"/api/script/generations/{generation['generation_id']}/rag/build")

    first_response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={"selection": "智人开局，装备一般", "instruction": "加几个疑问句开头"},
    )
    conversation_id = first_response.get_json()["conversation"]["conversation_id"]
    followup_response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={"conversation_id": conversation_id, "selection": "", "instruction": "不，疑问应该放在开头，然后再进入原文"},
    )

    assert first_response.status_code == 200
    assert followup_response.status_code == 200
    conversation = provider.edit_payload["conversation"]
    assert any(item["role"] == "user" and "加几个疑问句开头" in item["content"] for item in conversation)
    assert any(item["role"] == "assistant" and "这段可以补充" in item["content"] for item in conversation)
    assert any(item["selection"] == "智人开局，装备一般" for item in conversation)


def test_script_assistant_applies_pending_edit_only_with_patch_id(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    provider = FakeScriptProvider()
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=provider,
    )
    client = app.test_client()
    generation = create_generated_script(client)
    client.post(f"/api/script/generations/{generation['generation_id']}/rag/build")

    assist_response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={"selection": "智人开局，装备一般", "instruction": "为我修改这里，加上疑问句开头"},
    )
    assist_payload = assist_response.get_json()
    patch_id = assist_payload["result"]["patch_id"]
    conversation_id = assist_payload["conversation"]["conversation_id"]
    apply_response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={
            "conversation_id": conversation_id,
            "message": "应用这个修改",
            "intent_hint": "apply_patch",
            "patch_id": patch_id,
        },
    )

    assert assist_response.status_code == 200
    assert apply_response.status_code == 200
    payload = apply_response.get_json()
    assert payload["result"]["applied"] is True
    assert payload["result"]["patch_id"] == patch_id
    updated = MaterialDatabase(tmp_path / "outputs" / "material_workstation.sqlite3").find_script_generation(
        generation["generation_id"]
    )
    assert "火和烹饪让食物更容易消化" in updated["script"]["article"]


def test_script_assistant_history_restores_messages_for_selected_text(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    provider = FakeScriptProvider()
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=provider,
    )
    client = app.test_client()
    generation = create_generated_script(client)
    client.post(f"/api/script/generations/{generation['generation_id']}/rag/build")

    client.post(
        f"/api/script/generations/{generation['generation_id']}/assist",
        json={"selection": "智人开局，装备一般", "instruction": "加几个疑问句开头"},
    )

    response = client.post(
        f"/api/script/generations/{generation['generation_id']}/assist/history",
        json={"selection": " 智人开局，装备一般[1] "},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["selection"] == " 智人开局，装备一般[1] "
    assert payload["match_count"] >= 2
    assert [message["role"] for message in payload["messages"]] == ["user", "assistant"]
    assert payload["messages"][0]["content"] == "加几个疑问句开头"
    assert payload["messages"][1]["replacement"] == "火和烹饪让食物更容易消化，也减少了咀嚼时间，于是更多能量可以供给大脑。"


def test_timeline_generation_updates_material_vector_index(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    create_pdf(library / "demo.pdf")
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
        script_provider=FakeScriptProvider(),
    )
    client = app.test_client()
    client.post("/api/parse", json={"relative_path": "资料库/demo.pdf"})

    response = client.post("/api/materials/demo/timeline")

    assert response.status_code == 200
    store = LocalVectorStore(tmp_path / "outputs" / "material_rag.sqlite3")
    results = store.search("DeepSeek 整理后的自然正文 文明 迁徙", record_ids=["demo"], limit=1)
    assert results
    assert "DeepSeek 整理后的自然正文" in results[0]["text"]


def test_script_reader_uses_chinese_article_typography():
    styles = (Path(__file__).parent.parent / "drama_agents" / "webapp" / "static" / "styles.css").read_text(encoding="utf-8")
    reader_script = (Path(__file__).parent.parent / "drama_agents" / "webapp" / "static" / "script_reader.js").read_text(encoding="utf-8")
    template = (Path(__file__).parent.parent / "drama_agents" / "webapp" / "templates" / "script_generation_view.html").read_text(encoding="utf-8")
    article_rule = styles[styles.index(".script-article-card p {") : styles.index(".script-article-card p:last-child")]
    article_card_rule = styles[styles.index(".script-article-card {") : styles.index(".script-article-card p {")]
    page_shell_rule = styles[styles.index(".script-page-shell {") : styles.index(".script-page-hero")]
    workspace_rule = styles[styles.index(".script-reader-workspace {") : styles.index(".script-editor-toolbar")]
    main_rule = styles[styles.index(".script-reader-main {") : styles.index(".script-editor-toolbar")]
    toolbar_rule = styles[styles.index(".script-editor-toolbar {") : styles.index(".script-editor-toolbar h3")]

    assert "text-indent: 2em;" in article_rule
    assert "margin: 0;" in article_rule
    assert "white-space: pre-line" not in article_rule
    assert ".script-citation-wrap" in styles
    assert "[hidden]" in styles
    assert "display: none !important;" in styles
    assert "width: min(100vw - 64px, 1420px);" in page_shell_rule
    assert "grid-template-columns: minmax(0, 1fr) minmax(360px, 0.86fr);" in workspace_rule
    assert "height: auto;" in workspace_rule
    assert "overflow: visible;" in workspace_rule
    assert ".script-storyboard-panel" in styles
    assert "data-script-adapt" in template
    assert "data-storyboard-script-link" in template
    assert "max-width: 100%;" in article_card_rule
    assert "max-width: 100%;" in toolbar_rule
    assert "height: auto;" in main_rule
    assert "overflow-y: visible;" in main_rule
    assert "const assistantEnabled" in reader_script
    assert ".script-rag-message::before" in styles
    assert '.script-rag-message.user::before' in styles
    assert '.script-rag-message.assistant::before' in styles
    assert 'content: "你";' in styles
    assert 'content: "AI";' in styles
    assert 'display.addEventListener("mousedown"' in reader_script
    assert 'document.addEventListener("mouseup", syncSelectionAfterPointerUp)' in reader_script
    assert 'document.addEventListener("touchend", syncSelectionAfterPointerUp)' in reader_script
    assert 'display.addEventListener("keyup"' in reader_script
    assert "selectionchange" not in reader_script
    assert "isPointerSelecting" in reader_script
    assert "选中内容：" not in reader_script
    assert "我的要求：" not in reader_script
    assert "formatSelectedQuote" not in reader_script
    assert "stripSelectedQuoteFromInstruction" not in reader_script
    assert "loadSelectionHistory" in reader_script
    assert "/assistant/selection-history" in reader_script
    assert "/assist/history" not in reader_script
    assert "ragComposer.value = selectedText" not in reader_script
    assert "剧本对话助手" not in template
    assert "data-rag-" not in template
    assert "data-conversation-" not in template
    assert "data-selection-" not in template
    assert "styles.css') }}?v=20260630a" in template
    assert "script_reader.js') }}?v=20260630a" in template
    assert ".script-conversation-bar" in styles
    assert ".script-conversation-sidebar" in styles
    assert ".script-conversation-list" in styles
    assert ".script-selection-card" in styles
    assert "initializeConversations" in reader_script
    assert "currentConversationId" in reader_script
    assert "conversation_id" in reader_script
    assert "sendAssistantMessage" in reader_script
    assert "intent_hint" in reader_script
    assert "applyPatch" in reader_script
    assert "rejectPatch" in reader_script
    assert "patch_id" in reader_script


def test_conversation_list_limits_to_three_in_frontend_logic():
    reader_script = (Path(__file__).parent.parent / "drama_agents" / "webapp" / "static" / "script_reader.js").read_text(encoding="utf-8")
    template = (Path(__file__).parent.parent / "drama_agents" / "webapp" / "templates" / "script_generation_view.html").read_text(encoding="utf-8")
    styles = (Path(__file__).parent.parent / "drama_agents" / "webapp" / "static" / "styles.css").read_text(encoding="utf-8")

    assert "data-conversation-sidebar" not in template
    assert "script-conversation-sidebar" not in template
    assert "script-conversation-more-menu" in reader_script
    assert "conversationsExpanded" in reader_script
    assert "conversations.slice(0, 3)" in reader_script
    assert "data-conversation-more" not in template
    assert ".script-conversation-more-menu" in styles


def test_selection_does_not_load_history_automatically():
    reader_script = (Path(__file__).parent.parent / "drama_agents" / "webapp" / "static" / "script_reader.js").read_text(encoding="utf-8")

    assert "insertSelectionIntoComposer" in reader_script
    assert "parseComposerSubmission" in reader_script
    assert "loadSelectionHistory(selectedSelection.text)" in reader_script
    update_selected_block = reader_script[
        reader_script.index("function updateSelectedText()") : reader_script.index("function syncSelectionAfterPointerUp()")
    ]
    assert "insertSelectionIntoComposer" in update_selected_block
    assert "loadSelectionHistory" not in update_selected_block
    assert "loadConversation" not in update_selected_block
    assert "currentConversationId =" not in update_selected_block


def test_composer_send_button_is_compact_inside_textarea():
    template = (Path(__file__).parent.parent / "drama_agents" / "webapp" / "templates" / "script_generation_view.html").read_text(encoding="utf-8")
    styles = (Path(__file__).parent.parent / "drama_agents" / "webapp" / "static" / "styles.css").read_text(encoding="utf-8")
    reader_script = (Path(__file__).parent.parent / "drama_agents" / "webapp" / "static" / "script_reader.js").read_text(encoding="utf-8")

    assert "script-rag-composer-inner" not in template
    assert 'class="script-rag-send"' not in template
    send_rule = styles[styles.index(".script-rag-send") : styles.index(".script-rag-send:hover")]
    assert "position: absolute;" in send_rule
    assert "Enter 发送，Shift + Enter 换行" not in template
    assert 'event.key === "Enter" && !event.shiftKey' in reader_script


def test_selected_message_renders_attachment_and_clears_before_request():
    styles = (Path(__file__).parent.parent / "drama_agents" / "webapp" / "static" / "styles.css").read_text(encoding="utf-8")
    reader_script = (Path(__file__).parent.parent / "drama_agents" / "webapp" / "static" / "script_reader.js").read_text(encoding="utf-8")

    assert "let isSendingAssistantMessage = false;" in reader_script
    assert "function setComposerSending(isSending)" in reader_script
    assert "function renderSelectionAttachment(container, selection)" in reader_script
    assert 'addMessage("user", message, null, selectionForMessage)' in reader_script
    assert ".script-rag-selection-attachment" in styles
    assert ".script-rag-send:disabled" in styles

    ask_agent_block = reader_script[
        reader_script.index("async function askAgent()") : reader_script.index("async function sendAssistantMessage")
    ]
    assert "if (isSendingAssistantMessage)" in ask_agent_block
    assert ask_agent_block.index('ragComposer.value = "";') < ask_agent_block.index(
        "await sendAssistantMessage"
    )


def test_timeline_api_can_force_rebuild_existing_timeline(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    pdf_path = library / "demo.pdf"
    create_pdf(pdf_path)
    timeline_provider = FakeTimelineProvider()

    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=timeline_provider,
    )
    client = app.test_client()
    client.post("/api/parse", json={"relative_path": "资料库/demo.pdf"})
    client.post("/api/materials/demo/timeline")
    first_call_count = timeline_provider.calls

    response = client.post("/api/materials/demo/timeline", json={"force": True})

    assert response.status_code == 200
    assert timeline_provider.calls == first_call_count + 2


def test_material_detail_page_links_to_timeline_actions(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    pdf_path = library / "demo.pdf"
    create_pdf(pdf_path)

    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
    )
    client = app.test_client()
    client.post("/api/parse", json={"relative_path": "资料库/demo.pdf"})
    client.post("/api/materials/demo/timeline")

    response = client.get("/materials/demo")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "生成时间线" in html
    assert "/materials/demo/timeline" in html


def test_timeline_page_renders_event_modules(tmp_path):
    library = tmp_path / "资料库"
    library.mkdir()
    pdf_path = library / "demo.pdf"
    create_pdf(pdf_path)

    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        refiner_provider=FakeDeepSeekProvider(),
        timeline_provider=FakeTimelineProvider(),
    )
    client = app.test_client()
    client.post("/api/parse", json={"relative_path": "资料库/demo.pdf"})
    client.post("/api/materials/demo/timeline")

    response = client.get("/materials/demo/timeline")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "全书时间线" in html
    assert "约公元前 200000 年至公元前 50000 年" in html
    assert "非洲及早期智人扩散区域" in html
    assert "原文给出章节时间范围" in html
    assert "/materials/demo/chapters/ch01" in html
