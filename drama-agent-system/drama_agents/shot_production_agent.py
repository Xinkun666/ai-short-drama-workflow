from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from drama_agents.chapter_refiner import parse_json_object
from drama_agents.scene_reconstruction_agent import SceneReconstructionPipeline, static_frame_from_visual_text
from drama_agents.storyboard_agent import DEFAULT_FRAME_SIZE_PROMPT, DEFAULT_NEGATIVE_PROMPT, DEFAULT_STYLE_POLICY


SHOT_PLANNER_SYSTEM_PROMPT = """
你是历史科普短剧的镜头规划 Agent。

你的任务：
把一个场景级分镜 scene 拆解成多个可执行的镜头 shot plan。

你只负责拆镜头，不写最终 AI 绘图提示词。
你必须输出严格合法 JSON object，不要 Markdown，不要解释文字。

核心规则：
1. 每个 shot 只表达一个主要视觉信息点。
2. 不要把一个 scene 的所有画面元素塞进一个 shot。
3. 不要把所有 screen_text 全量复制到每个 shot。
4. 不要把所有 must_keep_points 全量复制到每个 shot。
5. 画面演绎中的多个动作、地点、时间跳转、蒙太奇元素，必须拆成多个 shot。
6. HOST_OPENING 应包含讲述人/黑板/屏幕解释镜头。
7. SYMBOLIC_MONTAGE 应拆成多个象征镜头或明确一个 montage keyframe，但不能杂乱堆叠。
8. INFOGRAPHIC 应拆成数据屏、对比图、解释图。
9. COMPARISON_SPLIT_SCREEN 应拆成左右对比或融合过渡镜头。
10. HISTORICAL_REENACTMENT 应拆成历史再现镜头，并避免现代物品。
11. shot 数量应匹配时长：15秒以内 2-4 镜，20-30秒 4-6 镜，30-45秒 5-8 镜。
12. 保留支撑点必须映射到具体 shot。
13. 输出中的 start_sec/end_sec 要覆盖整个 scene duration，不能明显重叠或漏段。
""".strip()


KEYFRAME_PROMPT_SYSTEM_PROMPT = """
你是历史科普短剧的关键帧提示词 Agent。

你的任务：
根据已经规划好的 shot plan，为每个 shot 生成 AI 关键帧绘图提示词。

你不能重新拆镜头。
你不能改变 shot_id、start_sec、end_sec、shot_type、purpose。
你只能补充 keyframe_prompt、negative_prompt、quality_notes。
你必须输出严格合法 JSON object，不要 Markdown，不要解释文字。

固定视觉风格：
历史科普卡通短剧风格，半扁平漫画插画，不低幼，不是写实电影，也不是过度幼稚的扁平小人。普通镜头优先使用地图、地貌、半扁平卡通人物、群体剪影、信息图、黑板、字幕、箭头、少量道具。关键镜头可以更有漫画冲击力，但仍保持科普短剧统一风格。

每个 keyframe_prompt 必须包含：
1. 画面用途
2. 时代地点
3. 画面主体
4. 环境背景
5. 静态画面
6. 构图
7. 镜头语言
8. 情绪氛围
9. 屏幕文字
10. 必须体现的信息
11. 单张关键帧约束

强制写入：
“这是单张关键帧，只描述一个清晰瞬间，不要把多个独立事件硬塞进同一画面。”

关键帧只描述一帧静态画面，禁止把镜头推进、快闪、缩回、转场、逐渐变化等视频运动写进 keyframe_prompt。
视频运动只属于 shot plan 或 video_prompt，不属于关键帧绘图提示词。

negative_prompt 必须包含：
写实照片、电影剧照、过度低幼、3D塑料感、现代建筑、现代服装、现代载具、血腥暴力、恐怖画面、文字乱码、水印、logo。

如果是远古史 / 历史再现镜头：
额外避免现代城市、现代机器、现代武器、科幻界面。

如果是地图 / 信息图镜头：
额外避免复杂人物抢主体、真实卫星照片风格、过密文字。
""".strip()


BASE_NEGATIVE_PROMPT = (
    "写实照片、电影剧照、过度低幼、3D塑料感、现代建筑、现代服装、现代载具、"
    "血腥暴力、恐怖画面、文字乱码、水印、logo"
)


class ShotProductionAgent:
    def __init__(self, shot_planner_provider=None, keyframe_prompt_provider=None, scene_reconstruction_pipeline=None):
        self.scene_reconstruction_pipeline = scene_reconstruction_pipeline
        if self.scene_reconstruction_pipeline is None and shot_planner_provider is None and keyframe_prompt_provider is None:
            self.scene_reconstruction_pipeline = SceneReconstructionPipeline()
        self.shot_planner_provider = shot_planner_provider or RuleBasedShotPlannerProvider()
        self.keyframe_prompt_provider = keyframe_prompt_provider or RuleBasedKeyframePromptProvider()

    @classmethod
    def from_environment(cls):
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            return cls()
        return cls(scene_reconstruction_pipeline=SceneReconstructionPipeline.from_environment())

    def generate(
        self,
        *,
        generation: dict[str, Any],
        storyboard_script: dict[str, Any],
        subjects: list[dict[str, Any]] | None = None,
        scenes: list[dict[str, Any]] | None = None,
        source_filename: str = "",
        output_dir: str | os.PathLike[str] | None = None,
    ) -> dict[str, Any]:
        if self.scene_reconstruction_pipeline is not None:
            return self.scene_reconstruction_pipeline.generate(
                generation=generation,
                storyboard_script=storyboard_script,
                source_filename=source_filename,
                output_dir=output_dir,
            )
        scene_script = [scene for scene in storyboard_script.get("scene_script") or [] if isinstance(scene, dict)]
        if not scene_script:
            raise RuntimeError("当前剧本缺少标准镜头生产稿 scene_script，无法生成镜头生产结构。")

        all_shots: list[dict[str, Any]] = []
        scene_blocks: list[dict[str, Any]] = []
        review_notes: list[str] = []
        for scene_index, scene in enumerate(scene_script, start=1):
            plan = normalize_scene_plan(
                self.shot_planner_provider.plan_scene(scene),
                scene=scene,
                scene_index=scene_index,
            )
            keyframes = normalize_keyframe_payload(
                self.keyframe_prompt_provider.generate_keyframes(scene=scene, scene_plan=plan),
                scene_plan=plan,
            )
            shot_lookup = {shot["shot_id"]: shot for shot in plan["shots"]}
            for item in keyframes["shots"]:
                shot_lookup[item["shot_id"]].update(
                    {
                        "keyframe_prompt": item["keyframe_prompt"],
                        "negative_prompt": item["negative_prompt"],
                        "quality_notes": item.get("quality_notes") or [],
                    }
                )
            scene_shots = [
                production_shot_payload(
                    scene=scene,
                    scene_index=scene_index,
                    shot=shot,
                    shot_index=len(all_shots) + index,
                )
                for index, shot in enumerate(plan["shots"], start=1)
            ]
            link_neighbor_shots(scene_shots)
            all_shots.extend(scene_shots)
            scene_blocks.append(scene_block_payload(scene, scene_index=scene_index, shots=scene_shots))
            if plan.get("review_notes"):
                review_notes.extend(str(note) for note in plan["review_notes"] if str(note).strip())

        actual_duration = round(sum(float(shot.get("duration_sec") or 0) for shot in all_shots), 1)
        title = str(storyboard_script.get("title") or generation.get("topic") or "镜头生产稿").strip()
        return {
            "generation_id": generation.get("generation_id", ""),
            "title": f"{title} - 镜头生产结构",
            "source_type": "standard_storyboard_script",
            "source_filename": source_filename,
            "status": "completed" if all_shots else "needs_review",
            "target_duration_sec": int(round(actual_duration)),
            "actual_duration_sec": actual_duration,
            "shot_count": len(all_shots),
            "style_policy": DEFAULT_STYLE_POLICY,
            "missing_subject_candidates": [],
            "missing_scene_candidates": [],
            "review_notes": dedupe(review_notes),
            "coverage": {
                "source_scene_count": len(scene_script),
                "planned_scene_count": len(scene_blocks),
                "planned_shot_count": len(all_shots),
                "coverage_ratio": 1.0 if scene_script else 0.0,
            },
            "script_feedback": [],
            "scene_blocks": scene_blocks,
            "shots": all_shots,
            "raw": {
                "format": "shot_production_structure",
                "source_storyboard_format": storyboard_script.get("format") or "",
                "scene_blocks": scene_blocks,
                "agent_systems": {
                    "shot_planner": "deepseek_v4_pro_shot_planner" if isinstance(self.shot_planner_provider, DeepSeekShotPlannerProvider) else "rule_based_shot_planner",
                    "keyframe_prompt": "deepseek_v4_pro_keyframe_prompt" if isinstance(self.keyframe_prompt_provider, DeepSeekKeyframePromptProvider) else "rule_based_keyframe_prompt",
                },
            },
        }


class DeepSeekShotPlannerProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str | None = None,
        base_url: str = "https://api.deepseek.com/chat/completions",
        timeout: int | None = None,
    ):
        self.api_key = api_key
        self.model = model or os.environ.get("DEEPSEEK_SHOT_PLANNER_MODEL") or "deepseek-v4-pro"
        self.base_url = base_url
        self.timeout = timeout or int(os.environ.get("DEEPSEEK_TIMEOUT", "240"))

    def plan_scene(self, scene: dict[str, Any]) -> dict[str, Any]:
        return post_deepseek_json(
            api_key=self.api_key,
            model=self.model,
            base_url=self.base_url,
            timeout=self.timeout,
            system_prompt=SHOT_PLANNER_SYSTEM_PROMPT,
            user_payload={
                "task": "把这个标准分镜 scene 拆解为可执行 shot plan。",
                "output_schema": {
                    "scene_id": "...",
                    "title": "...",
                    "duration_sec": 25,
                    "shots": [
                        {
                            "shot_id": "S01-SH01",
                            "start_sec": 0,
                            "end_sec": 4,
                            "shot_type": "...",
                            "purpose": "...",
                            "visual_focus": "...",
                            "subjects": [],
                            "environment": "...",
                            "action": "...",
                            "composition": "...",
                            "camera": "...",
                            "mood": "...",
                            "screen_text": [],
                            "must_keep_points": [],
                            "continuity_refs": [],
                        }
                    ],
                },
                "scene": scene,
            },
        )


class DeepSeekKeyframePromptProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str | None = None,
        base_url: str = "https://api.deepseek.com/chat/completions",
        timeout: int | None = None,
    ):
        self.api_key = api_key
        self.model = model or os.environ.get("DEEPSEEK_KEYFRAME_PROMPT_MODEL") or "deepseek-v4-pro"
        self.base_url = base_url
        self.timeout = timeout or int(os.environ.get("DEEPSEEK_TIMEOUT", "240"))

    def generate_keyframes(self, *, scene: dict[str, Any], scene_plan: dict[str, Any]) -> dict[str, Any]:
        return post_deepseek_json(
            api_key=self.api_key,
            model=self.model,
            base_url=self.base_url,
            timeout=self.timeout,
            system_prompt=KEYFRAME_PROMPT_SYSTEM_PROMPT,
            user_payload={
                "task": "根据 shot plan 为每个 shot 生成关键帧绘图提示词。",
                "scene": scene,
                "scene_plan": scene_plan,
                "output_schema": {
                    "scene_id": scene_plan.get("scene_id") or scene.get("scene_id") or "",
                    "shots": [
                        {
                            "shot_id": "...",
                            "keyframe_prompt": "...",
                            "negative_prompt": "...",
                            "quality_notes": [],
                        }
                    ],
                },
            },
        )


class RuleBasedShotPlannerProvider:
    def plan_scene(self, scene: dict[str, Any]) -> dict[str, Any]:
        scene_id = str(scene.get("scene_id") or "S01")
        title = str(scene.get("scene_title") or scene.get("title") or "未命名镜头")
        duration = safe_int(scene.get("duration_sec"), default=25)
        visual_beats = visual_beats_for_scene(scene)
        desired_count = shot_count_for_scene(scene, visual_beats)
        beats = fit_beats_to_count(visual_beats, desired_count, scene=scene)
        ranges = time_ranges(duration, len(beats))
        screen_text = normalize_string_list(scene.get("screen_text"))
        must_keep = normalize_string_list((scene.get("knowledge_payload") or {}).get("must_keep_details"))
        shots = []
        for index, beat in enumerate(beats, start=1):
            start_sec, end_sec = ranges[index - 1]
            shot_type = infer_production_shot_type(scene, beat, index=index)
            shots.append(
                {
                    "shot_id": f"{scene_id}-SH{index:02d}",
                    "start_sec": start_sec,
                    "end_sec": end_sec,
                    "shot_type": shot_type,
                    "purpose": purpose_for_beat(scene, beat, index=index),
                    "visual_focus": beat,
                    "subjects": subjects_for_text(beat),
                    "environment": environment_for_text(beat),
                    "action": action_for_text(beat),
                    "composition": composition_for_shot_type(shot_type),
                    "camera": camera_for_shot_type(shot_type),
                    "mood": mood_for_text(beat),
                    "screen_text": distribute_items(screen_text, index=index, count=len(beats)),
                    "must_keep_points": distribute_items(must_keep, index=index, count=len(beats)),
                    "continuity_refs": continuity_refs_for_index(scene_id, index),
                }
            )
        return {
            "scene_id": scene_id,
            "title": title,
            "duration_sec": duration,
            "shots": shots,
            "review_notes": [],
        }


class RuleBasedKeyframePromptProvider:
    def generate_keyframes(self, *, scene: dict[str, Any], scene_plan: dict[str, Any]) -> dict[str, Any]:
        shots = []
        for shot in scene_plan.get("shots") or []:
            shots.append(
                {
                    "shot_id": shot["shot_id"],
                    "keyframe_prompt": build_rule_based_keyframe_prompt(scene=scene, shot=shot),
                    "negative_prompt": negative_prompt_for_shot(shot),
                    "quality_notes": [
                        "单张关键帧只承担当前 shot 的视觉信息点。",
                        "屏幕文字建议后期叠加，生图时只保留少量清晰文字意图。",
                    ],
                }
            )
        return {"scene_id": scene_plan.get("scene_id") or scene.get("scene_id") or "", "shots": shots}


def post_deepseek_json(
    *,
    api_key: str,
    model: str,
    base_url: str,
    timeout: int,
    system_prompt: str,
    user_payload: dict[str, Any],
) -> dict[str, Any]:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, indent=2)},
        ],
        "temperature": 0.18,
        "max_tokens": int(os.environ.get("SHOT_PRODUCTION_MAX_TOKENS", "10000")),
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        base_url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"DeepSeek HTTP {exc.code}: {detail[:300]}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"DeepSeek 连接失败：{exc}") from exc
    content = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    try:
        return parse_json_object(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"DeepSeek 返回的 JSON 无法解析：{content[:300]}") from exc


def normalize_scene_plan(payload: dict[str, Any], *, scene: dict[str, Any], scene_index: int) -> dict[str, Any]:
    scene_id = str(scene.get("scene_id") or f"S{scene_index:02d}")
    duration = safe_int(scene.get("duration_sec"), default=safe_int(payload.get("duration_sec"), default=25))
    raw_shots = payload.get("shots") if isinstance(payload.get("shots"), list) else []
    if not raw_shots:
        raw_shots = RuleBasedShotPlannerProvider().plan_scene(scene)["shots"]
    shots = []
    previous_end = 0
    for index, item in enumerate(raw_shots, start=1):
        if not isinstance(item, dict):
            continue
        start_sec = safe_int(item.get("start_sec"), default=previous_end)
        end_sec = safe_int(item.get("end_sec"), default=start_sec + 4)
        start_sec = max(0, min(duration, start_sec))
        end_sec = max(start_sec + 1, min(duration, end_sec))
        if index == len(raw_shots):
            end_sec = duration
        shot_id = str(item.get("shot_id") or f"{scene_id}-SH{index:02d}")
        shots.append(
            {
                "shot_id": shot_id,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "shot_type": normalize_shot_type(item.get("shot_type")),
                "purpose": str(item.get("purpose") or item.get("visual_focus") or "推进本镜头画面。").strip(),
                "visual_focus": str(item.get("visual_focus") or item.get("purpose") or "").strip(),
                "subjects": normalize_string_list(item.get("subjects")),
                "environment": str(item.get("environment") or "").strip(),
                "action": str(item.get("action") or "").strip(),
                "composition": str(item.get("composition") or "").strip(),
                "camera": str(item.get("camera") or "").strip(),
                "mood": str(item.get("mood") or "").strip(),
                "screen_text": normalize_string_list(item.get("screen_text")),
                "must_keep_points": normalize_string_list(item.get("must_keep_points")),
                "continuity_refs": normalize_string_list(item.get("continuity_refs")),
            }
        )
        previous_end = end_sec
    if shots:
        shots[0]["start_sec"] = 0
        shots[-1]["end_sec"] = duration
    return {
        "scene_id": str(payload.get("scene_id") or scene_id),
        "title": str(payload.get("title") or scene.get("scene_title") or "未命名镜头"),
        "duration_sec": duration,
        "shots": shots,
        "review_notes": normalize_string_list(payload.get("review_notes")),
    }


def normalize_keyframe_payload(payload: dict[str, Any], *, scene_plan: dict[str, Any]) -> dict[str, Any]:
    by_id = {str(item.get("shot_id") or ""): item for item in payload.get("shots") or [] if isinstance(item, dict)}
    shots = []
    for plan_shot in scene_plan.get("shots") or []:
        item = by_id.get(plan_shot["shot_id"]) or {}
        prompt = str(item.get("keyframe_prompt") or "").strip()
        if not prompt:
            prompt = build_rule_based_keyframe_prompt(scene={}, shot=plan_shot)
        negative_prompt = str(item.get("negative_prompt") or "").strip() or negative_prompt_for_shot(plan_shot)
        shots.append(
            {
                "shot_id": plan_shot["shot_id"],
                "keyframe_prompt": prompt,
                "negative_prompt": negative_prompt,
                "quality_notes": normalize_string_list(item.get("quality_notes")),
            }
        )
    return {"scene_id": scene_plan.get("scene_id") or "", "shots": shots}


def production_shot_payload(
    *,
    scene: dict[str, Any],
    scene_index: int,
    shot: dict[str, Any],
    shot_index: int,
) -> dict[str, Any]:
    scene_id = str(scene.get("scene_id") or f"S{scene_index:02d}")
    scene_title = str(scene.get("scene_title") or scene.get("title") or "未命名镜头")
    duration = max(1, safe_int(shot.get("end_sec"), default=0) - safe_int(shot.get("start_sec"), default=0))
    narration = narration_for_shot(scene, shot)
    visual_goal = "；".join(
        item
        for item in [
            str(shot.get("visual_focus") or "").strip(),
            str(shot.get("action") or "").strip(),
        ]
        if item
    )
    return {
        "shot_id": shot["shot_id"],
        "shot_index": shot_index,
        "narration": narration,
        "subtitle_text": narration,
        "shot_type": normalize_shot_type(shot.get("shot_type")),
        "visual_goal": visual_goal or str(shot.get("purpose") or ""),
        "scene_id": "",
        "scene_name": str(shot.get("environment") or ""),
        "subject_ids": [],
        "subject_names": normalize_string_list(shot.get("subjects")),
        "visual_elements": visual_elements_for_shot(shot),
        "reference_assets": {},
        "camera": camera_payload(shot.get("camera")),
        "duration_sec": duration,
        "keyframe_prompt": str(shot.get("keyframe_prompt") or ""),
        "video_prompt": video_prompt_for_production_shot(shot),
        "negative_prompt": str(shot.get("negative_prompt") or DEFAULT_NEGATIVE_PROMPT),
        "fact_safety_note": str(scene.get("fact_boundary") or "标准镜头生产稿，需人工核对史实边界。"),
        "asset_status": "missing_keyframe",
        "keyframe_asset_id": "",
        "video_asset_id": "",
        "needs_manual_review": False,
        "source_paragraph_index": scene_index,
        "source_text_start": safe_int(shot.get("start_sec"), default=0),
        "source_text_end": safe_int(shot.get("end_sec"), default=0),
        "source_excerpt": str(shot.get("visual_focus") or shot.get("purpose") or ""),
        "is_supplemental": False,
        "supplemental_reason": "",
        "scene_block_id": scene_id,
        "scene_block_title": scene_title,
        "scene_block_index": scene_index,
        "sequence_id": scene_id,
        "sequence_title": scene_title,
        "beat_id": shot["shot_id"],
        "beat_title": str(shot.get("purpose") or shot.get("visual_focus") or ""),
        "prev_shot_id": "",
        "next_shot_id": "",
        "transition": "cut",
        "continuity": continuity_payload(shot),
        "production_plan": production_plan_payload(shot),
        "prompt_parts": {},
        "raw": dict(shot),
    }


def scene_block_payload(scene: dict[str, Any], *, scene_index: int, shots: list[dict[str, Any]]) -> dict[str, Any]:
    scene_id = str(scene.get("scene_id") or f"S{scene_index:02d}")
    visual_layer = scene.get("visual_layer") if isinstance(scene.get("visual_layer"), dict) else {}
    text = str(visual_layer.get("main_visual") or "")
    return {
        "scene_block_id": scene_id,
        "scene_block_index": scene_index,
        "title": str(scene.get("scene_title") or "未命名镜头"),
        "summary": preview_text(text or "标准镜头生产稿场景。", limit=120),
        "dramatic_purpose": str(scene.get("beat_function") or ""),
        "location": location_from_text(text),
        "time_context": time_from_text(text),
        "source_paragraph_indexes": [scene_index],
        "source_text_start": 0,
        "source_text_end": len(text),
        "source_excerpt": preview_text(text, limit=220),
        "scene_ids": [],
        "scene_names": [],
        "key_beats": [shot.get("visual_goal", "") for shot in shots[:4] if shot.get("visual_goal")],
        "estimated_duration_sec": round(sum(float(shot.get("duration_sec") or 0) for shot in shots), 1),
        "shot_count": len(shots),
        "keyframe_count": len(shots),
    }


def link_neighbor_shots(shots: list[dict[str, Any]]) -> None:
    for index, shot in enumerate(shots):
        if index > 0:
            shot["prev_shot_id"] = shots[index - 1]["shot_id"]
        if index < len(shots) - 1:
            shot["next_shot_id"] = shots[index + 1]["shot_id"]


def visual_beats_for_scene(scene: dict[str, Any]) -> list[str]:
    visual_layer = scene.get("visual_layer") if isinstance(scene.get("visual_layer"), dict) else {}
    text = str(visual_layer.get("main_visual") or "")
    raw_parts: list[str] = []
    for line in text.splitlines():
        clean = line.strip(" 　")
        if not clean:
            continue
        raw_parts.extend(split_visual_sentence(clean))
    return [part for part in raw_parts if part] or normalize_string_list(scene.get("narrator_lines")) or ["建立本镜头核心画面。"]


def split_visual_sentence(text: str) -> list[str]:
    if "快闪" in text and "：" in text:
        prefix, rest = text.split("：", 1)
        items = [item.strip() for item in re.split(r"[、,，]", rest) if item.strip()]
        return [f"{prefix}：{item}" for item in items] if items else [text]
    parts = re.split(r"(?<=[。！？；])\s*", text)
    return [part.strip(" 。；") for part in parts if part.strip(" 。；")]


def shot_count_for_scene(scene: dict[str, Any], beats: list[str]) -> int:
    duration = safe_int(scene.get("duration_sec"), default=25)
    min_count, max_count = (2, 4) if duration <= 15 else (4, 6) if duration <= 30 else (5, 8)
    scene_type = str(scene.get("scene_type") or "")
    bonus = 1 if any(marker in scene_type for marker in ["MONTAGE", "INFOGRAPHIC", "COMPARISON"]) else 0
    return max(min_count, min(max_count, len(beats) + bonus))


def fit_beats_to_count(beats: list[str], count: int, *, scene: dict[str, Any]) -> list[str]:
    fitted = list(beats)
    scene_type = str(scene.get("scene_type") or "")
    if "HOST_OPENING" in scene_type and not any("讲述人" in beat or "黑板" in beat or "虚拟老师" in beat for beat in fitted):
        fitted.append("虚拟老师在黑板或屏幕前收束本镜头问题。")
    while len(fitted) < count:
        fitted.append(fitted[-1] if fitted else "补足本镜头关键画面。")
    if len(fitted) <= count:
        return fitted
    head = fitted[: count - 1]
    tail = "；".join(fitted[count - 1 :])
    return head + [tail]


def time_ranges(duration: int, count: int) -> list[tuple[int, int]]:
    count = max(1, count)
    base = duration // count
    remainder = duration % count
    ranges = []
    cursor = 0
    for index in range(count):
        span = base + (1 if index < remainder else 0)
        end = cursor + max(1, span)
        if index == count - 1:
            end = duration
        ranges.append((cursor, end))
        cursor = end
    return ranges


def infer_production_shot_type(scene: dict[str, Any], beat: str, *, index: int) -> str:
    scene_type = str(scene.get("scene_type") or "")
    text = beat + scene_type
    if index == 1 and "HOST_OPENING" in scene_type:
        return "hook_shot"
    if any(marker in text for marker in ["地图", "地球", "非洲", "海岸", "迁徙"]):
        return "map_shot"
    if any(marker in text for marker in ["数据", "能量", "对比", "INFOGRAPHIC"]):
        return "explainer_shot"
    if any(marker in text for marker in ["快闪", "蒙太奇", "MONTAGE"]):
        return "montage_shot"
    if "COMPARISON" in scene_type:
        return "comparison_shot"
    if "HISTORICAL_REENACTMENT" in scene_type:
        return "narrative_shot"
    return "narrative_shot"


def build_rule_based_keyframe_prompt(*, scene: dict[str, Any], shot: dict[str, Any]) -> str:
    scene_type = str(scene.get("scene_type") or "")
    screen_text = " / ".join(normalize_string_list(shot.get("screen_text"))) or "无，后期按需叠加字幕"
    must_keep = "；".join(normalize_string_list(shot.get("must_keep_points"))) or "体现本 shot 的核心视觉信息"
    era_place = era_place_for_scene(scene, shot)
    static_visual = static_frame_from_visual_text(str(shot.get("visual_focus") or shot.get("action") or ""), boundary="end")
    return (
        "历史科普卡通短剧风格，半扁平漫画插画，不低幼，不是写实电影，也不是过度幼稚的扁平小人。"
        f"画面用途：{shot.get('purpose') or '镜头关键帧'}。"
        f"时代地点：{era_place}。"
        f"画面主体：{'、'.join(normalize_string_list(shot.get('subjects'))) or '按画面重点呈现主体'}。"
        f"环境背景：{shot.get('environment') or '符合历史科普短剧的简洁场景'}。"
        f"静态画面：{static_visual}。"
        f"构图：{shot.get('composition') or '主体清晰，背景辅助叙事'}。"
        f"镜头语言：{shot.get('camera') or '定格镜头，画面边界清楚，适合生成单张图'}。"
        f"情绪氛围：{shot.get('mood') or '清楚、有故事感'}。"
        f"屏幕文字：{screen_text}。"
        f"必须体现的信息：{must_keep}。"
        f"{DEFAULT_FRAME_SIZE_PROMPT}"
        "这是单张关键帧，只描述一个清晰瞬间，不要把多个独立事件硬塞进同一画面。"
        f"{'远古史/历史再现镜头，避免现代城市、现代机器、现代武器、科幻界面。' if is_historical_reenactment(scene_type, shot) else ''}"
    )


def negative_prompt_for_shot(shot: dict[str, Any]) -> str:
    items = [BASE_NEGATIVE_PROMPT]
    shot_type = normalize_shot_type(shot.get("shot_type"))
    if shot_type in {"narrative_shot", "hook_shot", "key_comic_shot"}:
        items.append("现代城市、现代机器、现代武器、科幻界面")
    if shot_type in {"map_shot", "explainer_shot", "concept_shot"}:
        items.append("复杂人物抢主体、真实卫星照片风格、过密文字")
    return "，".join(items)


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
    }
    return shot_type if shot_type in allowed else "narrative_shot"


def narration_for_shot(scene: dict[str, Any], shot: dict[str, Any]) -> str:
    lines = normalize_string_list(scene.get("narrator_lines"))
    if not lines:
        return str(shot.get("purpose") or shot.get("visual_focus") or "")
    shot_id = str(shot.get("shot_id") or "")
    match = re.search(r"SH(\d+)", shot_id)
    index = safe_int(match.group(1), default=1) if match else 1
    return lines[min(len(lines) - 1, max(0, index - 1))]


def visual_elements_for_shot(shot: dict[str, Any]) -> list[str]:
    values = []
    for key in ["visual_focus", "environment", "action"]:
        value = str(shot.get(key) or "").strip()
        if value:
            values.append(preview_text(value, limit=40))
    return dedupe(values)


def camera_payload(camera: Any) -> dict[str, str]:
    text = str(camera or "")
    return {
        "shot_size": "wide shot" if "远景" in text or "全景" in text else "medium wide shot",
        "angle": "eye level",
        "movement": "slow push in" if "推进" in text else "gentle pan" if "横移" in text else "subtle motion",
        "description": text,
    }


def continuity_payload(shot: dict[str, Any]) -> dict[str, str]:
    refs = normalize_string_list(shot.get("continuity_refs"))
    return {
        "previous_shot_relation": refs[0] if refs else "承接同一主镜头内上一关键帧。",
        "screen_direction": "left-to-right",
        "continuity_axis": "保持主体造型、色彩和空间方向一致。",
        "spatial_continuity_note": "同一主镜头内的关键帧应保持画风和叙事节奏一致。",
        "visual_bridge": str(shot.get("visual_focus") or ""),
    }


def production_plan_payload(shot: dict[str, Any]) -> dict[str, Any]:
    shot_type = normalize_shot_type(shot.get("shot_type"))
    render_method = "map_animation" if shot_type == "map_shot" else "motion_graphics" if shot_type in {"explainer_shot", "concept_shot"} else "image_to_video"
    return {
        "render_method": render_method,
        "cost_tier": "low" if render_method in {"map_animation", "motion_graphics"} else "medium",
        "needs_keyframe": True,
        "needs_video": True,
        "recommended_tool": "Seedream keyframe + image-to-video",
        "reason": "标准镜头生产稿拆出的每个 shot 对应一张关键帧和一段后续视频/动效任务。",
    }


def video_prompt_for_production_shot(shot: dict[str, Any]) -> str:
    duration = max(1, safe_int(shot.get("end_sec"), default=0) - safe_int(shot.get("start_sec"), default=0))
    return (
        f"基于本关键帧生成 {duration} 秒视频，保持历史科普卡通短剧风格一致。"
        f"运动方式：{shot.get('camera') or '轻微推进'}；动作重点：{shot.get('action') or shot.get('visual_focus') or ''}。"
    )


def distribute_items(items: list[str], *, index: int, count: int) -> list[str]:
    if not items:
        return []
    if count <= 1:
        return items[:2]
    bucket = min(len(items) - 1, max(0, round((index - 1) * (len(items) - 1) / max(1, count - 1))))
    return [items[bucket]]


def subjects_for_text(text: str) -> list[str]:
    candidates = ["智人", "早期智人", "尼安德特人", "虚拟老师", "讲述人", "婴儿", "部落"]
    return [item for item in candidates if item in text]


def environment_for_text(text: str) -> str:
    for marker in ["东非稀树草原", "非洲大陆", "太空", "黑板", "洞穴", "篝火", "海岸", "城市夜景"]:
        if marker in text:
            return marker
    if "地球" in text:
        return "太空与地球"
    if "草原" in text:
        return "东非稀树草原"
    return ""


def action_for_text(text: str) -> str:
    if "推进" in text:
        return "镜头推进，空间逐步展开。"
    if "移动" in text:
        return "主体小心移动。"
    if "快闪" in text:
        return "象征画面快速闪现。"
    if "缩回" in text or "黑板" in text:
        return "画面收束到讲述人黑板。"
    return preview_text(text, limit=80)


def composition_for_shot_type(shot_type: str) -> str:
    return {
        "hook_shot": "强开场构图，主体和问题清楚。",
        "map_shot": "大范围空间构图，地图或地貌为主体。",
        "explainer_shot": "数据屏或图解居中，信息层级清晰。",
        "montage_shot": "象征元素简洁排列，不堆满画面。",
        "comparison_shot": "左右对比或前后融合构图。",
    }.get(shot_type, "中景到全景，主体清晰，背景辅助。")


def camera_for_shot_type(shot_type: str) -> str:
    return {
        "hook_shot": "缓慢推进，建立悬念。",
        "map_shot": "从远景推进到地点。",
        "explainer_shot": "稳定镜头，轻微推近信息。",
        "montage_shot": "快速切换但单帧保持清晰。",
        "comparison_shot": "稳定左右对比镜头。",
    }.get(shot_type, "轻微推进或横移。")


def mood_for_text(text: str) -> str:
    if any(marker in text for marker in ["压迫", "狮子", "鬣狗", "危险"]):
        return "有压迫感但不恐怖。"
    if any(marker in text for marker in ["翻盘", "登月", "探海"]):
        return "反差强、带一点幽默和惊奇。"
    return "清楚、有纪录片式讲述感。"


def continuity_refs_for_index(scene_id: str, index: int) -> list[str]:
    if index <= 1:
        return []
    return [f"承接 {scene_id}-SH{index - 1:02d} 的画风、主体外观和空间方向。"]


def purpose_for_beat(scene: dict[str, Any], beat: str, *, index: int) -> str:
    if index == 1:
        return str(scene.get("beat_function") or "建立本主镜头开场信息。")
    return preview_text(beat, limit=80)


def era_place_for_scene(scene: dict[str, Any], shot: dict[str, Any]) -> str:
    text = " ".join([str(scene.get("scene_title") or ""), str(shot.get("visual_focus") or ""), str(shot.get("environment") or "")])
    time_label = time_from_text(text)
    location = location_from_text(text)
    return "，".join(item for item in [time_label, location] if item) or "历史科普叙事场景"


def is_historical_reenactment(scene_type: str, shot: dict[str, Any]) -> bool:
    return "HISTORICAL_REENACTMENT" in scene_type or normalize_shot_type(shot.get("shot_type")) in {"narrative_shot", "hook_shot"}


def location_from_text(text: str) -> str:
    for marker in ["非洲东部", "东非", "非洲大陆", "布隆伯斯洞穴", "红海", "黎凡特", "南亚"]:
        if marker in text:
            return marker
    return ""


def time_from_text(text: str) -> str:
    match = re.search(r"\d+\s*万年前", text)
    return match.group(0) if match else ""


def preview_text(value: Any, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[\n,，、]+", value) if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def safe_int(value: Any, *, default: int) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        clean = str(item or "").strip()
        if clean and clean not in seen:
            result.append(clean)
            seen.add(clean)
    return result
