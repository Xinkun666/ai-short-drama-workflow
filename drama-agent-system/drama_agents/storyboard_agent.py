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
            raw = fallback_storyboard_for_provider_output_error(payload, exc)
        return normalize_storyboard_payload(
            raw,
            generation=generation,
            subjects=subjects or [],
            scenes=scenes or [],
            target_duration_sec=payload.get("target_duration_sec"),
            source_type=source_type,
            source_filename=source_filename,
        )


class RuleBasedStoryboardProvider:
    def generate_storyboard(self, payload: dict[str, Any]) -> dict[str, Any]:
        article = str(payload.get("full_script") or "")
        units = split_article_into_narration_units(article)
        subjects = payload.get("provided_subjects") or []
        scenes = payload.get("provided_scenes") or []
        subject_lookup = {subject.get("subject_id"): subject for subject in subjects if subject.get("subject_id")}
        scene_lookup = {scene.get("scene_id"): scene for scene in scenes if scene.get("scene_id")}
        target_duration = safe_int(payload.get("target_duration_sec"), default=0)
        shots: list[dict[str, Any]] = []
        missing_subjects: list[dict[str, str]] = []
        missing_scenes: list[dict[str, str]] = []

        for index, narration in enumerate(units, start=1):
            shot_type = infer_shot_type(narration, index=index)
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
                "shot_index": index,
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
            }
            shot["keyframe_prompt"] = compose_keyframe_prompt(shot, style_policy=payload.get("visual_style_policy") or {})
            shot["video_prompt"] = compose_video_prompt(shot)
            shots.append(shot)

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
        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是 AI 历史科普短剧分镜导演。你不是剧本摘要员，也不是剧本改写员。"
                        "你只输出合法 JSON，不要 Markdown 代码围栏。"
                    ),
                },
                {"role": "user", "content": build_storyboard_prompt(payload)},
            ],
            "temperature": 0.18,
            "max_tokens": int(os.environ.get("STORYBOARD_MAX_TOKENS", "14000")),
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
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"DeepSeek 连接失败：{exc}") from exc
        content = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        return parse_json_object(content)


def fallback_storyboard_for_provider_output_error(payload: dict[str, Any], exc: Exception) -> dict[str, Any]:
    raw = RuleBasedStoryboardProvider().generate_storyboard(payload)
    board = raw.get("storyboard") if isinstance(raw.get("storyboard"), dict) else {}
    notes = board.get("review_notes") if isinstance(board.get("review_notes"), list) else []
    notes.append(f"分镜模型返回的 JSON 无法解析，已使用本地规则分镜兜底：{preview_text(str(exc), limit=120)}")
    board["review_notes"] = notes
    board["status"] = "needs_review"
    raw["storyboard"] = board
    return raw


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
        "visual_prompt": subject.get("visual_prompt", ""),
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
        "visual_prompt": scene.get("visual_prompt", ""),
        "negative_prompt": scene.get("negative_prompt", ""),
    }


def build_storyboard_prompt(payload: dict[str, Any]) -> str:
    target_duration = safe_int(payload.get("target_duration_sec"), default=0)
    prompt_payload = {
        "full_script": payload.get("full_script", ""),
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
        "你是 AI 历史科普短剧分镜导演。\n"
        "你不是剧本摘要员，不是剧本改写员。你要把完整剧本拆成可生产的镜头卡。\n"
        "硬性规则：不要简化剧本；不要删掉重要内容；不要只输出大纲；尽量覆盖完整旁白；"
        "如需补充，只补充解释镜头、地图镜头、过渡镜头或概念可视化镜头，且必须标注 supplemental。"
        "优先绑定 provided_subjects 和 provided_scenes。不要把道具、动物、现代比喻或抽象概念塞进 subject_ids 或 scene_id。"
        "根据完整剧本内容决定镜头数量和每镜时长；没有显式目标时长时，不要为了凑固定秒数压缩或拉长内容。"
        "每个镜头都必须包含 narration、subtitle_text、shot_type、visual_goal、subject_ids、scene_id、"
        "reference_assets、camera、duration_sec、keyframe_prompt、video_prompt、negative_prompt、fact_safety_note、needs_manual_review、asset_status。\n"
        "只输出严格 JSON，格式为 {\"storyboard\": {\"title\": \"...\", \"target_duration_sec\": 96, "
        "\"actual_duration_sec\": 96, \"style_policy\": {}, \"missing_subject_candidates\": [], "
        "\"missing_scene_candidates\": [], \"review_notes\": [], \"shots\": []}}。\n\n"
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
    subject_lookup = {subject.get("subject_id"): reference_subject_payload(subject) for subject in subjects if subject.get("subject_id")}
    scene_lookup = {scene.get("scene_id"): reference_scene_payload(scene) for scene in scenes if scene.get("scene_id")}
    raw_shots = board.get("shots") if isinstance(board.get("shots"), list) else []
    normalized_shots = [
        normalize_shot_payload(
            shot,
            index=index,
            subject_lookup=subject_lookup,
            scene_lookup=scene_lookup,
        )
        for index, shot in enumerate(raw_shots, start=1)
        if isinstance(shot, dict)
    ]
    actual_duration = sum(float(shot["duration_sec"]) for shot in normalized_shots)
    requested_target_duration = safe_int(target_duration_sec, default=0)
    board_target_duration = safe_int(board.get("target_duration_sec"), default=0)
    resolved_target_duration = (
        board_target_duration
        if board_target_duration > 0
        else requested_target_duration
        if requested_target_duration > 0
        else round(actual_duration)
    )
    status = "needs_review" if any(shot["needs_manual_review"] for shot in normalized_shots) else "completed"
    title = str(board.get("title") or f"{script.get('title') or generation.get('topic') or '剧本'} 分镜").strip()
    return {
        "storyboard_id": str(board.get("storyboard_id") or ""),
        "generation_id": generation.get("generation_id", ""),
        "title": title,
        "source_type": source_type,
        "source_filename": source_filename,
        "status": str(board.get("status") or status),
        "target_duration_sec": int(resolved_target_duration),
        "actual_duration_sec": float(board.get("actual_duration_sec") or actual_duration),
        "shot_count": len(normalized_shots),
        "style_policy": board.get("style_policy") if isinstance(board.get("style_policy"), dict) else DEFAULT_STYLE_POLICY,
        "missing_subject_candidates": normalize_candidate_list(board.get("missing_subject_candidates")),
        "missing_scene_candidates": normalize_candidate_list(board.get("missing_scene_candidates")),
        "review_notes": [str(item) for item in board.get("review_notes") or [] if str(item).strip()],
        "shots": normalized_shots,
        "raw": board,
    }


def normalize_shot_payload(
    shot: dict[str, Any],
    *,
    index: int,
    subject_lookup: dict[str, dict[str, Any]],
    scene_lookup: dict[str, dict[str, Any]],
) -> dict[str, Any]:
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
    duration_sec = safe_float(shot.get("duration_sec"), default=4.0)
    normalized = {
        "shot_id": str(shot.get("shot_id") or ""),
        "shot_index": int(shot.get("shot_index") or index),
        "narration": str(shot.get("narration") or "").strip(),
        "subtitle_text": str(shot.get("subtitle_text") or shot.get("narration") or "").strip(),
        "shot_type": normalize_shot_type(shot.get("shot_type")),
        "visual_goal": str(shot.get("visual_goal") or "").strip(),
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
        "raw": shot,
    }
    if not normalized["visual_goal"]:
        normalized["visual_goal"] = visual_goal_for_shot(normalized["narration"], normalized["shot_type"])
    if not normalized["keyframe_prompt"]:
        normalized["keyframe_prompt"] = compose_keyframe_prompt(normalized)
    if not normalized["video_prompt"]:
        normalized["video_prompt"] = compose_video_prompt(normalized)
    return normalized


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


def reference_prompt_text(reference_assets: dict[str, Any]) -> str:
    parts: list[str] = []
    for subject in reference_assets.get("subject_anchors") or []:
        if subject.get("visual_prompt"):
            parts.append(f"参考主体设定：{subject.get('name')}，{subject.get('visual_prompt')}")
    for scene in reference_assets.get("scene_anchors") or []:
        if scene.get("visual_prompt"):
            parts.append(f"参考场景设定：{scene.get('name')}，{scene.get('visual_prompt')}")
    return "。".join(parts) + ("。" if parts else "")


def visual_goal_for_shot(text: str, shot_type: str) -> str:
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
