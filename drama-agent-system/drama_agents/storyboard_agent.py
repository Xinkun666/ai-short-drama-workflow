from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any
from uuid import uuid4

from drama_agents.chapter_refiner import parse_json_object


DEFAULT_STYLE_POLICY = {
    "style_name": "历史科普卡通短剧风格",
    "visual_style": "半扁平漫画插画，不低幼，适合历史科普短剧。",
    "era_guardrail": "远古史和古史镜头避免现代建筑、现代服装、现代载具和科幻界面。",
    "fact_guardrail": "画面可以补充解释，但不能把推测写成确定史实。",
}
DEFAULT_NEGATIVE_PROMPT = "不要现代建筑、现代衣服、金属盔甲、科幻界面、写实恐怖、奇幻怪物化、低幼卡通。"
DEFAULT_FRAME_SIZE_PROMPT = "画幅和尺寸：16:9 横版，固定 1280 * 720，720p；不要 1080p、2K、4K、超高分辨率。"
PROP_MARKERS = ["火", "贝壳", "贝壳珠子", "赭石", "赭石板", "船", "木筏", "弓箭", "骨针", "CPU", "汽车油箱", "股票市场"]


class StoryboardAgent:
    def __init__(self, provider=None):
        self.provider = provider or RuleBasedStoryboardProvider()

    @classmethod
    def from_environment(cls):
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            return cls(provider=RuleBasedStoryboardProvider())
        return cls(provider=DeepSeekStoryboardProvider(api_key=api_key))

    def generate(
        self,
        *,
        generation: dict[str, Any],
        subjects: list[dict[str, Any]] | None = None,
        scenes: list[dict[str, Any]] | None = None,
        target_duration_sec: int | None = None,
        source_type: str = "script_generation",
        source_filename: str = "",
    ) -> dict[str, Any]:
        payload = storyboard_generation_payload(
            generation=generation,
            subjects=subjects or [],
            scenes=scenes or [],
            target_duration_sec=target_duration_sec,
        )
        try:
            raw = self.provider.generate_storyboard(payload)
        except json.JSONDecodeError as exc:
            raw = recover_storyboard_from_provider_error(self.provider, payload, exc)
        storyboard = normalize_storyboard_payload(
            raw,
            generation=generation,
            subjects=subjects or [],
            scenes=scenes or [],
            target_duration_sec=payload.get("target_duration_sec"),
            source_type=source_type,
            source_filename=source_filename,
        )
        quality_issues = storyboard_quality_issues(storyboard, generation=generation)
        if quality_issues and not isinstance(self.provider, RuleBasedStoryboardProvider):
            raw = recover_storyboard_from_quality_issue(self.provider, payload, quality_issues)
            storyboard = normalize_storyboard_payload(
                raw,
                generation=generation,
                subjects=subjects or [],
                scenes=scenes or [],
                target_duration_sec=payload.get("target_duration_sec"),
                source_type=source_type,
                source_filename=source_filename,
            )
            storyboard["status"] = "needs_review"
            storyboard["review_notes"] = merge_review_notes(
                [f"DeepSeek 分镜过度压缩，已使用兜底分镜。{issue}" for issue in quality_issues],
                storyboard["review_notes"],
            )
        return storyboard


class RuleBasedStoryboardProvider:
    def generate_storyboard(self, payload: dict[str, Any]) -> dict[str, Any]:
        article = str(payload.get("full_script") or "")
        units = split_article_into_narration_units_with_source(article)
        subjects = payload.get("provided_subjects") or []
        scenes = payload.get("provided_scenes") or []
        subject_lookup = {subject.get("subject_id"): subject for subject in subjects if subject.get("subject_id")}
        scene_lookup = {scene.get("scene_id"): scene for scene in scenes if scene.get("scene_id")}
        target_duration = safe_int(payload.get("target_duration_sec"), default=0)
        shots: list[dict[str, Any]] = []
        missing_subjects: list[dict[str, str]] = []
        missing_scenes: list[dict[str, str]] = []

        shot_index = 1
        for unit in units:
            narration = unit["text"]
            shot_type = infer_shot_type(narration, index=shot_index)
            subject_ids, subject_names = match_subjects(narration, subjects)
            scene_id, scene_name = match_scene(narration, scenes, shot_type=shot_type)
            visual_elements = visual_elements_for_text(narration)
            if mentions_known_subject(narration) and not subject_ids:
                add_missing_candidate(missing_subjects, "智人/古人类主体", "剧本提到人群，但当前主体池没有可绑定主体。")
            if needs_scene_binding(narration, shot_type) and not scene_id:
                add_missing_candidate(missing_scenes, suggested_scene_name(narration), "剧本段落需要稳定环境空间，但当前场景池没有可绑定场景。")
            reference_assets = build_reference_assets(
                subject_ids=subject_ids,
                scene_id=scene_id,
                subject_lookup=subject_lookup,
                scene_lookup=scene_lookup,
            )
            duration_sec = duration_for_shot_type(shot_type, narration)
            camera = camera_for_shot_type(shot_type)
            needs_review = needs_manual_review(narration, scene_id=scene_id, subject_ids=subject_ids, shot_type=shot_type)
            shot = {
                "shot_index": shot_index,
                "narration": narration,
                "subtitle_text": narration,
                "shot_type": shot_type,
                "visual_goal": visual_goal_for_shot(narration, shot_type),
                "scene_id": scene_id,
                "scene_name": scene_name,
                "subject_ids": subject_ids,
                "subject_names": subject_names,
                "visual_elements": visual_elements,
                "reference_assets": reference_assets,
                "camera": camera,
                "duration_sec": duration_sec,
                "keyframe_prompt": "",
                "video_prompt": "",
                "negative_prompt": DEFAULT_NEGATIVE_PROMPT,
                "fact_safety_note": fact_safety_note_for_text(narration),
                "needs_manual_review": needs_review,
                "asset_status": asset_status_for_reference_assets(reference_assets),
                "source_paragraph_index": unit["paragraph_index"],
                "source_text_start": unit["start"],
                "source_text_end": unit["end"],
                "source_excerpt": unit["text"],
                "is_supplemental": False,
                "supplemental_reason": "",
            }
            shot["keyframe_prompt"] = compose_keyframe_prompt(shot, style_policy=payload.get("visual_style_policy") or {})
            shot["video_prompt"] = compose_video_prompt(shot)
            shots.append(shot)
            shot_index += 1
            if should_add_supplemental_shot(shot_type, narration):
                supplemental = dict(shot)
                supplemental.update(
                    {
                        "shot_index": shot_index,
                        "narration": supplemental_narration_for_shot(shot_type, narration),
                        "subtitle_text": "",
                        "shot_type": supplemental_shot_type(shot_type),
                        "visual_goal": supplemental_visual_goal(shot_type, narration),
                        "duration_sec": supplemental_duration(shot_type),
                        "source_paragraph_index": 0,
                        "source_text_start": 0,
                        "source_text_end": 0,
                        "source_excerpt": "",
                        "is_supplemental": True,
                        "supplemental_reason": supplemental_reason_for_shot(shot_type, narration),
                    }
                )
                supplemental["keyframe_prompt"] = compose_keyframe_prompt(
                    supplemental,
                    style_policy=payload.get("visual_style_policy") or {},
                )
                supplemental["video_prompt"] = compose_video_prompt(supplemental)
                shots.append(supplemental)
                shot_index += 1

        actual_duration = sum(float(shot["duration_sec"]) for shot in shots)
        resolved_target_duration = target_duration if target_duration > 0 else round(actual_duration)
        review_notes = []
        if target_duration > 0 and actual_duration > target_duration:
            review_notes.append("原剧本较长，为保证完整性，预计时长超过目标时长。")
        if any(shot["needs_manual_review"] for shot in shots):
            review_notes.append("部分镜头包含现代比喻、抽象概念或缺少参考资产，需要人工检查。")
        return {
            "storyboard": {
                "title": f"{payload.get('script_title') or payload.get('topic') or '剧本'} 分镜",
                "target_duration_sec": resolved_target_duration,
                "actual_duration_sec": actual_duration,
                "style_policy": payload.get("visual_style_policy") or DEFAULT_STYLE_POLICY,
                "missing_subject_candidates": missing_subjects,
                "missing_scene_candidates": missing_scenes,
                "review_notes": review_notes,
                "shots": shots,
            }
        }


def should_generate_storyboard_in_chunks(payload: dict[str, Any]) -> bool:
    article = str(payload.get("full_script") or "")
    min_chars = safe_int(os.environ.get("STORYBOARD_CHUNK_FIRST_MIN_CHARS"), default=1800)
    min_paragraphs = safe_int(os.environ.get("STORYBOARD_CHUNK_FIRST_MIN_PARAGRAPHS"), default=8)
    if min_chars > 0 and len(article) >= min_chars:
        return True
    if min_paragraphs > 0 and len(split_article_into_paragraph_spans(article)) >= min_paragraphs:
        return True
    return False


class DeepSeekStoryboardProvider:
    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        base_url: str = "https://api.deepseek.com/chat/completions",
        timeout: int | None = None,
    ):
        self.api_key = api_key
        self.model = model or os.environ.get("DEEPSEEK_STORYBOARD_MODEL") or "deepseek-v4-pro"
        self.base_url = base_url
        self.timeout = timeout or int(os.environ.get("DEEPSEEK_TIMEOUT", "240"))

    def generate_storyboard(self, payload: dict[str, Any]) -> dict[str, Any]:
        if should_generate_storyboard_in_chunks(payload):
            return self.generate_storyboard_chunks(payload)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是 AI 历史科普短剧分镜导演。你不是剧本摘要员，也不是剧本改写员。"
                    "你只输出合法 JSON，不要 Markdown 代码围栏。"
                ),
            },
            {"role": "user", "content": build_storyboard_prompt(payload)},
        ]
        content = self._post_chat(messages, max_tokens=int(os.environ.get("STORYBOARD_MAX_TOKENS", "14000")))
        try:
            return parse_json_object(content)
        except json.JSONDecodeError:
            try:
                return self.repair_storyboard_json(content)
            except json.JSONDecodeError:
                return self.generate_storyboard_chunks(payload)

    def _post_chat(self, messages: list[dict[str, str]], *, max_tokens: int) -> str:
        body = {
            "model": getattr(self, "model", None) or os.environ.get("DEEPSEEK_STORYBOARD_MODEL") or "deepseek-v4-pro",
            "messages": messages,
            "temperature": 0.18,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            getattr(self, "base_url", "https://api.deepseek.com/chat/completions"),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {getattr(self, 'api_key', '')}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=getattr(self, "timeout", 240)) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"DeepSeek HTTP {exc.code}: {detail[:300]}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"DeepSeek 连接失败：{exc}") from exc
        return str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")

    def repair_storyboard_json(self, content: str) -> dict[str, Any]:
        repaired = self._post_chat(
            [
                {
                    "role": "system",
                    "content": "你是 JSON 修复器。只输出合法 JSON object，不要解释，不要 Markdown。",
                },
                {
                    "role": "user",
                    "content": (
                        "请修复下面这个分镜 JSON，使它成为合法 JSON object。"
                        "保留已有字段，不要新增解释文本。\n\n"
                        f"{preview_text(content, limit=12000)}"
                    ),
                },
            ],
            max_tokens=int(os.environ.get("STORYBOARD_REPAIR_MAX_TOKENS", "14000")),
        )
        return parse_json_object(repaired)

    def generate_storyboard_chunks(self, payload: dict[str, Any]) -> dict[str, Any]:
        paragraphs = split_article_into_paragraph_spans(str(payload.get("full_script") or ""))
        if not paragraphs:
            raise json.JSONDecodeError("empty storyboard chunks", "", 0)
        merged_shots: list[dict[str, Any]] = []
        title = f"{payload.get('script_title') or payload.get('topic') or '剧本'} 分镜"
        for paragraph in paragraphs:
            chunk_payload = dict(payload)
            chunk_payload["full_script"] = paragraph["text"]
            chunk_payload["chunk_source_paragraph_index"] = paragraph["paragraph_index"]
            content = self._post_chat(
                [
                    {
                        "role": "system",
                        "content": "你是 AI 历史科普短剧分镜导演。只输出合法 JSON object。",
                    },
                    {"role": "user", "content": build_storyboard_prompt(chunk_payload)},
                ],
                max_tokens=int(os.environ.get("STORYBOARD_CHUNK_MAX_TOKENS", "5000")),
            )
            chunk = parse_json_object(content)
            board = chunk.get("storyboard") if isinstance(chunk.get("storyboard"), dict) else chunk
            for shot in board.get("shots") or []:
                if isinstance(shot, dict):
                    shot["source_paragraph_index"] = paragraph["paragraph_index"]
                    merged_shots.append(shot)
        if not merged_shots:
            raise json.JSONDecodeError("empty storyboard chunks", "", 0)
        return {"storyboard": {"title": title, "shots": merged_shots, "fallback": {"stage": "chunked_deepseek"}}}


def recover_storyboard_from_provider_error(provider: Any, payload: dict[str, Any], exc: json.JSONDecodeError) -> dict[str, Any]:
    error_summary = preview_text(str(exc), limit=160)
    repair = getattr(provider, "repair_storyboard_json", None)
    if callable(repair):
        try:
            raw = repair(getattr(exc, "doc", "") or "")
            annotate_fallback(raw, stage="repair", error_summary=error_summary)
            return raw
        except json.JSONDecodeError as repair_exc:
            error_summary = f"{error_summary}; repair: {preview_text(str(repair_exc), limit=120)}"
    chunked = getattr(provider, "generate_storyboard_chunks", None)
    if callable(chunked):
        try:
            raw = chunked(payload)
            annotate_fallback(raw, stage="chunked", error_summary=error_summary)
            return raw
        except json.JSONDecodeError as chunk_exc:
            error_summary = f"{error_summary}; chunked: {preview_text(str(chunk_exc), limit=120)}"
    return fallback_storyboard_for_provider_output_error(payload, exc, error_summary=error_summary)


def annotate_fallback(raw: dict[str, Any], *, stage: str, error_summary: str) -> None:
    board = raw.get("storyboard") if isinstance(raw.get("storyboard"), dict) else raw
    if not isinstance(board, dict):
        return
    board["fallback"] = {"stage": stage, "error_summary": error_summary}


def fallback_storyboard_for_provider_output_error(
    payload: dict[str, Any],
    exc: Exception,
    *,
    error_summary: str | None = None,
) -> dict[str, Any]:
    raw = RuleBasedStoryboardProvider().generate_storyboard(payload)
    board = raw.get("storyboard") if isinstance(raw.get("storyboard"), dict) else {}
    notes = board.get("review_notes") if isinstance(board.get("review_notes"), list) else []
    notes.append(f"DeepSeek JSON 无法解析，已使用本地规则分镜兜底。")
    board["review_notes"] = notes
    board["status"] = "needs_review"
    board["fallback"] = {"stage": "rule_based", "error_summary": error_summary or preview_text(str(exc), limit=160)}
    raw["storyboard"] = board
    return raw


def recover_storyboard_from_quality_issue(provider: Any, payload: dict[str, Any], quality_issues: list[str]) -> dict[str, Any]:
    chunked = getattr(provider, "generate_storyboard_chunks", None)
    if callable(chunked):
        try:
            raw = chunked(payload)
            board = raw.get("storyboard") if isinstance(raw.get("storyboard"), dict) else raw
            if isinstance(board, dict):
                notes = [str(item) for item in board.get("review_notes") or [] if str(item).strip()]
                notes.append("DeepSeek 分镜过度压缩，已改用 DeepSeek 分段生成。")
                board["review_notes"] = notes
                board["status"] = "needs_review"
                board["fallback"] = {"stage": "quality_chunked", "quality_issues": quality_issues}
            return raw
        except json.JSONDecodeError:
            pass
    raw = RuleBasedStoryboardProvider().generate_storyboard(payload)
    board = raw.get("storyboard") if isinstance(raw.get("storyboard"), dict) else {}
    notes = [str(item) for item in board.get("review_notes") or [] if str(item).strip()]
    notes.append("DeepSeek 分镜过度压缩，已使用本地规则分镜兜底。")
    board["review_notes"] = notes
    board["status"] = "needs_review"
    board["fallback"] = {"stage": "quality_rule_based", "quality_issues": quality_issues}
    raw["storyboard"] = board
    return raw


def merge_review_notes(*groups: list[str]) -> list[str]:
    notes: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            note = str(item or "").strip()
            if note and note not in seen:
                notes.append(note)
                seen.add(note)
    return notes


def storyboard_generation_payload(
    *,
    generation: dict[str, Any],
    subjects: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
    target_duration_sec: int | None = None,
) -> dict[str, Any]:
    script = generation.get("script") if isinstance(generation.get("script"), dict) else {}
    target = safe_int(target_duration_sec, default=0)
    return {
        "generation_id": generation.get("generation_id", ""),
        "topic": generation.get("topic", ""),
        "time_range": generation.get("time_range", ""),
        "script_title": script.get("title") or generation.get("script_title") or generation.get("topic") or "",
        "full_script": script.get("article") or "",
        "adapted_segments": script.get("adapted_segments") or (script.get("storyboard_script") or {}).get("adapted_segments") or [],
        "fact_cards": script.get("fact_cards") or [],
        "causal_chain": script.get("causal_chain") or [],
        "outline": script.get("outline") or [],
        "matched_events": generation.get("matched_events") or [],
        "provided_subjects": [reference_subject_payload(subject) for subject in subjects],
        "provided_scenes": [reference_scene_payload(scene) for scene in scenes],
        "existing_subject_anchors": [reference_subject_payload(subject) for subject in subjects if subject.get("anchor_asset_id")],
        "existing_scene_anchors": [reference_scene_payload(scene) for scene in scenes if scene.get("anchor_asset_id")],
        "visual_style_policy": DEFAULT_STYLE_POLICY,
        "target_duration_sec": target,
    }


def reference_subject_payload(subject: dict[str, Any]) -> dict[str, Any]:
    return {
        "subject_id": subject.get("subject_id", ""),
        "name": subject.get("canonical_name") or subject.get("name") or "",
        "canonical_name": subject.get("canonical_name") or subject.get("name") or "",
        "visual_phase_label": subject.get("visual_phase_label", ""),
        "subject_type": subject.get("subject_type", ""),
        "role_in_script": subject.get("role_in_script", ""),
        "anchor_asset_id": subject.get("anchor_asset_id", ""),
        "asset_url": subject.get("anchor_asset_id", ""),
        "visual_prompt": compact_reference_visual_prompt(subject.get("visual_prompt", ""), kind="subject"),
        "negative_prompt": subject.get("negative_prompt", ""),
    }


def reference_scene_payload(scene: dict[str, Any]) -> dict[str, Any]:
    return {
        "scene_id": scene.get("scene_id", ""),
        "name": scene.get("canonical_name") or scene.get("name") or "",
        "canonical_name": scene.get("canonical_name") or scene.get("name") or "",
        "visual_phase_label": scene.get("visual_phase_label", ""),
        "scene_type": scene.get("scene_type", ""),
        "role_in_script": scene.get("role_in_script", ""),
        "anchor_asset_id": scene.get("anchor_asset_id", ""),
        "asset_url": scene.get("anchor_asset_id", ""),
        "visual_prompt": compact_reference_visual_prompt(scene.get("visual_prompt", ""), kind="scene"),
        "negative_prompt": scene.get("negative_prompt", ""),
    }


def build_storyboard_prompt(payload: dict[str, Any]) -> str:
    target_duration = safe_int(payload.get("target_duration_sec"), default=0)
    prompt_payload = {
        "full_script": payload.get("full_script", ""),
        "adapted_segments": payload.get("adapted_segments") or [],
        "provided_subjects": payload.get("provided_subjects") or [],
        "provided_scenes": payload.get("provided_scenes") or [],
        "existing_subject_anchors": payload.get("existing_subject_anchors") or [],
        "existing_scene_anchors": payload.get("existing_scene_anchors") or [],
        "visual_style_policy": payload.get("visual_style_policy") or DEFAULT_STYLE_POLICY,
        "duration_policy": (
            f"explicit_soft_target_{target_duration}_seconds"
            if target_duration > 0
            else "derive_shot_count_and_total_duration_from_full_script_content"
        ),
    }
    if target_duration > 0:
        prompt_payload["target_duration_sec"] = target_duration
    return (
        "你是专业历史科普短剧分镜导演，不是摘要员，不是剧本改写员。\n"
        "必须先把完整剧本划分成少量“主场景 scene_blocks”，再在每个主场景内拆关键帧/分镜 shots；"
        "不允许把一句旁白当成一个主场景，也不允许只输出少数大纲式镜头。\n"
        "scene_blocks 是剧情结构层，不是视觉资产池；每个主场景通常覆盖多个句子或多个连续段落，"
        "必须包含 scene_block_id、title、summary、dramatic_purpose、location、time_context、"
        "source_paragraph_indexes、source_text_start、source_text_end、source_excerpt、key_beats。\n"
        "每个 shot 必须绑定 source_paragraph_index、source_text_start、source_text_end、source_excerpt；"
        "每个 shot 必须绑定所属主场景 scene_block_id，sequence_id 必须等于 scene_block_id，sequence_title 必须等于主场景 title；"
        "视觉解释、地图、图解或转场补充镜头必须 is_supplemental=true 并填写 supplemental_reason。\n"
        "每个 shot 必须包含 narration、subtitle_text、shot_type、visual_goal、duration_sec、"
        "sequence_id、sequence_title、beat_id、beat_title、transition、continuity、production_plan、prompt_parts。\n"
        "continuity 要说明 previous_shot_relation、screen_direction、continuity_axis、spatial_continuity_note、visual_bridge。\n"
        "production_plan 要包含 render_method、cost_tier、needs_keyframe、needs_video、recommended_tool、reason。"
        "地图迁徙镜头优先 map_animation；抽象解释优先 diagram 或 motion_graphics；普通叙事优先 low/medium cost。\n"
        "prompt_parts 必须拆出 style_prompt、scene_prompt、subject_prompt、composition_prompt、camera_prompt、lighting_prompt、negative_prompt。\n"
        "优先绑定 provided_subjects 和 provided_scenes；不要把道具、动物、现代比喻或抽象概念塞进 subject_ids 或 scene_id。\n"
        "不能直接改写剧本；如果剧本某处不利于视觉表达，写入 script_feedback，不要擅自删除。\n"
        "如果输入包含 adapted_segments，必须优先按 adapted_segments 的 dramatic_function、visual_progression 和 scene_intent 组织主场景和镜头；full_script 作为完整旁白兜底。\n"
        "根据完整剧本内容决定镜头数量和每镜时长；没有显式目标时长时，不要为了凑固定秒数压缩或拉长内容。"
        "一个镜头通常只承载 1 到 2 句旁白；长段落必须拆成多个镜头。\n"
        "只输出严格 JSON object，格式为 {\"storyboard\": {\"title\": \"...\", \"coverage\": {}, "
        "\"script_feedback\": [], \"review_notes\": [], \"scene_blocks\": [], \"shots\": []}}。\n\n"
        f"输入：\n{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}"
    )


def normalize_storyboard_payload(
    payload: dict[str, Any],
    *,
    generation: dict[str, Any],
    subjects: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
    target_duration_sec: int | None,
    source_type: str = "script_generation",
    source_filename: str = "",
) -> dict[str, Any]:
    board = payload.get("storyboard") if isinstance(payload, dict) and isinstance(payload.get("storyboard"), dict) else payload
    if not isinstance(board, dict):
        board = {}
    script = generation.get("script") if isinstance(generation.get("script"), dict) else {}
    article = str(script.get("article") or "")
    paragraphs = split_article_into_paragraph_spans(article)
    subject_lookup = {subject.get("subject_id"): reference_subject_payload(subject) for subject in subjects if subject.get("subject_id")}
    scene_lookup = {scene.get("scene_id"): reference_scene_payload(scene) for scene in scenes if scene.get("scene_id")}
    raw_shots = board.get("shots") if isinstance(board.get("shots"), list) else []
    normalized_shots = [
        normalize_shot_payload(
            shot,
            index=index,
            subject_lookup=subject_lookup,
            scene_lookup=scene_lookup,
            paragraphs=paragraphs,
        )
        for index, shot in enumerate(raw_shots, start=1)
        if isinstance(shot, dict)
    ]
    scene_blocks = normalize_scene_blocks_for_storyboard(
        board.get("scene_blocks"),
        paragraphs=paragraphs,
        shots=normalized_shots,
        scenes=scenes,
    )
    style_policy = board.get("style_policy") if isinstance(board.get("style_policy"), dict) else DEFAULT_STYLE_POLICY
    apply_scene_blocks_to_shots(scene_blocks, normalized_shots)
    apply_shot_structure_links(normalized_shots)
    enrich_storyboard_prompts_with_continuity(normalized_shots, scene_blocks, style_policy=style_policy)
    apply_scene_block_metrics(scene_blocks, normalized_shots)
    actual_duration = sum(float(shot["duration_sec"]) for shot in normalized_shots)
    provider_coverage = board.get("coverage") if isinstance(board.get("coverage"), dict) else {}
    coverage = compute_storyboard_coverage(paragraphs, normalized_shots)
    if provider_coverage:
        coverage["provider_coverage"] = provider_coverage
    script_feedback = board.get("script_feedback") if isinstance(board.get("script_feedback"), list) else infer_script_feedback(paragraphs)
    review_notes = [str(item) for item in board.get("review_notes") or [] if str(item).strip()]
    if coverage.get("coverage_ratio", 1) < 0.9:
        uncovered = ", ".join(str(item) for item in coverage.get("uncovered_paragraphs") or [])
        review_notes.append(f"剧本覆盖率低于 90%，未覆盖段落：{uncovered or '未知'}。")
    requested_target_duration = safe_int(target_duration_sec, default=0)
    board_target_duration = safe_int(board.get("target_duration_sec"), default=0)
    resolved_target_duration = (
        board_target_duration
        if board_target_duration > 0
        else requested_target_duration
        if requested_target_duration > 0
        else round(actual_duration)
    )
    status = "needs_review" if coverage.get("coverage_ratio", 1) < 0.9 or any(shot["needs_manual_review"] for shot in normalized_shots) else "completed"
    resolved_status = "needs_review" if coverage.get("coverage_ratio", 1) < 0.9 else str(board.get("status") or status)
    title = str(board.get("title") or f"{script.get('title') or generation.get('topic') or '剧本'} 分镜").strip()
    return {
        "storyboard_id": str(board.get("storyboard_id") or ""),
        "generation_id": generation.get("generation_id", ""),
        "title": title,
        "source_type": source_type,
        "source_filename": source_filename,
        "status": resolved_status,
        "target_duration_sec": int(resolved_target_duration),
        "actual_duration_sec": actual_duration,
        "shot_count": len(normalized_shots),
        "style_policy": style_policy,
        "missing_subject_candidates": normalize_candidate_list(board.get("missing_subject_candidates")),
        "missing_scene_candidates": normalize_candidate_list(board.get("missing_scene_candidates")),
        "review_notes": review_notes,
        "coverage": coverage,
        "script_feedback": script_feedback,
        "scene_blocks": scene_blocks,
        "shots": normalized_shots,
        "raw": board,
    }


def normalize_scene_blocks_for_storyboard(
    raw_scene_blocks: Any,
    *,
    paragraphs: list[dict[str, Any]],
    shots: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(raw_scene_blocks, list):
        normalized = normalize_provider_scene_blocks(raw_scene_blocks, paragraphs=paragraphs, scenes=scenes)
        if normalized and len(normalized) < max(1, len(shots)):
            return normalized
    return derive_scene_blocks_from_paragraphs(paragraphs, scenes=scenes)


def normalize_provider_scene_blocks(
    raw_scene_blocks: list[Any],
    *,
    paragraphs: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_scene_blocks, start=1):
        if not isinstance(item, dict):
            continue
        paragraph_indexes = normalize_int_list(
            item.get("source_paragraph_indexes") or item.get("paragraph_indexes") or item.get("paragraphs")
        )
        source_paragraphs = [paragraph for paragraph in paragraphs if paragraph["paragraph_index"] in paragraph_indexes]
        text = "\n\n".join(paragraph["text"] for paragraph in source_paragraphs) or str(item.get("source_excerpt") or "")
        scene_block_id = str(item.get("scene_block_id") or item.get("id") or f"scene-{index:02d}").strip()
        if not scene_block_id or scene_block_id in seen:
            scene_block_id = f"scene-{index:02d}"
        seen.add(scene_block_id)
        title = str(item.get("title") or item.get("scene_title") or title_for_scene_block(text, index=index)).strip()
        source_start = safe_int(item.get("source_text_start"), default=source_paragraphs[0]["start"] if source_paragraphs else 0)
        source_end = safe_int(item.get("source_text_end"), default=source_paragraphs[-1]["end"] if source_paragraphs else source_start)
        normalized.append(
            scene_block_payload(
                scene_block_id=scene_block_id,
                index=index,
                title=title,
                paragraphs=source_paragraphs,
                fallback_text=text,
                scenes=scenes,
                summary=str(item.get("summary") or item.get("description") or "").strip(),
                dramatic_purpose=str(item.get("dramatic_purpose") or item.get("purpose") or "").strip(),
                location=str(item.get("location") or "").strip(),
                time_context=str(item.get("time_context") or item.get("time") or "").strip(),
                source_text_start=source_start,
                source_text_end=source_end,
                key_beats=[str(beat).strip() for beat in item.get("key_beats") or item.get("beats") or [] if str(beat).strip()],
            )
        )
    return normalized


def derive_scene_blocks_from_paragraphs(
    paragraphs: list[dict[str, Any]],
    *,
    scenes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not paragraphs:
        return []
    target_count = target_scene_block_count(paragraphs)
    max_chars = max(420, int(sum(len(paragraph["text"]) for paragraph in paragraphs) / max(1, target_count) * 1.25))
    blocks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_theme = ""
    for paragraph in paragraphs:
        theme = scene_block_theme_for_text(paragraph["text"])
        current_chars = sum(len(item["text"]) for item in current)
        should_break = False
        if current:
            theme_changed = theme and current_theme and theme != current_theme
            can_open_theme_block = len(blocks) + 1 < 8
            can_open_sized_block = len(blocks) + 1 < target_count
            if theme_changed and (current_chars >= 180 or len(current) >= 2) and can_open_theme_block:
                should_break = True
            elif (len(current) >= 2 or current_chars >= max_chars) and can_open_sized_block:
                should_break = True
        if should_break:
            blocks.append(current)
            current = []
            current_theme = ""
        current.append(paragraph)
        if not current_theme and theme:
            current_theme = theme
    if current:
        blocks.append(current)
    while len(blocks) > 8:
        blocks = merge_closest_scene_block_pair(blocks)
    return [
        scene_block_payload(
            scene_block_id=f"scene-{index:02d}",
            index=index,
            title=title_for_scene_block("\n\n".join(paragraph["text"] for paragraph in block), index=index),
            paragraphs=block,
            fallback_text="",
            scenes=scenes,
        )
        for index, block in enumerate(blocks, start=1)
    ]


def target_scene_block_count(paragraphs: list[dict[str, Any]]) -> int:
    paragraph_count = len(paragraphs)
    if paragraph_count <= 2:
        return max(1, paragraph_count)
    if paragraph_count <= 5:
        return 3
    return min(8, max(4, round(paragraph_count / 2)))


def merge_closest_scene_block_pair(blocks: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    if len(blocks) <= 1:
        return blocks
    best_index = 0
    best_size = None
    for index in range(len(blocks) - 1):
        size = sum(len(item["text"]) for item in blocks[index] + blocks[index + 1])
        if best_size is None or size < best_size:
            best_index = index
            best_size = size
    merged = []
    for index, block in enumerate(blocks):
        if index == best_index:
            merged.append(block + blocks[index + 1])
        elif index == best_index + 1:
            continue
        else:
            merged.append(block)
    return merged


def scene_block_payload(
    *,
    scene_block_id: str,
    index: int,
    title: str,
    paragraphs: list[dict[str, Any]],
    fallback_text: str,
    scenes: list[dict[str, Any]],
    summary: str = "",
    dramatic_purpose: str = "",
    location: str = "",
    time_context: str = "",
    source_text_start: int | None = None,
    source_text_end: int | None = None,
    key_beats: list[str] | None = None,
) -> dict[str, Any]:
    text = "\n\n".join(paragraph["text"] for paragraph in paragraphs) or fallback_text
    source_start = source_text_start if source_text_start is not None else (paragraphs[0]["start"] if paragraphs else 0)
    source_end = source_text_end if source_text_end is not None else (paragraphs[-1]["end"] if paragraphs else source_start + len(text))
    matched_scenes = match_scene_assets_for_block(text, scenes)
    return {
        "scene_block_id": scene_block_id,
        "scene_block_index": index,
        "title": title or f"主场景 {index}",
        "summary": summary or summary_for_scene_block(text),
        "dramatic_purpose": dramatic_purpose or purpose_for_scene_block(text),
        "location": location or location_for_scene_block(text),
        "time_context": time_context or time_context_for_scene_block(text),
        "source_paragraph_indexes": [paragraph["paragraph_index"] for paragraph in paragraphs],
        "source_text_start": source_start,
        "source_text_end": source_end,
        "source_excerpt": preview_text(text, limit=220),
        "scene_ids": [scene["scene_id"] for scene in matched_scenes if scene.get("scene_id")],
        "scene_names": [scene.get("canonical_name") or scene.get("name") or "" for scene in matched_scenes],
        "key_beats": key_beats or key_beats_for_scene_block(text),
        "estimated_duration_sec": 0,
        "shot_count": 0,
    }


def apply_scene_blocks_to_shots(scene_blocks: list[dict[str, Any]], shots: list[dict[str, Any]]) -> None:
    if not scene_blocks:
        return
    block_by_id = {block["scene_block_id"]: block for block in scene_blocks}
    paragraph_to_block: dict[int, dict[str, Any]] = {}
    for block in scene_blocks:
        for paragraph_index in block.get("source_paragraph_indexes") or []:
            paragraph_to_block[safe_int(paragraph_index, default=0)] = block
    previous_block = scene_blocks[0]
    for shot in shots:
        block = block_by_id.get(str(shot.get("scene_block_id") or ""))
        paragraph_index = safe_int(shot.get("source_paragraph_index"), default=0)
        if not block and paragraph_index > 0:
            block = paragraph_to_block.get(paragraph_index)
        if not block and shot.get("is_supplemental"):
            block = previous_block
        if not block:
            block = previous_block or scene_blocks[0]
        shot["scene_block_id"] = block["scene_block_id"]
        shot["scene_block_title"] = block["title"]
        shot["scene_block_index"] = block["scene_block_index"]
        shot["sequence_id"] = block["scene_block_id"]
        shot["sequence_title"] = block["title"]
        shot["beat_id"] = f"{block['scene_block_id']}-beat-{max(1, paragraph_index):02d}"
        shot["beat_title"] = beat_title_for_shot(shot)
        previous_block = block


def apply_scene_block_metrics(scene_blocks: list[dict[str, Any]], shots: list[dict[str, Any]]) -> None:
    for block in scene_blocks:
        block_shots = [shot for shot in shots if shot.get("scene_block_id") == block["scene_block_id"]]
        block["shot_count"] = len(block_shots)
        block["estimated_duration_sec"] = round(sum(float(shot.get("duration_sec") or 0) for shot in block_shots), 1)
        if not block.get("key_beats"):
            block["key_beats"] = [
                preview_text(shot.get("narration") or shot.get("source_excerpt") or "", limit=46)
                for shot in block_shots[:4]
                if (shot.get("narration") or shot.get("source_excerpt"))
            ]


def scene_block_theme_for_text(text: str) -> str:
    marker_groups = {
        "opening_survival": ["草原", "狮子", "鬣狗", "行走的自助餐", "老祖宗", "战五渣", "弱小"],
        "body_cooperation": ["大脑", "能量", "产道", "早产儿", "肌肉", "部落", "协作", "社交"],
        "fire_food": ["火", "烹饪", "篝火", "烤糊", "烤"],
        "encounter": ["黎凡特", "尼安德特", "冰河期", "第一次走出非洲"],
        "story_language": ["多巴火山", "火山灰", "语言", "讲故事", "八卦", "虚构"],
        "mass_cooperation": ["成百上千", "合作", "神祇", "祖先传说", "内讧", "群体能膨胀"],
        "symbolic_world": ["布隆伯斯", "贝壳", "赭石", "壁画", "葬礼", "仪式", "红花"],
        "migration": ["红海", "索马里", "阿拉伯", "印度洋", "巽他", "澳大利亚", "迁徙", "踏上了阿拉伯半岛"],
        "civilization_cost": ["其他人种都消失", "食物链", "大型动物", "灭绝", "文明", "月球", "股票市场", "代价"],
    }
    scores = {
        theme: sum(2 if marker in text else 0 for marker in markers)
        for theme, markers in marker_groups.items()
    }
    if "早在" in text[:80] and has_any(text[:220], ["草原", "智人", "狮子", "鬣狗"]):
        scores["opening_survival"] += 5
    if "30万年前" in text and has_any(text, ["火", "烹饪"]):
        scores["fire_food"] += 4
    if has_any(text, ["大约10万年前", "地中海东部"]) and "尼安德特" in text:
        scores["encounter"] += 5
    if "靠着讲故事" in text and has_any(text, ["成百上千", "合作"]):
        scores["mass_cooperation"] += 5
    best_theme = max(scores, key=lambda theme: scores[theme])
    return best_theme if scores[best_theme] > 0 else "narrative"


def title_for_scene_block(text: str, *, index: int) -> str:
    theme = scene_block_theme_for_text(text)
    titles = {
        "opening_survival": "开场：东非草原上的弱小智人",
        "body_cooperation": "主场景：大脑代价与部落协作",
        "fire_food": "主场景：火与烹饪改变生存方式",
        "encounter": "主场景：黎凡特遭遇与第一次失败",
        "story_language": "主场景：灾变压力与讲故事能力",
        "mass_cooperation": "主场景：故事让陌生人合作",
        "symbolic_world": "主场景：符号、仪式与共同想象",
        "migration": "主场景：走出非洲与全球迁徙",
        "civilization_cost": "主场景：智人胜出与故事的代价",
    }
    return titles.get(theme, f"主场景 {index}：叙事推进")


def summary_for_scene_block(text: str) -> str:
    sentences = split_sentences(text)
    return preview_text("".join(sentences[:2]) or text, limit=120)


def purpose_for_scene_block(text: str) -> str:
    theme = scene_block_theme_for_text(text)
    purposes = {
        "opening_survival": "建立开场钩子，说明智人的弱小处境和本集问题。",
        "body_cooperation": "解释智人生理劣势如何反过来催生群体协作。",
        "fire_food": "展示火和烹饪如何给大脑、社交与生存方式提供支撑。",
        "encounter": "制造与尼安德特人的对照，说明第一次扩张并不顺利。",
        "story_language": "把灾变、语言和虚构故事串成认知跃迁的因果段落。",
        "mass_cooperation": "说明共同故事如何把陌生个体组织成更大规模的协作群体。",
        "symbolic_world": "用符号、仪式和艺术收束共同想象的主题。",
        "migration": "展开智人再次走出非洲并扩散到全球的空间段落。",
        "civilization_cost": "收束智人胜出后的生态代价，并把故事能力延伸到现代文明。",
    }
    return purposes.get(theme, "承接上一个主场景，推进剧本的核心论点。")


def location_for_scene_block(text: str) -> str:
    mapping = [
        (["红海", "索马里", "阿拉伯"], "红海海口与阿拉伯半岛方向"),
        (["印度洋", "印度河", "恒河", "湄公河"], "印度洋海岸迁徙路线"),
        (["巽他", "澳大利亚", "巴布亚"], "巽他大陆边缘海峡"),
        (["布隆伯斯"], "布隆伯斯洞穴"),
        (["黎凡特", "地中海"], "黎凡特地区"),
        (["多巴火山", "南亚"], "南亚及印度洋周边"),
        (["篝火", "烹饪", "火"], "智人营地篝火区"),
        (["部落", "营地"], "非洲智人部落营地"),
        (["草原", "非洲东部"], "东非稀树草原"),
        (["壁画", "葬礼", "仪式"], "史前洞穴仪式空间"),
    ]
    for markers, location in mapping:
        if has_any(text, markers):
            return location
    return "按剧本上下文确定的历史环境"


def time_context_for_scene_block(text: str) -> str:
    match = re.search(r"(?:约|大约)?\d+(?:\.\d+)?万年前", text)
    if match:
        return match.group(0)
    if "冰河期" in text:
        return "冰河期"
    if "今天" in text or "现代" in text:
        return "现代回望"
    return "剧本对应历史阶段"


def key_beats_for_scene_block(text: str) -> list[str]:
    sentences = split_sentences(text)
    beats = [preview_text(sentence, limit=54) for sentence in sentences[:4] if sentence.strip()]
    if len(beats) == 1 and len(text) > 70:
        beats.extend(split_long_text(text, max_chars=54)[1:4])
    return beats or [preview_text(text, limit=54)]


def beat_title_for_shot(shot: dict[str, Any]) -> str:
    if shot.get("is_supplemental"):
        return "补充视觉说明"
    shot_type = shot.get("shot_type")
    labels = {
        "hook_shot": "开场钩子",
        "explainer_shot": "机制解释",
        "comparison_shot": "对照冲突",
        "map_shot": "空间迁徙",
        "concept_shot": "概念图解",
        "transition_shot": "转场过渡",
        "montage_shot": "蒙太奇压缩",
        "key_comic_shot": "关键画面",
    }
    return labels.get(str(shot_type), "叙事推进")


def match_scene_assets_for_block(text: str, scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matched = []
    for scene in scenes:
        name = str(scene.get("canonical_name") or scene.get("name") or "")
        aliases = [str(item) for item in scene.get("aliases") or []]
        if name and (name in text or any(alias and alias in text for alias in aliases)):
            matched.append(scene)
    if matched:
        return matched
    for markers, expected in [
        (["稀树草原", "非洲东部"], "东非稀树草原"),
        (["部落", "营地"], "非洲智人部落营地"),
        (["篝火", "烹饪", "火"], "篝火烹饪营地"),
        (["布隆伯斯"], "布隆伯斯洞穴"),
        (["黎凡特", "尼安德特"], "黎凡特冰河期遭遇地带"),
        (["红海", "索马里", "阿拉伯"], "红海海口迁徙渡口"),
        (["印度洋", "河口"], "印度洋海岸迁徙路线"),
        (["巽他", "澳大利亚"], "巽他大陆尽头海峡"),
        (["壁画", "葬礼", "仪式"], "洞穴壁画与葬礼仪式空间"),
    ]:
        if has_any(text, markers):
            matched.extend(scene for scene in scenes if str(scene.get("canonical_name") or scene.get("name") or "") == expected)
    return matched


def normalize_int_list(value: Any) -> list[int]:
    if isinstance(value, int):
        return [value]
    if isinstance(value, str):
        value = re.split(r"[,，、\s]+", value.strip())
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        parsed = safe_int(item, default=0)
        if parsed > 0 and parsed not in result:
            result.append(parsed)
    return result


def normalize_shot_payload(
    shot: dict[str, Any],
    *,
    index: int,
    subject_lookup: dict[str, dict[str, Any]],
    scene_lookup: dict[str, dict[str, Any]],
    paragraphs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    paragraphs = paragraphs or []
    subject_ids = [str(item) for item in shot.get("subject_ids") or [] if str(item).strip()]
    subject_names = [str(item) for item in shot.get("subject_names") or [] if str(item).strip()]
    if not subject_names:
        subject_names = [subject_lookup[subject_id]["name"] for subject_id in subject_ids if subject_id in subject_lookup]
    scene_id = str(shot.get("scene_id") or "")
    scene_name = str(shot.get("scene_name") or "")
    if not scene_name and scene_id in scene_lookup:
        scene_name = scene_lookup[scene_id]["name"]
    reference_assets = shot.get("reference_assets") if isinstance(shot.get("reference_assets"), dict) else {}
    if not reference_assets:
        reference_assets = build_reference_assets(
            subject_ids=subject_ids,
            scene_id=scene_id,
            subject_lookup=subject_lookup,
            scene_lookup=scene_lookup,
        )
    camera = shot.get("camera") if isinstance(shot.get("camera"), dict) else {}
    source = source_fields_for_shot(shot, paragraphs)
    is_supplemental = bool(shot.get("is_supplemental") or shot.get("supplemental"))
    narration = str(shot.get("narration") or shot.get("subtitle_text") or source["source_excerpt"]).strip()
    subtitle_text = str(shot.get("subtitle_text") or narration).strip()
    shot_type = normalize_shot_type(shot.get("shot_type"))
    duration_sec = safe_float(shot.get("duration_sec"), default=0.0)
    if duration_sec <= 0:
        duration_sec = duration_for_shot_type(shot_type, narration)
    visual_goal = str(shot.get("visual_goal") or "").strip()
    if is_generic_visual_goal(visual_goal):
        visual_goal = visual_goal_for_shot(narration, shot_type)
    normalized = {
        "shot_id": str(shot.get("shot_id") or f"shot-{uuid4().hex}"),
        "shot_index": int(shot.get("shot_index") or index),
        "narration": narration,
        "subtitle_text": subtitle_text,
        "shot_type": shot_type,
        "visual_goal": visual_goal,
        "scene_id": scene_id,
        "scene_name": scene_name,
        "subject_ids": subject_ids,
        "subject_names": subject_names,
        "visual_elements": [str(item) for item in shot.get("visual_elements") or [] if str(item).strip()],
        "reference_assets": reference_assets,
        "camera": {
            "shot_size": str(camera.get("shot_size") or "medium wide shot"),
            "angle": str(camera.get("angle") or "eye level"),
            "movement": str(camera.get("movement") or "slow push in"),
        },
        "duration_sec": duration_sec,
        "keyframe_prompt": str(shot.get("keyframe_prompt") or "").strip(),
        "video_prompt": str(shot.get("video_prompt") or "").strip(),
        "negative_prompt": str(shot.get("negative_prompt") or DEFAULT_NEGATIVE_PROMPT).strip(),
        "fact_safety_note": str(shot.get("fact_safety_note") or "").strip(),
        "asset_status": str(shot.get("asset_status") or asset_status_for_reference_assets(reference_assets)),
        "keyframe_asset_id": str(shot.get("keyframe_asset_id") or ""),
        "video_asset_id": str(shot.get("video_asset_id") or ""),
        "needs_manual_review": bool(shot.get("needs_manual_review")),
        "source_paragraph_index": source["source_paragraph_index"],
        "source_text_start": source["source_text_start"],
        "source_text_end": source["source_text_end"],
        "source_excerpt": source["source_excerpt"],
        "is_supplemental": is_supplemental,
        "supplemental_reason": str(shot.get("supplemental_reason") or ("视觉补充镜头。" if is_supplemental else "")),
        "sequence_id": str(shot.get("sequence_id") or ""),
        "sequence_title": str(shot.get("sequence_title") or ""),
        "beat_id": str(shot.get("beat_id") or ""),
        "beat_title": str(shot.get("beat_title") or ""),
        "prev_shot_id": str(shot.get("prev_shot_id") or ""),
        "next_shot_id": str(shot.get("next_shot_id") or ""),
        "transition": str(shot.get("transition") or "cut"),
        "continuity": shot.get("continuity") if isinstance(shot.get("continuity"), dict) else {},
        "production_plan": shot.get("production_plan") if isinstance(shot.get("production_plan"), dict) else {},
        "prompt_parts": shot.get("prompt_parts") if isinstance(shot.get("prompt_parts"), dict) else {},
        "raw": shot,
    }
    if not normalized["sequence_id"]:
        normalized["sequence_id"] = f"seq-{max(1, normalized['source_paragraph_index']):02d}"
    if not normalized["sequence_title"]:
        normalized["sequence_title"] = sequence_title_for_shot(normalized)
    if not normalized["beat_id"]:
        normalized["beat_id"] = f"{normalized['sequence_id']}-beat-01"
    if not normalized["beat_title"]:
        normalized["beat_title"] = normalized["sequence_title"]
    if not normalized["visual_goal"]:
        normalized["visual_goal"] = visual_goal_for_shot(normalized["narration"], normalized["shot_type"])
    if not normalized["continuity"]:
        normalized["continuity"] = continuity_for_shot(normalized, previous=None)
    if not normalized["production_plan"]:
        normalized["production_plan"] = production_plan_for_shot(normalized)
    if not normalized["prompt_parts"]:
        normalized["prompt_parts"] = prompt_parts_for_shot(normalized)
    else:
        normalized["prompt_parts"] = ensure_frame_size_prompt_part(sanitize_prompt_parts(normalized["prompt_parts"]))
    if not normalized["keyframe_prompt"]:
        normalized["keyframe_prompt"] = compose_keyframe_prompt(normalized)
    if not normalized["video_prompt"]:
        normalized["video_prompt"] = compose_video_prompt(normalized)
    return normalized


def split_article_into_paragraph_spans(article: str) -> list[dict[str, Any]]:
    text = str(article or "")
    paragraphs: list[dict[str, Any]] = []
    for index, match in enumerate(re.finditer(r"\S.*?(?=\n\s*\n|\Z)", text, flags=re.S), start=1):
        raw = match.group()
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw.rstrip())
        paragraph_text = raw.strip()
        if not paragraph_text:
            continue
        paragraphs.append(
            {
                "paragraph_index": index,
                "text": paragraph_text,
                "start": match.start() + leading,
                "end": match.start() + trailing,
            }
        )
    if not paragraphs and text.strip():
        clean = text.strip()
        start = text.find(clean)
        paragraphs.append({"paragraph_index": 1, "text": clean, "start": start, "end": start + len(clean)})
    return paragraphs


def split_article_into_narration_units_with_source(article: str, *, max_chars: int = 82) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for paragraph in split_article_into_paragraph_spans(article):
        paragraph_text = paragraph["text"]
        if len(paragraph_text) <= max_chars:
            units.append(
                {
                    "text": paragraph_text,
                    "paragraph_index": paragraph["paragraph_index"],
                    "start": paragraph["start"],
                    "end": paragraph["end"],
                }
            )
            continue
        cursor = 0
        for sentence in split_sentences(paragraph_text):
            sentence_start = paragraph_text.find(sentence, cursor)
            if sentence_start < 0:
                sentence_start = cursor
            for chunk in split_long_text(sentence, max_chars=max_chars):
                chunk_start = paragraph_text.find(chunk, sentence_start)
                if chunk_start < 0:
                    chunk_start = sentence_start
                units.append(
                    {
                        "text": chunk,
                        "paragraph_index": paragraph["paragraph_index"],
                        "start": paragraph["start"] + chunk_start,
                        "end": paragraph["start"] + chunk_start + len(chunk),
                    }
                )
                sentence_start = chunk_start + len(chunk)
            cursor = sentence_start + len(sentence)
    return units or [{"text": str(article or "").strip(), "paragraph_index": 1, "start": 0, "end": len(str(article or "").strip())}]


def split_article_into_narration_units(article: str, *, max_chars: int = 82) -> list[str]:
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n+", str(article or "")) if paragraph.strip()]
    units: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            units.append(paragraph)
            continue
        sentences = split_sentences(paragraph)
        current = ""
        for sentence in sentences:
            if not current:
                current = sentence
            elif len(current) + len(sentence) <= max_chars:
                current += sentence
            else:
                units.extend(split_long_text(current, max_chars=max_chars))
                current = sentence
        if current:
            units.extend(split_long_text(current, max_chars=max_chars))
    return units or [str(article or "").strip()]


def split_sentences(text: str) -> list[str]:
    parts = re.findall(r".+?[。！？!?；;]|.+$", text)
    return [part.strip() for part in parts if part.strip()]


def split_long_text(text: str, *, max_chars: int) -> list[str]:
    clean = str(text or "").strip()
    if len(clean) <= max_chars:
        return [clean] if clean else []
    chunks = []
    for start in range(0, len(clean), max_chars):
        chunk = clean[start : start + max_chars].strip()
        if chunk:
            chunks.append(chunk)
    return chunks


def source_fields_for_shot(shot: dict[str, Any], paragraphs: list[dict[str, Any]]) -> dict[str, Any]:
    is_supplemental = bool(shot.get("is_supplemental") or shot.get("supplemental"))
    if is_supplemental:
        return {"source_paragraph_index": 0, "source_text_start": 0, "source_text_end": 0, "source_excerpt": ""}
    paragraph_index = safe_int(shot.get("source_paragraph_index"), default=0)
    start = safe_int(shot.get("source_text_start"), default=-1)
    end = safe_int(shot.get("source_text_end"), default=-1)
    excerpt = str(shot.get("source_excerpt") or "").strip()
    if paragraph_index > 0 and start >= 0 and end > start and excerpt:
        return {
            "source_paragraph_index": paragraph_index,
            "source_text_start": start,
            "source_text_end": end,
            "source_excerpt": excerpt,
        }
    text = str(shot.get("narration") or shot.get("subtitle_text") or "").strip()
    for paragraph in paragraphs:
        position = paragraph["text"].find(text)
        if text and position >= 0:
            source_start = paragraph["start"] + position
            return {
                "source_paragraph_index": paragraph["paragraph_index"],
                "source_text_start": source_start,
                "source_text_end": source_start + len(text),
                "source_excerpt": text,
            }
    if paragraphs:
        paragraph = paragraphs[min(max(paragraph_index, 1), len(paragraphs)) - 1]
        excerpt = text or paragraph["text"]
        return {
            "source_paragraph_index": paragraph["paragraph_index"],
            "source_text_start": paragraph["start"],
            "source_text_end": paragraph["start"] + len(excerpt),
            "source_excerpt": excerpt,
        }
    return {"source_paragraph_index": 0, "source_text_start": 0, "source_text_end": 0, "source_excerpt": text}


def apply_shot_structure_links(shots: list[dict[str, Any]]) -> None:
    for index, shot in enumerate(shots):
        previous = shots[index - 1] if index else None
        next_shot = shots[index + 1] if index + 1 < len(shots) else None
        shot["shot_index"] = index + 1
        shot["prev_shot_id"] = previous["shot_id"] if previous else ""
        shot["next_shot_id"] = next_shot["shot_id"] if next_shot else ""
        transition = transition_method_for_shot(shot, previous=previous)
        shot["transition"] = transition
        shot["continuity"] = continuity_for_shot(
            shot,
            previous=previous,
            next_shot=next_shot,
            transition=transition,
        )


def enrich_storyboard_prompts_with_continuity(
    shots: list[dict[str, Any]],
    scene_blocks: list[dict[str, Any]],
    *,
    style_policy: dict[str, Any],
) -> None:
    block_by_id = {str(block.get("scene_block_id") or ""): block for block in scene_blocks}
    shot_by_id = {str(shot.get("shot_id") or ""): shot for shot in shots}
    for index, shot in enumerate(shots):
        previous = shot_by_id.get(str(shot.get("prev_shot_id") or "")) or (shots[index - 1] if index else None)
        next_shot = shot_by_id.get(str(shot.get("next_shot_id") or "")) or (shots[index + 1] if index + 1 < len(shots) else None)
        scene_block = block_by_id.get(str(shot.get("scene_block_id") or ""), {})
        keyframe_base = str(shot.get("keyframe_prompt") or "").strip() or compose_keyframe_prompt(
            shot,
            style_policy=style_policy,
        )
        keyframe_base = strip_anchor_generation_task_text(keyframe_base)
        keyframe_base = ensure_keyframe_frame_size_prompt(keyframe_base, shot)
        keyframe_lines = keyframe_continuity_prompt_lines(
            shot,
            previous=previous,
            next_shot=next_shot,
            scene_block=scene_block,
        )
        missing_keyframe_lines = missing_prompt_lines(keyframe_base, keyframe_lines)
        if missing_keyframe_lines:
            shot["keyframe_prompt"] = append_prompt_lines(keyframe_base, missing_keyframe_lines)
        else:
            shot["keyframe_prompt"] = keyframe_base

        video_base = str(shot.get("video_prompt") or "").strip() or compose_video_prompt(shot)
        video_lines = video_continuity_prompt_lines(
            shot,
            previous=previous,
            next_shot=next_shot,
        )
        if video_lines and "视频连续性：" not in video_base:
            shot["video_prompt"] = append_prompt_lines(video_base, video_lines)
        else:
            shot["video_prompt"] = video_base

        prompt_parts = dict(shot.get("prompt_parts") or {})
        prompt_parts = sanitize_prompt_parts(prompt_parts)
        prompt_parts["frame_purpose_prompt"] = frame_purpose_prompt(shot)
        prompt_parts["frame_change_prompt"] = frame_change_prompt(previous, shot) if previous else ""
        prompt_parts["continuity_prompt"] = " ".join(keyframe_lines)
        prompt_parts["previous_shot_prompt"] = previous_shot_prompt(previous, shot) if previous else ""
        prompt_parts["next_shot_prompt"] = next_shot_prompt(next_shot) if next_shot else ""
        prompt_parts["transition_prompt"] = transition_prompt_for_shot(shot)
        prompt_parts["scene_block_prompt"] = scene_block_prompt(scene_block)
        shot["prompt_parts"] = prompt_parts


def compute_storyboard_coverage(paragraphs: list[dict[str, Any]], shots: list[dict[str, Any]]) -> dict[str, Any]:
    paragraph_indexes = {paragraph["paragraph_index"] for paragraph in paragraphs}
    covered = {
        safe_int(shot.get("source_paragraph_index"), default=0)
        for shot in shots
        if not shot.get("is_supplemental") and safe_int(shot.get("source_paragraph_index"), default=0) > 0
    }
    uncovered = sorted(paragraph_indexes - covered)
    paragraph_count = len(paragraph_indexes)
    covered_count = paragraph_count - len(uncovered)
    coverage_ratio = 1.0 if paragraph_count == 0 else round(covered_count / paragraph_count, 4)
    return {
        "paragraph_count": paragraph_count,
        "covered_paragraph_count": covered_count,
        "coverage_ratio": coverage_ratio,
        "uncovered_paragraphs": uncovered,
        "supplemental_shot_count": sum(1 for shot in shots if shot.get("is_supplemental")),
        "warning": "" if coverage_ratio >= 0.9 else "部分剧本段落未覆盖。",
    }


def storyboard_quality_issues(storyboard: dict[str, Any], *, generation: dict[str, Any]) -> list[str]:
    script = generation.get("script") if isinstance(generation.get("script"), dict) else {}
    article = str(script.get("article") or "")
    if not article.strip():
        return []
    paragraphs = split_article_into_paragraph_spans(article)
    units = split_article_into_narration_units_with_source(article)
    shot_count = len(storyboard.get("shots") or [])
    actual_duration = safe_float(storyboard.get("actual_duration_sec"), default=0.0)
    coverage = storyboard.get("coverage") if isinstance(storyboard.get("coverage"), dict) else {}
    provider_coverage = coverage.get("provider_coverage") if isinstance(coverage.get("provider_coverage"), dict) else {}
    issues: list[str] = []

    coverage_ratio = safe_float(coverage.get("coverage_ratio"), default=1.0)
    if coverage_ratio < 0.9:
        issues.append(f"本地覆盖率只有 {round(coverage_ratio * 100)}%。")

    provider_total_shots = safe_int(provider_coverage.get("total_shots"), default=0)
    if provider_total_shots > 0 and shot_count < provider_total_shots:
        issues.append(f"模型自报应有 {provider_total_shots} 镜头，但实际只返回 {shot_count} 镜头。")

    provider_duration = safe_float(provider_coverage.get("estimated_total_duration_seconds"), default=0.0)
    if provider_duration > 0 and actual_duration > 0 and actual_duration < provider_duration * 0.75:
        issues.append(f"模型估算约 {round(provider_duration)} 秒，但实际镜头时长只有 {round(actual_duration)} 秒。")

    expected_min_shots = max(len(paragraphs), int(len(units) * 0.45))
    if len(article) >= 600 and shot_count < expected_min_shots:
        issues.append(f"剧本拆分至少需要约 {expected_min_shots} 个镜头，当前只有 {shot_count} 个。")

    return issues


def infer_script_feedback(paragraphs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    feedback = []
    for paragraph in paragraphs:
        text = paragraph["text"]
        if "多巴火山" in text and "语言" in text:
            feedback.append(
                {
                    "severity": "medium",
                    "paragraph_index": paragraph["paragraph_index"],
                    "issue": "灾变压力与语言能力变化之间需要更清晰的因果桥。",
                    "suggestion": "建议补一句灾变压力如何促使群体更依赖信息传递。",
                    "should_rewrite_script": True,
                }
            )
    return feedback


def sequence_title_for_shot(shot: dict[str, Any]) -> str:
    text = f"{shot.get('narration', '')}{shot.get('visual_goal', '')}"
    if has_any(text, ["草原", "开局", "开场"]):
        return "开场：东非草原上的弱小智人"
    if has_any(text, ["大脑", "早产儿", "产道", "能量"]):
        return "生理劣势：大脑、早产儿与协作压力"
    if has_any(text, ["火", "烹饪", "篝火"]):
        return "火与烹饪：能量和协作"
    if has_any(text, ["黎凡特", "尼安德特"]):
        return "第一次遭遇：冰河期与其他人种"
    if has_any(text, ["布隆伯斯", "贝壳", "赭石"]):
        return "象征萌芽：洞穴里的符号能力"
    if has_any(text, ["红海", "印度洋", "巽他", "迁徙"]):
        return "走出非洲：海岸线与大陆边缘"
    if has_any(text, ["壁画", "葬礼", "仪式"]):
        return "结尾：故事与共同想象"
    if shot.get("is_supplemental"):
        return "视觉补充：解释与转场"
    return f"段落 {max(1, safe_int(shot.get('source_paragraph_index'), default=1))}：叙事推进"


def transition_method_for_shot(shot: dict[str, Any], *, previous: dict[str, Any] | None) -> str:
    if not previous:
        return "opening"
    same_block = bool(shot.get("scene_block_id")) and shot.get("scene_block_id") == previous.get("scene_block_id")
    if same_block:
        return "cut"
    if shot.get("shot_type") == "map_shot" or previous.get("shot_type") == "map_shot":
        return "map_transition"
    if shot.get("shot_type") in {"explainer_shot", "concept_shot", "transition_shot"}:
        return "concept_bridge"
    bridge = visual_bridge_for_shot(shot, previous=previous)
    if bridge != "旁白关键词":
        return "visual_bridge"
    return "narration_bridge"


def continuity_for_shot(
    shot: dict[str, Any],
    *,
    previous: dict[str, Any] | None,
    next_shot: dict[str, Any] | None = None,
    transition: str | None = None,
) -> dict[str, str]:
    transition_method = transition or transition_method_for_shot(shot, previous=previous)
    if not previous:
        relation = "not_applicable"
        axis = "not_applicable"
    elif transition_method == "map_transition":
        relation = "map_transition"
        axis = "map_axis"
    elif shot.get("shot_type") in {"explainer_shot", "concept_shot", "transition_shot"}:
        relation = "concept_cut"
        axis = "not_applicable"
    elif shot.get("scene_block_id") and shot.get("scene_block_id") == previous.get("scene_block_id"):
        relation = "continues_same_scene_block"
        axis = "same_axis"
    elif shot.get("scene_id") and shot.get("scene_id") == previous.get("scene_id"):
        relation = "continues_same_scene"
        axis = "same_axis"
    elif shot.get("scene_id") != previous.get("scene_id"):
        relation = "location_jump"
        axis = "new_axis"
    else:
        relation = "time_jump"
        axis = "new_axis"
    bridge = visual_bridge_for_shot(shot, previous=previous)
    return {
        "previous_shot_relation": relation,
        "screen_direction": screen_direction_for_shot(shot, previous=previous, transition=transition_method),
        "continuity_axis": axis,
        "spatial_continuity_note": spatial_continuity_note_for_shot(shot, previous=previous, transition=transition_method),
        "visual_bridge": bridge,
        "transition_method": transition_method,
        "transition_guidance": transition_guidance_for_method(transition_method, shot, previous=previous),
        "previous_scene_block_title": str(previous.get("scene_block_title") or "") if previous else "",
        "current_scene_block_title": str(shot.get("scene_block_title") or ""),
        "next_scene_block_title": str(next_shot.get("scene_block_title") or "") if next_shot else "",
    }


def visual_bridge_for_shot(shot: dict[str, Any], *, previous: dict[str, Any] | None = None) -> str:
    text = f"{shot.get('narration', '')}{shot.get('visual_goal', '')}"
    previous_text = f"{previous.get('narration', '')}{previous.get('visual_goal', '')}" if previous else ""
    if shot.get("shot_type") == "map_shot":
        return "地图缩放与迁徙箭头"
    if has_any(text + previous_text, ["火", "篝火"]):
        return "火光"
    if has_any(text + previous_text, ["尘土", "草原"]):
        return "尘土和地平线"
    if has_any(text + previous_text, ["海", "红海", "印度洋", "海岸"]):
        return "海岸线与路线箭头"
    if has_any(text + previous_text, ["洞穴", "壁画", "赭石", "贝壳"]):
        return "岩壁纹理与赭石色"
    return "旁白关键词"


def screen_direction_for_shot(
    shot: dict[str, Any],
    *,
    previous: dict[str, Any] | None,
    transition: str,
) -> str:
    if transition == "map_transition" or shot.get("shot_type") == "map_shot":
        return "left_to_right_route"
    if previous and shot.get("scene_block_id") == previous.get("scene_block_id"):
        return "preserve_previous_axis"
    if transition in {"visual_bridge", "narration_bridge"}:
        return "static_to_new_axis"
    return "static"


def spatial_continuity_note_for_shot(
    shot: dict[str, Any],
    *,
    previous: dict[str, Any] | None,
    transition: str,
) -> str:
    if not previous:
        return "开场镜头要先建立本集统一画风、主体外观、时代氛围和基本空间轴线。"
    if shot.get("scene_block_id") == previous.get("scene_block_id"):
        return "主场景内连续：延续上一镜头的主体外观、光线色温、空间方向和叙事情绪，只改变当前镜头需要强调的动作或构图。"
    if transition == "map_transition":
        return "主场景切换：用地图缩放、路线箭头或地理位置关系先交代空间，再进入新场景。"
    if transition == "visual_bridge":
        return "主场景切换：保留上一镜头的视觉母题作为入画元素，再让新地点或新时间自然显现。"
    if transition == "concept_bridge":
        return "主场景切换：用图解、符号或概念画面承接因果关系，避免真实历史场景突然跳切。"
    return "主场景切换：用旁白关键词、因果句或字幕锚点建立时间和地点关系，再进入新场景。"


def transition_guidance_for_method(
    method: str,
    shot: dict[str, Any],
    *,
    previous: dict[str, Any] | None,
) -> str:
    if method == "opening":
        return "先用稳定开场画面建立统一画风、主体设定和本集叙事问题。"
    if method == "map_transition":
        return "用地图缩放、路线箭头、海岸线或地理标签完成主场景切换，地图结束点要对应下一镜头空间。"
    if method == "visual_bridge":
        bridge = visual_bridge_for_shot(shot, previous=previous)
        return f"用“{bridge}”作为视觉桥，上一镜头末尾的颜色、形状或运动方向在本镜头开头继续出现。"
    if method == "concept_bridge":
        return "用图解层、符号化画面或旁白关键词承接抽象因果，先解释关系，再落到当前画面。"
    if method == "narration_bridge":
        return "用旁白中的关键词、因果句或字幕锚点完成切换，让观众知道为什么从上一主场景来到这里。"
    return "主场景内连续：保持上一镜头的画风、主体、光线、空间轴线和叙事情绪，动作自然接续。"


def scene_block_prompt(scene_block: dict[str, Any]) -> str:
    if not scene_block:
        return ""
    parts = [
        f"主场景：{scene_block.get('title') or ''}",
        f"目标：{scene_block.get('dramatic_purpose') or ''}",
        f"摘要：{scene_block.get('summary') or ''}",
    ]
    return "；".join(part for part in parts if not part.endswith("："))


def previous_shot_prompt(previous: dict[str, Any], shot: dict[str, Any]) -> str:
    relation = "主场景内连续" if shot.get("scene_block_id") == previous.get("scene_block_id") else "主场景切换"
    previous_title = previous.get("scene_block_title") or previous.get("sequence_title") or "上一主场景"
    previous_goal = preview_text(previous.get("visual_goal") or previous.get("narration") or "", limit=72)
    return f"上一镜头承接：{relation}；上一主场景“{previous_title}”；上一镜头画面终点：{previous_goal}。"


def next_shot_prompt(next_shot: dict[str, Any]) -> str:
    next_title = next_shot.get("scene_block_title") or next_shot.get("sequence_title") or "下一主场景"
    next_goal = preview_text(next_shot.get("visual_goal") or next_shot.get("narration") or "", limit=72)
    return f"下一镜头预留：为“{next_title}”保留动作、视线、光线或视觉母题的出口；下一镜头目标：{next_goal}。"


def transition_prompt_for_shot(shot: dict[str, Any]) -> str:
    continuity = shot.get("continuity") if isinstance(shot.get("continuity"), dict) else {}
    method = continuity.get("transition_method") or shot.get("transition") or "cut"
    guidance = continuity.get("transition_guidance") or ""
    return f"转场方式：{method}；{guidance}".strip()


def frame_purpose_prompt(shot: dict[str, Any]) -> str:
    goal = str(shot.get("visual_goal") or visual_goal_for_shot(shot.get("narration", ""), shot.get("shot_type", ""))).strip()
    narration = preview_text(shot.get("narration") or shot.get("subtitle_text") or "", limit=80)
    return f"本帧剧情任务：{goal}；当前旁白“{narration}”，画面必须提供新的剧情信息、动作状态或视觉焦点。"


def frame_change_prompt(previous: dict[str, Any], shot: dict[str, Any]) -> str:
    previous_goal = preview_text(previous.get("visual_goal") or previous.get("narration") or "", limit=60)
    current_goal = preview_text(shot.get("visual_goal") or shot.get("narration") or "", limit=60)
    if shot.get("scene_block_id") == previous.get("scene_block_id"):
        return (
            f"相对上一帧变化：从“{previous_goal}”推进到“{current_goal}”；"
            "保持画风、主体外观、光线和空间轴线连续，但必须改变动作、视线、构图焦点或画面信息。"
        )
    return (
        f"相对上一帧变化：从上一主场景“{previous.get('scene_block_title') or previous.get('sequence_title') or '上一场景'}”"
        f"切换到当前“{shot.get('scene_block_title') or shot.get('sequence_title') or '当前场景'}”；"
        "用转场方式交代地点、时间或因果变化，避免无解释跳切。"
    )


def keyframe_continuity_prompt_lines(
    shot: dict[str, Any],
    *,
    previous: dict[str, Any] | None,
    next_shot: dict[str, Any] | None,
    scene_block: dict[str, Any],
) -> list[str]:
    continuity = shot.get("continuity") if isinstance(shot.get("continuity"), dict) else {}
    lines = []
    block_prompt = scene_block_prompt(scene_block)
    if block_prompt:
        lines.append(block_prompt)
    lines.append(frame_purpose_prompt(shot))
    if previous:
        lines.append(previous_shot_prompt(previous, shot))
        lines.append(frame_change_prompt(previous, shot))
    else:
        lines.append("上一镜头承接：开场镜头；先建立本集统一画风、主体外观、时代氛围和空间轴线。")
    if next_shot:
        lines.append(next_shot_prompt(next_shot))
    continuity_note = continuity.get("spatial_continuity_note") or ""
    visual_bridge = continuity.get("visual_bridge") or "旁白关键词"
    transition_guidance = continuity.get("transition_guidance") or ""
    lines.append(f"画面连续性：{continuity_note}；视觉桥：{visual_bridge}；{transition_guidance}")
    return [line for line in lines if line.strip()]


def video_continuity_prompt_lines(
    shot: dict[str, Any],
    *,
    previous: dict[str, Any] | None,
    next_shot: dict[str, Any] | None,
) -> list[str]:
    continuity = shot.get("continuity") if isinstance(shot.get("continuity"), dict) else {}
    lines = [
        f"视频连续性：{continuity.get('spatial_continuity_note') or '保持主体外观、画风、光线和叙事情绪连续。'}",
        transition_prompt_for_shot(shot),
    ]
    if previous:
        lines.append(previous_shot_prompt(previous, shot))
    if next_shot:
        lines.append(next_shot_prompt(next_shot))
    return [line for line in lines if line.strip()]


def append_prompt_lines(base: str, lines: list[str]) -> str:
    clean_lines = [line.strip() for line in lines if line.strip()]
    if not clean_lines:
        return base
    return f"{base.rstrip()}\n\n" + "\n".join(clean_lines)


def missing_prompt_lines(base: str, lines: list[str]) -> list[str]:
    value = str(base or "")
    missing = []
    for line in lines:
        clean = line.strip()
        if not clean:
            continue
        prefix = clean.split("：", 1)[0] + "：" if "：" in clean else clean
        if prefix not in value:
            missing.append(clean)
    return missing


def sanitize_prompt_parts(prompt_parts: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(prompt_parts)
    for key in ("scene_prompt", "subject_prompt", "composition_prompt", "camera_prompt", "lighting_prompt"):
        if key in normalized:
            normalized[key] = strip_anchor_generation_task_text(str(normalized.get(key) or ""))
    return normalized


def ensure_keyframe_frame_size_prompt(prompt: str, shot: dict[str, Any]) -> str:
    if prompt_has_frame_size(prompt):
        return prompt
    prompt_parts = shot.get("prompt_parts") if isinstance(shot.get("prompt_parts"), dict) else {}
    frame_size_prompt = str(prompt_parts.get("frame_size_prompt") or DEFAULT_FRAME_SIZE_PROMPT).strip()
    return append_prompt_lines(prompt, [frame_size_prompt])


def ensure_frame_size_prompt_part(prompt_parts: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(prompt_parts)
    if not str(normalized.get("frame_size_prompt") or "").strip():
        normalized["frame_size_prompt"] = DEFAULT_FRAME_SIZE_PROMPT
    return normalized


def prompt_has_frame_size(prompt: str) -> bool:
    value = str(prompt or "")
    return "16:9 横版" in value and ("1280 * 720" in value or "1280x720" in value)


def production_plan_for_shot(shot: dict[str, Any]) -> dict[str, Any]:
    shot_type = shot.get("shot_type")
    if shot_type == "map_shot":
        return {
            "render_method": "map_animation",
            "cost_tier": "medium",
            "needs_keyframe": False,
            "needs_video": True,
            "recommended_tool": "map_api",
            "reason": "迁徙路线和地理关系用地图动画更清晰且成本可控。",
        }
    if shot_type in {"explainer_shot", "concept_shot"}:
        return {
            "render_method": "diagram",
            "cost_tier": "medium",
            "needs_keyframe": True,
            "needs_video": False,
            "recommended_tool": "editor_only",
            "reason": "抽象机制优先图解或轻动态图形，避免误导为真实历史场景。",
        }
    if shot_type == "key_comic_shot":
        return {
            "render_method": "image_to_video",
            "cost_tier": "high",
            "needs_keyframe": True,
            "needs_video": True,
            "recommended_tool": "video_model",
            "reason": "关键漫画镜头值得使用较高成本增强戏剧表现。",
        }
    if shot_type == "montage_shot":
        return {
            "render_method": "montage",
            "cost_tier": "medium",
            "needs_keyframe": True,
            "needs_video": False,
            "recommended_tool": "editor_only",
            "reason": "仪式与共同想象适合用多张静帧剪辑压缩表达。",
        }
    return {
        "render_method": "seedream_keyframe_only",
        "cost_tier": "low",
        "needs_keyframe": True,
        "needs_video": False,
        "recommended_tool": "seedream",
        "reason": "普通叙事镜头用关键帧加轻微推拉即可表达，控制成本。",
    }


def prompt_parts_for_shot(shot: dict[str, Any]) -> dict[str, str]:
    camera = shot.get("camera") or {}
    subject_text = "、".join(shot.get("subject_names") or []) or "按旁白呈现主体"
    scene_text = shot.get("scene_name") or "适合旁白内容的历史科普画面空间"
    return {
        "style_prompt": f"{DEFAULT_STYLE_POLICY['style_name']}，{DEFAULT_STYLE_POLICY['visual_style']}",
        "scene_prompt": scene_text,
        "subject_prompt": subject_text,
        "composition_prompt": shot.get("visual_goal") or shot.get("narration") or "",
        "camera_prompt": f"{camera.get('shot_size', 'medium wide shot')}，{camera.get('angle', 'eye level')}，{camera.get('movement', '缓慢推进')}",
        "lighting_prompt": "自然光或火光，保持历史科普插画质感。",
        "frame_size_prompt": DEFAULT_FRAME_SIZE_PROMPT,
        "negative_prompt": shot.get("negative_prompt") or DEFAULT_NEGATIVE_PROMPT,
    }


def should_add_supplemental_shot(shot_type: str, text: str) -> bool:
    return shot_type in {"map_shot", "explainer_shot", "concept_shot", "transition_shot"} and len(text) > 12


def supplemental_shot_type(shot_type: str) -> str:
    if shot_type == "explainer_shot":
        return "concept_shot"
    return shot_type


def supplemental_narration_for_shot(shot_type: str, text: str) -> str:
    if shot_type == "map_shot":
        return "补充地图动画：用路线和地理节点解释迁徙空间。"
    if shot_type in {"explainer_shot", "concept_shot"}:
        return "补充图解：把抽象机制拆成观众能看懂的视觉关系。"
    return "补充转场：用视觉桥接连接前后段落。"


def supplemental_visual_goal(shot_type: str, text: str) -> str:
    if shot_type == "map_shot":
        return "补一张迁徙路线地图，承接旁白中的地理移动。"
    if shot_type in {"explainer_shot", "concept_shot"}:
        return "补一张机制图解，降低抽象概念理解成本。"
    return "补一个短转场，避免时间或地点跳跃突兀。"


def supplemental_reason_for_shot(shot_type: str, text: str) -> str:
    if shot_type == "map_shot":
        return "原文包含地理迁徙，需要额外地图镜头帮助观众理解空间关系。"
    if shot_type in {"explainer_shot", "concept_shot"}:
        return "原文包含抽象机制，需要额外图解镜头帮助视觉化。"
    return "原文段落存在时间或地点跳转，需要额外转场镜头保持连续性。"


def supplemental_duration(shot_type: str) -> float:
    return 3.0 if shot_type in {"explainer_shot", "concept_shot"} else 4.0



def infer_shot_type(text: str, *, index: int) -> str:
    if has_any(text, ["红海", "索马里", "阿拉伯", "印度洋", "河口", "巽他", "澳大利亚", "迁徙路线"]):
        return "map_shot"
    if has_any(text, ["大脑", "能量", "产道", "早产儿"]):
        return "explainer_shot"
    if has_any(text, ["黎凡特", "尼安德特"]):
        return "comparison_shot"
    if has_any(text, ["布隆伯斯", "贝壳", "赭石"]):
        return "key_comic_shot"
    if has_any(text, ["多巴火山", "火山灰", "气温骤降"]):
        return "transition_shot"
    if has_any(text, ["葬礼", "壁画", "仪式"]):
        return "montage_shot"
    if has_any(text, ["股票市场", "CPU", "汽车油箱", "比喻"]):
        return "concept_shot"
    if index == 1 or has_any(text, ["草原", "狮子", "鬣狗", "开场"]):
        return "hook_shot" if index <= 2 else "narrative_shot"
    if has_any(text, ["火", "烹饪", "篝火"]):
        return "narrative_shot"
    return "narrative_shot"


def normalize_shot_type(value: Any) -> str:
    shot_type = str(value or "").strip()
    allowed = {
        "hook_shot",
        "narrative_shot",
        "explainer_shot",
        "comparison_shot",
        "map_shot",
        "concept_shot",
        "transition_shot",
        "montage_shot",
        "key_comic_shot",
        "asset_shot",
        "supplemental_visual_shot",
    }
    return shot_type if shot_type in allowed else "narrative_shot"


def match_subjects(text: str, subjects: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    matches: list[dict[str, Any]] = []

    def add_by_names(names: list[str]) -> None:
        for subject in subjects:
            canonical = str(subject.get("canonical_name") or subject.get("name") or "")
            if canonical in names and subject not in matches:
                matches.append(subject)

    if has_any(text, ["尼安德特"]):
        add_by_names(["尼安德特人"])
    if has_any(text, ["直立人"]):
        add_by_names(["直立人"])
    if has_any(text, ["丹尼索瓦"]):
        add_by_names(["丹尼索瓦人"])
    if has_any(text, ["早期智人群体", "部落", "群体", "围着火光"]):
        add_by_names(["早期智人群体", "智人"])
    elif has_any(text, ["智人", "人类", "我们"]):
        add_by_names(["智人", "早期智人群体"])
    return (
        [str(subject.get("subject_id")) for subject in matches if subject.get("subject_id")],
        [str(subject.get("canonical_name") or subject.get("name")) for subject in matches if subject.get("canonical_name") or subject.get("name")],
    )


def match_scene(text: str, scenes: list[dict[str, Any]], *, shot_type: str) -> tuple[str, str]:
    mapping = [
        (["稀树草原", "非洲东部"], "东非稀树草原"),
        (["部落", "营地"], "非洲智人部落营地"),
        (["篝火", "烹饪", "火光", "围坐"], "篝火烹饪营地"),
        (["布隆伯斯"], "布隆伯斯洞穴"),
        (["黎凡特", "尼安德特"], "黎凡特冰河期遭遇地带"),
        (["多巴火山", "火山灰"], "多巴火山灾变"),
        (["红海", "索马里", "阿拉伯"], "红海海口迁徙渡口"),
        (["印度洋", "印度河", "恒河", "湄公河", "河口"], "印度洋海岸迁徙路线"),
        (["巽他", "澳大利亚", "巴布亚"], "巽他大陆尽头海峡"),
        (["洞穴壁画", "葬礼", "仪式"], "洞穴壁画与葬礼仪式空间"),
    ]
    for markers, scene_name in mapping:
        if has_any(text, markers):
            for scene in scenes:
                canonical = str(scene.get("canonical_name") or scene.get("name") or "")
                if canonical == scene_name:
                    return str(scene.get("scene_id") or ""), canonical
    if shot_type in {"map_shot", "explainer_shot", "concept_shot"}:
        return "", ""
    return "", ""


def build_reference_assets(
    *,
    subject_ids: list[str],
    scene_id: str,
    subject_lookup: dict[str, dict[str, Any]],
    scene_lookup: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "subject_anchors": [
            {
                "subject_id": subject_id,
                "name": subject_lookup.get(subject_id, {}).get("name", ""),
                "asset_url": subject_lookup.get(subject_id, {}).get("asset_url", ""),
                "visual_prompt": subject_lookup.get(subject_id, {}).get("visual_prompt", ""),
            }
            for subject_id in subject_ids
        ],
        "scene_anchors": [
            {
                "scene_id": scene_id,
                "name": scene_lookup.get(scene_id, {}).get("name", ""),
                "asset_url": scene_lookup.get(scene_id, {}).get("asset_url", ""),
                "visual_prompt": scene_lookup.get(scene_id, {}).get("visual_prompt", ""),
            }
        ]
        if scene_id
        else [],
    }


def compose_keyframe_prompt(shot: dict[str, Any], *, style_policy: dict[str, Any] | None = None) -> str:
    prompt_parts = shot.get("prompt_parts") if isinstance(shot.get("prompt_parts"), dict) else {}
    if prompt_parts:
        return (
            f"{prompt_parts.get('style_prompt', '')}。"
            f"场景：{prompt_parts.get('scene_prompt', '')}。"
            f"主体：{prompt_parts.get('subject_prompt', '')}。"
            f"构图：{prompt_parts.get('composition_prompt', '')}。"
            f"镜头：{prompt_parts.get('camera_prompt', '')}。"
            f"光线：{prompt_parts.get('lighting_prompt', '')}。"
            f"{prompt_parts.get('frame_size_prompt') or DEFAULT_FRAME_SIZE_PROMPT}"
            "这是 Seedream 关键帧首帧图 prompt，只描述单张画面。"
        )
    style = style_policy or DEFAULT_STYLE_POLICY
    subject_text = "、".join(shot.get("subject_names") or []) or "画面主体按旁白内容呈现"
    scene_text = shot.get("scene_name") or "适合旁白内容的解释性画面空间"
    elements = "、".join(shot.get("visual_elements") or [])
    camera = shot.get("camera") or {}
    asset_prompts = reference_prompt_text(shot.get("reference_assets") or {})
    return (
        f"{style.get('style_name', '历史科普卡通短剧风格')}，{style.get('visual_style', '')}"
        f"{scene_text}。画面主体：{subject_text}。"
        f"旁白对应画面：{shot.get('visual_goal') or shot.get('narration') or ''}。"
        f"{'画面元素：' + elements + '。' if elements else ''}"
        f"{asset_prompts}"
        f"构图：{camera.get('shot_size', 'medium wide shot')}，{camera.get('angle', 'eye level')}。"
        f"{DEFAULT_FRAME_SIZE_PROMPT}"
        "这是 Seedream 关键帧首帧图 prompt，只描述单张画面。"
        f"{style.get('era_guardrail', '')}"
    )


def compose_video_prompt(shot: dict[str, Any]) -> str:
    duration = safe_float(shot.get("duration_sec"), default=4.0)
    camera = shot.get("camera") or {}
    movement = camera.get("movement") or "slow push in"
    return (
        f"基于首帧，镜头{movement}，画面中的尘土、火光、水面或人物姿态产生轻微自然运动。"
        f"保持主体外观、场景空间、半扁平历史科普卡通风格不变化，持续{duration:g}秒。"
    )


def compact_reference_visual_prompt(prompt: Any, *, kind: str) -> str:
    text = str(prompt or "").strip()
    if not text:
        return ""
    details = extract_anchor_reference_details(text, kind=kind)
    if details:
        return "；".join(details)
    clean = strip_anchor_generation_task_text(text)
    clean = re.sub(r"\s+", " ", clean).strip(" ，。；")
    return preview_text(clean, limit=180)


def extract_anchor_reference_details(text: str, *, kind: str) -> list[str]:
    if kind == "scene":
        labels = ["时代", "地区", "地形", "天气", "光线", "色彩", "氛围", "典型元素", "必须保持"]
    else:
        labels = ["时代", "地区", "外观", "服饰", "道具", "身体语言", "群体构成", "必须保持"]
    details = []
    for label in labels:
        match = re.search(rf"^\s*-\s*{re.escape(label)}：(.+?)\s*$", text, flags=re.M)
        if not match:
            continue
        value = match.group(1).strip(" ，。；")
        if value:
            details.append(f"{label}：{value}")
    return details


def strip_anchor_generation_task_text(text: str) -> str:
    value = str(text or "")
    if not value:
        return ""
    value = re.sub(
        r"请生成一张[“\"]主体锚点图[”\"].*?构图干净，竖屏短视频资产友好，主体占画面主要位置，背景不能抢戏。*",
        "",
        value,
        flags=re.S,
    )
    value = re.sub(
        r"请生成一张[“\"]场景锚点图[”\"].*?不要生成单个道具特写，例如火、贝壳、弓箭、骨针、船。*",
        "",
        value,
        flags=re.S,
    )
    return re.sub(r"\n{3,}", "\n\n", value).strip(" ，。；\n")


def reference_prompt_text(reference_assets: dict[str, Any]) -> str:
    parts: list[str] = []
    for subject in reference_assets.get("subject_anchors") or []:
        visual_prompt = compact_reference_visual_prompt(subject.get("visual_prompt", ""), kind="subject")
        if visual_prompt:
            parts.append(f"参考主体一致性：{subject.get('name')}，{visual_prompt}")
    for scene in reference_assets.get("scene_anchors") or []:
        visual_prompt = compact_reference_visual_prompt(scene.get("visual_prompt", ""), kind="scene")
        if visual_prompt:
            parts.append(f"参考场景一致性：{scene.get('name')}，{visual_prompt}")
    return "。".join(parts) + ("。" if parts else "")


def visual_goal_for_shot(text: str, shot_type: str) -> str:
    if shot_type == "narrative_shot":
        return narrative_visual_goal_for_text(text)
    labels = {
        "hook_shot": "建立开场钩子和生存压力。",
        "explainer_shot": "把抽象知识点转成可理解的图解画面。",
        "comparison_shot": "对比不同古人类主体和环境处境。",
        "map_shot": "呈现迁徙方向、地理边界和空间关系。",
        "concept_shot": "把现代比喻或抽象概念转成不误导古史画面的图解。",
        "transition_shot": "完成时间、地点或灾变状态的转场。",
        "montage_shot": "用连续画面压缩展示仪式和共同想象。",
        "key_comic_shot": "突出一个关键漫画式历史画面。",
    }
    return f"{labels.get(shot_type, '推进旁白叙事。')}旁白：{preview_text(text, limit=58)}"


def narrative_visual_goal_for_text(text: str) -> str:
    clean = str(text or "").strip()
    if has_any(clean, ["没有尖牙", "跑得", "力气不如", "自助餐", "狮子", "鬣狗"]):
        return f"强化生存劣势：用捕食者、速度或体型对比表现早期智人的脆弱处境。旁白：{preview_text(clean, limit=58)}"
    if has_any(clean, ["学名叫", "名字叫", "叫智人"]):
        return f"完成身份揭示：在上一帧弱小动物印象上叠加智人身份认知，画面焦点从处境转向主体名称。旁白：{preview_text(clean, limit=58)}"
    if has_any(clean, ["别被这个名字骗了", "不是某种", "和我们无关", "远古怪物"]):
        return f"纠正误解：让远古智人与现代观众产生视觉关联，说明他们不是无关怪物而是人类谱系。旁白：{preview_text(clean, limit=58)}"
    if has_any(clean, ["早期的我们", "就是我们", "现代人"]):
        return f"建立观众连接：从远古个体过渡到现代人轮廓或眼神呼应，让观众意识到这是我们的早期阶段。旁白：{preview_text(clean, limit=58)}"
    if has_any(clean, ["部落", "孩子", "工具", "分配食物", "协作"]):
        return f"展示群体协作：用分工动作、照料孩子或工具制作推动智人从个体脆弱转向集体合作。旁白：{preview_text(clean, limit=58)}"
    if has_any(clean, ["火光", "讲故事", "故事", "虚构"]):
        return f"表现共同想象：用篝火、讲述手势和听众视线表现故事如何组织群体。旁白：{preview_text(clean, limit=58)}"
    return f"推进当前旁白的具体画面变化：让动作、视线、道具或构图焦点相对上一帧发生清晰变化。旁白：{preview_text(clean, limit=58)}"


def is_generic_visual_goal(value: str) -> bool:
    clean = str(value or "").strip(" 。；，")
    return clean in {"", "推进旁白叙事", "推进叙事", "旁白驱动", "解释旁白", "呈现旁白"}


def visual_elements_for_text(text: str) -> list[str]:
    elements = []
    for marker in PROP_MARKERS + ["狮子", "鬣狗", "稀树", "尘土", "火山灰", "红花", "岩壁", "壁画"]:
        if marker in text and marker not in elements:
            elements.append(marker)
    return elements


def duration_for_shot_type(shot_type: str, text: str) -> float:
    if shot_type in {"explainer_shot", "concept_shot"}:
        return 3.0
    if shot_type == "map_shot":
        return 5.0
    if shot_type in {"key_comic_shot", "hook_shot"}:
        return 4.5
    if shot_type == "montage_shot":
        return 3.5
    if len(text) > 70:
        return 5.0
    return 4.0


def camera_for_shot_type(shot_type: str) -> dict[str, str]:
    if shot_type == "map_shot":
        return {"shot_size": "wide map shot", "angle": "top down oblique angle", "movement": "缓慢横移"}
    if shot_type == "explainer_shot":
        return {"shot_size": "medium infographic shot", "angle": "front angle", "movement": "轻微推进"}
    if shot_type == "key_comic_shot":
        return {"shot_size": "medium close shot", "angle": "slightly low angle", "movement": "轻微推进"}
    if shot_type == "hook_shot":
        return {"shot_size": "wide shot", "angle": "slightly high angle", "movement": "缓慢推进"}
    return {"shot_size": "medium wide shot", "angle": "eye level", "movement": "缓慢推进"}


def asset_status_for_reference_assets(reference_assets: dict[str, Any]) -> str:
    anchors = (reference_assets.get("subject_anchors") or []) + (reference_assets.get("scene_anchors") or [])
    if anchors and any(not anchor.get("asset_url") for anchor in anchors):
        return "missing_reference_asset"
    return "missing_keyframe"


def fact_safety_note_for_text(text: str) -> str:
    if has_any(text, ["狮子", "鬣狗"]):
        return "动物威胁是生存压力的画面化表达，不能写成确定追捕事件。"
    if has_any(text, ["多巴火山", "全球气温骤降"]):
        return "多巴火山影响范围和强度需要保留学术不确定性。"
    if has_any(text, ["股票市场", "CPU", "汽车油箱"]):
        return "现代对象只能作为概念图解或比喻，不应进入远古真实场景。"
    return ""


def needs_manual_review(text: str, *, scene_id: str, subject_ids: list[str], shot_type: str) -> bool:
    if has_any(text, ["股票市场", "CPU", "汽车油箱"]):
        return True
    if shot_type not in {"map_shot", "explainer_shot", "concept_shot"} and mentions_known_subject(text) and not subject_ids:
        return True
    if needs_scene_binding(text, shot_type) and not scene_id:
        return True
    return False


def mentions_known_subject(text: str) -> bool:
    return has_any(text, ["智人", "人类", "尼安德特", "直立人", "丹尼索瓦"])


def needs_scene_binding(text: str, shot_type: str) -> bool:
    if shot_type in {"map_shot", "explainer_shot", "concept_shot"}:
        return False
    return has_any(text, ["草原", "营地", "洞穴", "黎凡特", "火山", "葬礼", "壁画", "篝火"])


def suggested_scene_name(text: str) -> str:
    if "草原" in text:
        return "东非稀树草原"
    if "洞穴" in text:
        return "洞穴空间"
    if "营地" in text:
        return "智人营地"
    return "待补场景"


def add_missing_candidate(candidates: list[dict[str, str]], name: str, reason: str) -> None:
    if not any(candidate.get("name") == name for candidate in candidates):
        candidates.append({"name": name, "reason": reason})


def normalize_candidate_list(value: Any) -> list[dict[str, str]]:
    candidates = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("canonical_name") or "").strip()
        reason = str(item.get("reason") or item.get("note") or "").strip()
        if name:
            candidates.append({"name": name, "reason": reason})
    return candidates


def has_any(text: str, markers: list[str]) -> bool:
    return any(marker in str(text or "") for marker in markers)


def safe_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def preview_text(text: str, *, limit: int) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    return clean[:limit]


def new_storyboard_id() -> str:
    return f"sb-{uuid4().hex}"
