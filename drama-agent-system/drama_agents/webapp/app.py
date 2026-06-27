from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

from drama_agents.chapter_refiner import ChapterRefiner, result_to_payload as refinement_result_to_payload
from drama_agents.map_api import REGIONS, clamp_int, ensure_default_data, parse_bbox, parse_bool, region_label, render_map
from drama_agents.material_splitter import MaterialSplitter, SUPPORTED_MATERIAL_EXTENSIONS, slugify
from drama_agents.script_agent import ScriptAgent, render_script_markdown
from drama_agents.script_assistant import ScriptAssistantController
from drama_agents.storage import MaterialDatabase
from drama_agents.timeline_builder import TimelineBuilder, result_to_payload as timeline_result_to_payload
from drama_agents.vector_store import LocalVectorStore, build_material_chunks


ALLOWED_UPLOADS = SUPPORTED_MATERIAL_EXTENSIONS
ENV_PROVIDER = object()


def create_app(
    workspace: Path | str,
    outputs: Path | str,
    refiner_provider=ENV_PROVIDER,
    timeline_provider=ENV_PROVIDER,
    script_provider=ENV_PROVIDER,
) -> Flask:
    package_dir = Path(__file__).parent
    app = Flask(
        __name__,
        template_folder=str(package_dir / "templates"),
        static_folder=str(package_dir / "static"),
    )
    workspace_path = Path(workspace).resolve()
    outputs_path = Path(outputs).resolve()
    upload_path = workspace_path / "uploads"
    output_splits_path = outputs_path / "material_splits"
    script_outputs_path = outputs_path / "script_generations"
    map_data_path = package_dir.parents[1] / "data" / "natural_earth"
    map_outputs_path = outputs_path / "maps"
    records_path = outputs_path / "material_records.json"
    database_path = outputs_path / "material_workstation.sqlite3"
    rag_database_path = outputs_path / "material_rag.sqlite3"
    database = MaterialDatabase(database_path)
    refiner = ChapterRefiner.from_environment() if refiner_provider is ENV_PROVIDER else ChapterRefiner(refiner_provider)
    timeline_builder = (
        TimelineBuilder.from_environment() if timeline_provider is ENV_PROVIDER else TimelineBuilder(timeline_provider)
    )
    script_agent = ScriptAgent.from_environment() if script_provider is ENV_PROVIDER else ScriptAgent(script_provider)
    upload_path.mkdir(parents=True, exist_ok=True)
    output_splits_path.mkdir(parents=True, exist_ok=True)
    script_outputs_path.mkdir(parents=True, exist_ok=True)
    map_outputs_path.mkdir(parents=True, exist_ok=True)

    app.config["WORKSPACE"] = workspace_path
    app.config["OUTPUTS"] = outputs_path
    app.config["UPLOADS"] = upload_path
    app.config["MATERIAL_SPLITS"] = output_splits_path
    app.config["SCRIPT_GENERATIONS"] = script_outputs_path
    app.config["MAP_DATA"] = map_data_path
    app.config["MAP_OUTPUTS"] = map_outputs_path
    app.config["MATERIAL_RECORDS"] = records_path
    app.config["MATERIAL_DATABASE"] = database_path
    app.config["RAG_DATABASE"] = rag_database_path
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    migrate_legacy_records(database, records_path, outputs_path)

    @app.route("/", methods=["GET"])
    def index():
        return render_template("index.html")

    @app.route("/materials/<record_id>", methods=["GET"])
    def material_detail(record_id: str):
        record = find_record(database_path, record_id)
        if not record:
            abort(404)
        manifest_path = outputs_path / record["output_relative_path"] / "manifest.json"
        manifest = read_json_file(manifest_path, default={})
        chapters = chapters_for_detail(record, manifest)
        return render_template("detail.html", record=record, manifest=manifest, chapters=chapters)

    @app.route("/materials/<record_id>/timeline", methods=["GET"])
    def material_timeline(record_id: str):
        record = find_record(database_path, record_id)
        if not record:
            abort(404)
        output_dir = outputs_path / record["output_relative_path"]
        timeline = read_json_file(output_dir / "timeline" / "timeline.json", default=None)
        if not timeline:
            abort(404)
        manifest = read_json_file(output_dir / "manifest.json", default={})
        chapters = chapters_for_detail(record, manifest)
        chapter_urls = {chapter["chapter_id"]: chapter["reader_url"] for chapter in chapters}
        return render_template(
            "timeline.html",
            record=record,
            timeline=timeline,
            events=timeline.get("events", []),
            chapter_urls=chapter_urls,
        )

    @app.route("/materials/<record_id>/chapters/<chapter_id>", methods=["GET"])
    def chapter_reader(record_id: str, chapter_id: str):
        record = find_record(database_path, record_id)
        if not record:
            abort(404)
        output_dir = outputs_path / record["output_relative_path"]
        manifest = read_json_file(output_dir / "manifest.json", default={})
        chapter = find_chapter(manifest, chapter_id)
        if not chapter:
            abort(404)
        chapter = chapter_with_links(chapter, outputs_path)
        reader_payload = read_json_file(output_dir / "reader" / f"{chapter_id}_reader.json", default=None)
        if not reader_payload:
            reader_payload = raw_reader_payload(chapter)
        chapters = chapters_for_detail(record, manifest)
        return render_template(
            "reader.html",
            record=record,
            chapter=chapter,
            reader=reader_payload,
            chapters=chapters,
        )

    @app.route("/favicon.ico", methods=["GET"])
    def favicon():
        return ("", 204)

    @app.route("/api/sources", methods=["GET"])
    def list_sources():
        return jsonify({"sources": discover_sources(workspace_path)})

    @app.route("/api/upload", methods=["POST"])
    def upload_source():
        file = request.files.get("file")
        if not file or not file.filename:
            return jsonify({"error": "没有收到上传文件"}), 400
        suffix = Path(file.filename).suffix.lower()
        if suffix not in ALLOWED_UPLOADS:
            return jsonify({"error": "暂不支持该材料格式"}), 400
        filename = secure_filename(file.filename) or f"source{suffix}"
        if not Path(filename).suffix:
            filename = f"{filename}{suffix}"
        destination = unique_path(upload_path / filename)
        file.save(destination)
        return jsonify(
            {
                "source": source_payload(destination, workspace_path),
                "message": "uploaded",
            }
        )

    @app.route("/api/split", methods=["POST"])
    def split_source():
        payload = request.get_json(silent=True) or {}
        relative_path = payload.get("relative_path")
        if not relative_path:
            return jsonify({"error": "缺少素材路径"}), 400
        source = resolve_workspace_path(workspace_path, relative_path)
        if not is_supported_source(source):
            return jsonify({"error": "素材不存在或格式不支持"}), 404
        result = parse_material_source(source, output_splits_path)
        return jsonify(result_payload(result, workspace_path, outputs_path))

    @app.route("/api/parse", methods=["POST"])
    def parse_source():
        payload = request.get_json(silent=True) or {}
        relative_path = payload.get("relative_path")
        if not relative_path:
            return jsonify({"error": "缺少材料路径"}), 400
        source = resolve_workspace_path(workspace_path, relative_path)
        if not is_supported_source(source):
            return jsonify({"error": "材料不存在或格式不支持"}), 404
        result = parse_material_source(source, output_splits_path)
        refinement = refiner.refine_book(result)
        result_data = result_payload(result, workspace_path, outputs_path)
        refinement_data = refinement_result_to_payload(refinement)
        attach_refinement_to_result(result_data, refinement_data, outputs_path)
        record = create_parse_record(result_data, source, workspace_path, refinement_data)
        upsert_record(database_path, record, result_data)
        rebuild_rag_for_records(database_path, outputs_path, rag_database_path, [record["record_id"]])
        save_records(records_path, load_records(database_path))
        return jsonify({"result": result_data, "record": record, "refinement": refinement_data})

    @app.route("/api/records", methods=["GET"])
    def list_records():
        return jsonify({"records": load_records(database_path)})

    @app.route("/api/script/timeline-sources", methods=["GET"])
    def list_script_timeline_sources():
        return jsonify({"timeline_sources": timeline_sources_for_script(database_path)})

    @app.route("/api/script/generate", methods=["POST"])
    def generate_script():
        payload = request.get_json(silent=True) or {}
        topic = str(payload.get("topic") or "").strip()
        time_range = str(payload.get("time_range") or "").strip()
        record_ids = payload.get("timeline_record_ids") or payload.get("record_ids") or []
        if not isinstance(record_ids, list):
            return jsonify({"error": "时间线选择格式不正确"}), 400
        try:
            time_start_year = parse_optional_year(payload.get("time_start_year"), "开始年份")
            time_end_year = parse_optional_year(payload.get("time_end_year"), "结束年份")
            result = script_agent.generate(
                topic=topic,
                time_range_text=time_range,
                time_start_year=time_start_year,
                time_end_year=time_end_year,
                record_ids=[str(record_id) for record_id in record_ids],
                database=MaterialDatabase(database_path),
                output_dir=script_outputs_path,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({"result": result})

    @app.route("/api/script/generations", methods=["GET"])
    def list_script_generations():
        return jsonify({"generations": MaterialDatabase(database_path).list_script_generations()})

    @app.route("/api/script/generations/<generation_id>", methods=["GET"])
    def get_script_generation(generation_id: str):
        generation = MaterialDatabase(database_path).find_script_generation(generation_id)
        if not generation:
            abort(404)
        return jsonify({"generation": generation})

    @app.route("/api/script/generations/<generation_id>/article", methods=["PATCH"])
    def update_script_article(generation_id: str):
        payload = request.get_json(silent=True) or {}
        article = str(payload.get("article") or "").strip()
        if not article:
            return jsonify({"error": "剧本正文不能为空"}), 400
        database = MaterialDatabase(database_path)
        try:
            generation = database.update_script_article(generation_id, article)
        except KeyError:
            abort(404)
        sync_script_generation_files(generation)
        return jsonify({"generation": generation})

    @app.route("/api/script/generations/<generation_id>/rag/build", methods=["POST"])
    def build_script_rag(generation_id: str):
        database = MaterialDatabase(database_path)
        generation = database.find_script_generation(generation_id)
        if not generation:
            abort(404)
        record_ids = [str(record_id) for record_id in generation.get("selected_record_ids") or []]
        chunks = build_material_chunks(database, outputs_path, record_ids)
        count = LocalVectorStore(rag_database_path).replace_record_chunks(record_ids, chunks)
        return jsonify({"generation_id": generation_id, "chunk_count": count})

    @app.route("/api/script/generations/<generation_id>/assistant/conversations", methods=["GET"])
    def list_script_assistant_conversations(generation_id: str):
        database = MaterialDatabase(database_path)
        if not database.find_script_generation(generation_id):
            abort(404)
        return jsonify({"conversations": database.list_script_assistant_conversations(generation_id)})

    @app.route("/api/script/generations/<generation_id>/assistant/conversations", methods=["POST"])
    def create_script_assistant_conversation(generation_id: str):
        payload = request.get_json(silent=True) or {}
        database = MaterialDatabase(database_path)
        if not database.find_script_generation(generation_id):
            abort(404)
        conversation = database.create_script_assistant_conversation(
            generation_id,
            title=str(payload.get("title") or ""),
        )
        return jsonify({"conversation": conversation})

    @app.route("/api/script/generations/<generation_id>/assistant/conversations/<conversation_id>", methods=["GET"])
    def get_script_assistant_conversation(generation_id: str, conversation_id: str):
        database = MaterialDatabase(database_path)
        if not database.find_script_generation(generation_id):
            abort(404)
        conversation = database.find_script_assistant_conversation(generation_id, conversation_id)
        if not conversation:
            abort(404)
        messages = database.list_script_assistant_messages(
            generation_id,
            conversation_id=conversation_id,
            limit=None,
        )
        return jsonify(
            {
                "conversation": conversation,
                "messages": [assistant_history_message(message) for message in messages],
            }
        )

    @app.route("/api/script/generations/<generation_id>/assistant/conversations/<conversation_id>", methods=["PATCH"])
    def update_script_assistant_conversation(generation_id: str, conversation_id: str):
        payload = request.get_json(silent=True) or {}
        title = str(payload.get("title") or "").strip()
        if not title:
            return jsonify({"error": "标题不能为空"}), 400
        database = MaterialDatabase(database_path)
        if not database.find_script_generation(generation_id):
            abort(404)
        try:
            conversation = database.update_script_assistant_conversation_title(generation_id, conversation_id, title)
        except KeyError:
            abort(404)
        return jsonify({"conversation": conversation})

    @app.route("/api/script/generations/<generation_id>/assistant/conversations/<conversation_id>", methods=["DELETE"])
    def delete_script_assistant_conversation(generation_id: str, conversation_id: str):
        database = MaterialDatabase(database_path)
        if not database.find_script_generation(generation_id):
            abort(404)
        try:
            conversation = database.archive_script_assistant_conversation(generation_id, conversation_id)
        except KeyError:
            abort(404)
        return jsonify({"conversation": conversation, "deleted": conversation_id})

    @app.route("/api/script/generations/<generation_id>/assist", methods=["POST"])
    def assist_script_edit(generation_id: str):
        payload = request.get_json(silent=True) or {}
        database = MaterialDatabase(database_path)
        if not database.find_script_generation(generation_id):
            abort(404)
        controller = ScriptAssistantController(
            database=database,
            script_agent=script_agent,
            rag_database_path=rag_database_path,
            outputs_path=outputs_path,
        )
        try:
            response_payload, status = controller.handle(generation_id, payload)
        except KeyError:
            abort(404)
        generation = response_payload.get("generation")
        if generation:
            sync_script_generation_files(generation)
        return jsonify(response_payload), status

    @app.route("/api/script/generations/<generation_id>/assistant/selection-history", methods=["POST"])
    def script_assistant_selection_history(generation_id: str):
        payload = request.get_json(silent=True) or {}
        selection = str(payload.get("selection") or "")
        limit = int(payload.get("limit") or 20)
        database = MaterialDatabase(database_path)
        generation = database.find_script_generation(generation_id)
        if not generation:
            abort(404)
        messages = database.list_script_assistant_messages_for_selection(generation_id, selection, limit=limit)
        return jsonify(
            {
                "generation_id": generation_id,
                "selection": selection,
                "match_count": len(messages),
                "messages": [assistant_history_message(message) for message in messages],
            }
        )

    @app.route("/api/script/generations/<generation_id>/assist/history", methods=["POST"])
    def script_assistant_history(generation_id: str):
        payload = request.get_json(silent=True) or {}
        selection = str(payload.get("selection") or "")
        database = MaterialDatabase(database_path)
        generation = database.find_script_generation(generation_id)
        if not generation:
            abort(404)
        messages = database.list_script_assistant_messages_for_selection(generation_id, selection, limit=20)
        return jsonify(
            {
                "generation_id": generation_id,
                "selection": selection,
                "match_count": len(messages),
                "messages": [assistant_history_message(message) for message in messages],
            }
        )

    @app.route("/script-generations/<generation_id>/<section>", methods=["GET"])
    def script_generation_view(generation_id: str, section: str):
        if section not in {"script", "subjects", "maps"}:
            abort(404)
        generation = MaterialDatabase(database_path).find_script_generation(generation_id)
        if not generation:
            abort(404)
        section_titles = {
            "script": "剧本阅读器",
            "subjects": "主体查看",
            "maps": "地点画面",
        }
        return render_template(
            "script_generation_view.html",
            generation=generation,
            section=section,
            section_title=section_titles[section],
        )

    @app.route("/api/script/generations/<generation_id>", methods=["DELETE"])
    def delete_script_generation(generation_id: str):
        generations = MaterialDatabase(database_path).delete_script_generation(generation_id)
        return jsonify({"deleted": generation_id, "generations": generations})

    @app.route("/api/maps/regions", methods=["GET"])
    def list_map_regions():
        return jsonify({"regions": REGIONS})

    @app.route("/api/maps/render", methods=["GET"])
    def render_workstation_map():
        region = str(request.args.get("region") or "world")
        bbox_value = request.args.get("bbox")
        bbox = parse_bbox(bbox_value) if bbox_value else None
        title = str(request.args.get("title") or region_label(region))
        width = clamp_int(request.args.get("width"), default=1280, minimum=320, maximum=3000)
        height = clamp_int(request.args.get("height"), default=800, minimum=240, maximum=2200)
        show_cities = parse_bool(request.args.get("cities"), default=False)
        show_rivers = parse_bool(request.args.get("rivers"), default=True)
        show_lakes = parse_bool(request.args.get("lakes"), default=True)
        ensure_default_data(map_data_path)
        with tempfile.NamedTemporaryFile(prefix=f"map_{slugify(region) or 'world'}_", suffix=".png", dir=map_outputs_path, delete=False) as tmp:
            rendered = render_map(
                data_dir=map_data_path,
                output_path=Path(tmp.name),
                region=region,
                bbox=bbox,
                title=title,
                width=width,
                height=height,
                show_cities=show_cities,
                show_rivers=show_rivers,
                show_lakes=show_lakes,
            )
        return send_file(rendered, mimetype="image/png", as_attachment=False)

    @app.route("/api/materials/<record_id>/timeline", methods=["POST"])
    def build_material_timeline(record_id: str):
        payload = request.get_json(silent=True) or {}
        record = find_record(database_path, record_id)
        if not record:
            abort(404)
        output_dir = outputs_path / record["output_relative_path"]
        manifest = read_json_file(output_dir / "manifest.json", default={})
        if not manifest.get("chapters"):
            return jsonify({"error": "没有可用于生成时间线的章节"}), 400
        source = resolve_workspace_path(workspace_path, record["source_relative_path"])
        split_input = {
            "book_id": record_id,
            "source_file": str(source),
            "output_dir": str(output_dir),
            "chapters": manifest.get("chapters", []),
        }
        timeline = timeline_builder.build_book(split_input, force=bool(payload.get("force")))
        timeline_data = timeline_result_to_payload(timeline)
        record = update_record_timeline(database_path, record_id, timeline_data, outputs_path)
        rebuild_rag_for_records(database_path, outputs_path, rag_database_path, [record_id])
        save_records(records_path, load_records(database_path))
        return jsonify({"timeline": timeline_data, "record": record})

    @app.route("/api/records/<record_id>", methods=["DELETE"])
    def delete_record(record_id: str):
        records = MaterialDatabase(database_path).delete_record(record_id)
        save_records(records_path, records)
        return jsonify({"deleted": record_id, "records": records})

    @app.route("/api/records/<record_id>", methods=["PATCH"])
    def update_record(record_id: str):
        payload = request.get_json(silent=True) or {}
        book_name = str(payload.get("book_name") or "").strip()
        if not book_name:
            return jsonify({"error": "书名不能为空"}), 400
        try:
            record = MaterialDatabase(database_path).update_book_name(record_id, book_name)
        except KeyError:
            abort(404)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        records = load_records(database_path)
        save_records(records_path, records)
        return jsonify({"record": record, "records": records})

    @app.route("/outputs/<path:filename>", methods=["GET"])
    def output_file(filename: str):
        safe_path = resolve_workspace_path(outputs_path, filename)
        if not safe_path.exists() or not safe_path.is_file():
            abort(404)
        return send_from_directory(outputs_path, filename, as_attachment=False)

    return app


def parse_material_source(source: Path, output_splits_path: Path):
    book_id = slugify(source.stem) or "book"
    output_dir = output_splits_path / book_id
    if output_dir.exists():
        shutil.rmtree(output_dir)
    return MaterialSplitter(output_root=output_dir).split_material(source)


def sync_script_generation_files(generation: dict) -> None:
    json_path = Path(generation.get("json_path") or "")
    markdown_path = Path(generation.get("markdown_path") or "")
    if json_path:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(generation, ensure_ascii=False, indent=2), encoding="utf-8")
    if markdown_path:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(render_script_markdown(generation), encoding="utf-8")


def is_edit_confirmation(text: str) -> bool:
    compact = "".join(str(text or "").split())
    if not compact:
        return False
    negative_markers = ("不可以", "别改", "不要改", "先不要", "不对", "不是")
    if any(marker in compact for marker in negative_markers):
        return False
    confirmation_markers = (
        "可以",
        "按这个改",
        "按你说的改",
        "就这么改",
        "确认修改",
        "应用修改",
        "你说得对",
        "你说的对",
        "同意",
        "开始为我修改",
        "开始修改",
        "开始真正修改",
        "请直接修改",
        "直接修改",
        "保存这个修改",
    )
    return any(marker in compact for marker in confirmation_markers)


def assistant_history_message(message: dict) -> dict:
    result = message.get("result") if isinstance(message.get("result"), dict) else {}
    return {
        "message_id": message.get("message_id"),
        "conversation_id": message.get("conversation_id", ""),
        "role": message.get("role", ""),
        "content": message.get("content", ""),
        "intent": message.get("intent", ""),
        "focus_action": message.get("focus_action", ""),
        "selection": message.get("selection", ""),
        "selection_text": message.get("selection", ""),
        "reference_selection": message.get("reference_selection", ""),
        "reference_selection_text": message.get("reference_selection", ""),
        "selection_hash": message.get("selection_hash", ""),
        "reference_selection_hash": message.get("reference_selection_hash", ""),
        "paragraph_id": message.get("paragraph_id", ""),
        "start_offset": message.get("start_offset"),
        "end_offset": message.get("end_offset"),
        "replacement": result.get("replacement", ""),
        "patch_id": message.get("patch_id") or result.get("patch_id") or result.get("pending_edit_id"),
        "applied": bool(result.get("applied")) if result else False,
        "rejected": bool(result.get("rejected")) if result else False,
        "used_context_ids": result.get("used_context_ids", []) if result else [],
        "created_at": message.get("created_at", ""),
    }


def carried_selection_for_followup(
    *,
    instruction: str,
    pending: dict | None,
    messages: list[dict],
) -> str:
    if not should_continue_previous_selection(instruction):
        return ""
    if pending and pending.get("selection"):
        return str(pending["selection"])
    for message in reversed(messages):
        selection = str(message.get("selection") or "").strip()
        if selection:
            return selection
    return ""


def should_continue_previous_selection(text: str) -> bool:
    compact = "".join(str(text or "").split())
    if not compact:
        return False
    standalone_chat = {"你好", "您好", "谢谢", "多谢", "可以聊聊吗"}
    if compact in standalone_chat:
        return False
    markers = (
        "这里",
        "这段",
        "刚才",
        "上面",
        "上一版",
        "前面",
        "原文",
        "开头",
        "疑问",
        "改",
        "修改",
        "重写",
        "润色",
        "补充",
        "调整",
        "不是",
        "不，",
        "不对",
    )
    return any(marker in compact for marker in markers)


def rebuild_rag_for_records(database_path: Path, outputs_path: Path, rag_database_path: Path, record_ids: list[str]) -> int:
    database = MaterialDatabase(database_path)
    chunks = build_material_chunks(database, outputs_path, record_ids)
    return LocalVectorStore(rag_database_path).replace_record_chunks(record_ids, chunks)


def parse_pdf_source(source: Path, output_splits_path: Path):
    return parse_material_source(source, output_splits_path)


def discover_sources(workspace: Path) -> list[dict]:
    search_roots = [workspace / "资料库", workspace / "uploads"]
    sources: list[dict] = []
    for root in search_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                if path.suffix.lower() in SUPPORTED_MATERIAL_EXTENSIONS:
                    sources.append(source_payload(path, workspace))
    sources.sort(key=lambda item: item["relative_path"])
    return sources


def source_payload(path: Path, workspace: Path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "relative_path": path.relative_to(workspace).as_posix(),
        "size_mb": round(stat.st_size / 1024 / 1024, 2),
        "format": path.suffix.lower().lstrip("."),
    }


def parse_optional_year(value, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label}必须是整数")
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是整数") from exc


def result_payload(result, workspace: Path, outputs: Path) -> dict:
    payload = asdict(result)
    payload["source_relative_path"] = relative_or_absolute(Path(result.source_file), workspace)
    payload["output_relative_path"] = relative_or_absolute(Path(result.output_dir), outputs)
    payload["links"] = {
        "manifest": output_link(Path(result.output_dir) / "manifest.json", outputs),
        "chapter_review": output_link(Path(result.output_dir) / "chapter_review.md", outputs),
        "qa_report": output_link(Path(result.output_dir) / "qa_report.md", outputs),
    }
    for chapter in payload["chapters"]:
        chapter["pdf_link"] = output_link(Path(chapter["pdf_path"]), outputs) if chapter.get("pdf_path") else ""
        chapter["text_link"] = output_link(Path(chapter["text_path"]), outputs)
    return payload


def is_supported_source(source: Path) -> bool:
    return source.exists() and source.is_file() and source.suffix.lower() in SUPPORTED_MATERIAL_EXTENSIONS


def attach_refinement_to_result(result_data: dict, refinement_data: dict, outputs: Path) -> None:
    refined_by_id = {chapter["chapter_id"]: chapter for chapter in refinement_data.get("chapters", [])}
    for chapter in result_data.get("chapters", []):
        refined = refined_by_id.get(chapter["chapter_id"])
        if not refined:
            continue
        chapter["reader_status"] = refined.get("status")
        chapter["reader_message"] = refined.get("message")
        chapter["reader_json_link"] = output_link(Path(refined["reader_json_path"]), outputs)
        chapter["reader_markdown_link"] = output_link(Path(refined["reader_markdown_path"]), outputs)
        chapter["reader_html_link"] = output_link(Path(refined["reader_html_path"]), outputs)


def create_parse_record(result_data: dict, source: Path, workspace: Path, refinement_data: dict | None = None) -> dict:
    record_id = result_data["book_id"]
    chapters = result_data.get("chapters", [])
    total_words = sum(chapter.get("word_count", 0) for chapter in chapters)
    refinement_data = refinement_data or {}
    return {
        "record_id": record_id,
        "parsed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "book_name": source.name,
        "source_relative_path": source.relative_to(workspace).as_posix(),
        "page_count": result_data.get("page_count", 0),
        "total_words": total_words,
        "chapter_count": len(chapters),
        "excluded_count": len(result_data.get("excluded_sections", [])),
        "refinement_status": refinement_data.get("status", "not_run"),
        "refinement_message": refinement_data.get("message", ""),
        "refined_chapter_count": refinement_data.get("refined_count", 0),
        "timeline_status": "not_run",
        "timeline_message": "",
        "timeline_event_count": 0,
        "timeline_url": f"/materials/{record_id}/timeline",
        "output_relative_path": result_data.get("output_relative_path", ""),
        "detail_url": f"/materials/{record_id}",
        "links": result_data.get("links", {}),
    }


def load_records(database_path: Path) -> list[dict]:
    return MaterialDatabase(database_path).list_records()


def timeline_sources_for_script(database_path: Path) -> list[dict]:
    sources = []
    for record in load_records(database_path):
        if record.get("timeline_status") != "completed" or not record.get("timeline_event_count"):
            continue
        sources.append(
            {
                "record_id": record["record_id"],
                "book_name": record.get("book_name", record["record_id"]),
                "timeline_event_count": record.get("timeline_event_count", 0),
                "timeline_url": record.get("timeline_url", f"/materials/{record['record_id']}/timeline"),
            }
        )
    return sources


def save_records(records_path: Path, records: list[dict]) -> None:
    records_path.parent.mkdir(parents=True, exist_ok=True)
    records_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_record(database_path: Path, record: dict, result_data: dict) -> None:
    MaterialDatabase(database_path).upsert_parse(record, result_data)


def update_record_timeline(database_path: Path, record_id: str, timeline_data: dict, outputs: Path) -> dict:
    links = {}
    if timeline_data.get("timeline_json_path"):
        links["timeline_json"] = output_link(Path(timeline_data["timeline_json_path"]), outputs)
    if timeline_data.get("timeline_markdown_path"):
        links["timeline_markdown"] = output_link(Path(timeline_data["timeline_markdown_path"]), outputs)
    try:
        return MaterialDatabase(database_path).update_timeline(record_id, timeline_data, links=links)
    except KeyError:
        abort(404)


def find_record(database_path: Path, record_id: str) -> dict | None:
    return MaterialDatabase(database_path).find_record(record_id)


def migrate_legacy_records(database: MaterialDatabase, records_path: Path, outputs_path: Path) -> None:
    legacy_records = read_json_file(records_path, default=[])
    if not isinstance(legacy_records, list):
        return
    for record in legacy_records:
        if not isinstance(record, dict) or not record.get("record_id"):
            continue
        output_dir = outputs_path / record.get("output_relative_path", "")
        manifest = read_json_file(output_dir / "manifest.json", default={})
        result_data = {
            "chapters": manifest.get("chapters", []) if isinstance(manifest, dict) else [],
            "excluded_sections": manifest.get("excluded_sections", []) if isinstance(manifest, dict) else [],
            "warnings": manifest.get("warnings", []) if isinstance(manifest, dict) else [],
        }
        database.upsert_parse(record, result_data)
        timeline_path = output_dir / "timeline" / "timeline.json"
        if timeline_path.exists():
            database.update_timeline(
                record["record_id"],
                {
                    "status": record.get("timeline_status", "completed"),
                    "message": record.get("timeline_message", ""),
                    "event_count": record.get("timeline_event_count", 0),
                    "timeline_json_path": str(timeline_path),
                    "timeline_markdown_path": str(output_dir / "timeline" / "timeline.md"),
                },
                links={key: value for key, value in (record.get("links") or {}).items() if key.startswith("timeline")},
            )


def read_json_file(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def chapters_for_detail(record: dict, manifest: dict) -> list[dict]:
    output_path = record.get("output_relative_path", "")
    chapters = []
    for chapter in manifest.get("chapters", []):
        chapter_id = chapter.get("chapter_id")
        chapter_view = dict(chapter)
        chapter_view["reader_url"] = f"/materials/{record['record_id']}/chapters/{chapter_id}"
        chapter_view["reader_available"] = bool(output_path and chapter_id)
        chapters.append(chapter_view)
    return chapters


def chapter_with_links(chapter: dict, outputs: Path) -> dict:
    chapter_view = dict(chapter)
    chapter_view["pdf_link"] = output_link(Path(chapter["pdf_path"]), outputs) if chapter.get("pdf_path") else ""
    chapter_view["text_link"] = output_link(Path(chapter["text_path"]), outputs) if chapter.get("text_path") else ""
    return chapter_view


def find_chapter(manifest: dict, chapter_id: str) -> dict | None:
    for chapter in manifest.get("chapters", []):
        if chapter.get("chapter_id") == chapter_id:
            return chapter
    return None


def raw_reader_payload(chapter: dict) -> dict:
    return {
        "chapter_id": chapter["chapter_id"],
        "title": chapter["title"],
        "subtitle": "原始章节内容",
        "summary": "本章尚未完成 DeepSeek 精提取，当前展示原始章节文件入口。",
        "page_range": [chapter["start_page"], chapter["end_page"]],
        "sections": [
            {
                "heading": "待精提取",
                "body": "请先重新解析并确保 DEEPSEEK_API_KEY 可用。",
                "page_refs": [chapter["start_page"], chapter["end_page"]],
            }
        ],
        "visual_assets": [],
        "key_concepts": [],
        "drama_tags": [],
        "status": "raw",
    }


def output_link(path: Path, outputs: Path) -> str:
    return f"/outputs/{path.relative_to(outputs).as_posix()}"


def relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def resolve_workspace_path(root: Path, relative_path: str) -> Path:
    target = (root / relative_path).resolve()
    if root != target and root not in target.parents:
        raise ValueError("路径越界")
    return target


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("无法生成唯一文件名")
