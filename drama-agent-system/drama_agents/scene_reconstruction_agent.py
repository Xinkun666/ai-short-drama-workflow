from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4

from drama_agents.chapter_refiner import parse_json_object
from drama_agents.storage import current_timestamp
from drama_agents.storyboard_agent import DEFAULT_FRAME_SIZE_PROMPT, DEFAULT_STYLE_POLICY


DIRECTOR_SYSTEM_PROMPT = """
你是历史科普短剧的分镜重构导演 Agent。

你的任务：
把一个标准分镜 scene 重构成可执行的视频分段 segments 和关键帧边界 keyframes。

你只负责导演层重构，不写最终 AI 绘图提示词。
你必须输出严格合法 JSON object，不要 Markdown，不要解释文字。

核心规则：
1. 必须同时参考 previous_scene、current_scene、next_scene。
2. segment 是视频段落；keyframe 是段落边界。
3. 如果 n 个 segment 是连续视频段，通常 keyframe_count = n + 1。
4. 允许 DIRECT_CUT，不强制所有关键帧之间连续转场。
5. 每个 segment 只表达一个主要画面演绎阶段。
6. 不要把全部 screen_text 复制到每个 segment。
7. 不要把全部 must_keep_points 复制到每个 segment。
8. 需要判断 inter_scene_bridge 是否必要。
9. bridge_strategy 只允许 DIRECT_CUT、REUSE_CURRENT_END_KEYFRAME、GENERATE_BRIDGE_KEYFRAME。
10. segment connection.type 只允许 DIRECT_CUT、IMAGE_TO_VIDEO、CAMERA_MOTION、MATCH_CUT、GRAPHIC_WIPE。
11. segment.visual_action 只能描述当前 segment 从 from_keyframe 到 to_keyframe 的画面动作，禁止复制整段画面演绎。
12. 前后 segment 不要重复同一个开场推镜、地点建立或蒙太奇前缀，除非画面真的回到该动作。
13. 快闪蒙太奇应集中在对应 segment 中，用 DIRECT_CUT，不要把火箭、潜水器、城市夜景等提前塞进草原建立段或弱小对比段。
14. narration_range.full_text 必须按旁白原文顺序分配给对应 segment，不要把提问句提前配给反差蒙太奇段。
15. 如果输入中提供 segment_blueprint，必须严格使用其中的 segment_id、时间范围、source_visual_unit_ids、source_narration_unit_ids 和 visual_action_seed。
16. 你可以补充导演语言、镜头语言和衔接理由，但不能改变 segment_blueprint 的叙事顺序和源稿映射。
""".strip()


KEYFRAME_PROMPT_SYSTEM_PROMPT = """
你是历史科普短剧的关键帧提示词 Agent。

你的任务：
根据已经完成的分镜重构结果，为每个 keyframe 补充 AI 关键帧绘图提示词。

你不能重新分段。
你不能改变 scene_id、segment_id、keyframe_id、start_sec、end_sec、from_keyframe_id、to_keyframe_id。
你只能补充 keyframe_prompt、negative_prompt。
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

关键帧只描述一帧静态画面，禁止把视频运动写进 keyframe_prompt。
禁止在 keyframe_prompt 中出现“镜头推进、缓慢旋转、再落到、快闪、缩回、逐渐”等连续运动描述。
镜头运动、转场、快闪、缩放、推进必须留给 segment.motion_prompt。

negative_prompt 必须包含：
写实照片、电影剧照、过度低幼、3D塑料感、现代建筑、现代服装、现代载具、血腥暴力、恐怖画面、文字乱码、水印、logo。
""".strip()


BASE_NEGATIVE_PROMPT = (
    "写实照片、电影剧照、过度低幼、3D塑料感、现代建筑、现代服装、现代载具、"
    "血腥暴力、恐怖画面、文字乱码、水印、logo"
)
MANDATORY_SINGLE_KEYFRAME_SENTENCE = "这是单张关键帧，只描述一个清晰瞬间，不要把多个独立事件硬塞进同一画面。"
KEYFRAME_PROMPT_REQUIRED_MARKERS = [
    "画面用途：",
    "时代地点：",
    "画面主体：",
    "环境背景：",
    "静态画面：",
    "构图：",
    "镜头语言：",
    "情绪氛围：",
    "屏幕文字：",
    "必须体现的信息：",
]
KEYFRAME_MOTION_FORBIDDEN_TERMS = ["镜头推进", "缓慢旋转", "再落到", "快闪", "缩回", "逐渐"]
ALLOWED_BRIDGE_STRATEGIES = {"DIRECT_CUT", "REUSE_CURRENT_END_KEYFRAME", "GENERATE_BRIDGE_KEYFRAME"}
ALLOWED_CONNECTION_TYPES = {"DIRECT_CUT", "IMAGE_TO_VIDEO", "CAMERA_MOTION", "MATCH_CUT", "GRAPHIC_WIPE"}


class ParsedSceneNormalizer:
    def normalize(self, scene: dict[str, Any], *, index: int) -> dict[str, Any]:
        visual_layer = scene.get("visual_layer") if isinstance(scene.get("visual_layer"), dict) else {}
        knowledge = scene.get("knowledge_payload") if isinstance(scene.get("knowledge_payload"), dict) else {}
        return {
            "scene_id": str(scene.get("scene_id") or f"S{index:02d}").strip(),
            "title": str(scene.get("scene_title") or scene.get("title") or "未命名镜头").strip(),
            "scene_type": split_tags(scene.get("scene_type")),
            "function": split_function(scene.get("beat_function") or scene.get("function")),
            "duration_sec": parse_duration_seconds(scene.get("duration_sec")),
            "source_refs": normalize_string_list(scene.get("source_atoms") or scene.get("source_trace")),
            "narration": normalize_string_list(scene.get("narrator_lines") or scene.get("narration")),
            "visual_description": str(visual_layer.get("main_visual") or scene.get("visual_description") or "").strip(),
            "screen_text": normalize_string_list(scene.get("screen_text")),
            "dialogue": normalize_dialogue(scene.get("historical_character_dialogue") or scene.get("dialogue")),
            "must_keep_points": normalize_string_list(knowledge.get("must_keep_details") or scene.get("must_keep_points")),
            "fact_boundary": str(scene.get("fact_boundary") or "用户提供分镜稿，需人工核对").strip(),
            "raw": scene,
        }


class SceneReconstructionPipeline:
    def __init__(
        self,
        *,
        director_provider=None,
        keyframe_prompt_provider=None,
        normalizer: ParsedSceneNormalizer | None = None,
        validator=None,
    ):
        self.director_provider = director_provider or RuleBasedSceneReconstructionProvider()
        self.keyframe_prompt_provider = keyframe_prompt_provider or RuleBasedKeyframePromptProvider()
        self.normalizer = normalizer or ParsedSceneNormalizer()
        self.validator = validator or SceneReconstructionValidator()

    @classmethod
    def from_environment(cls):
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            return cls()
        return cls(
            director_provider=DeepSeekSceneReconstructionProvider(api_key=api_key),
            keyframe_prompt_provider=DeepSeekKeyframePromptProvider(api_key=api_key),
        )

    def generate(
        self,
        *,
        generation: dict[str, Any],
        storyboard_script: dict[str, Any],
        output_dir: str | Path | None = None,
        source_filename: str = "",
    ) -> dict[str, Any]:
        raw_scenes = [scene for scene in storyboard_script.get("scene_script") or [] if isinstance(scene, dict)]
        if not raw_scenes:
            raise RuntimeError("当前剧本缺少标准镜头生产稿 scene_script，无法生成分镜重构。")
        reconstruction_id = f"sr-{uuid4().hex}"
        scenes = [self.normalizer.normalize(scene, index=index) for index, scene in enumerate(raw_scenes, start=1)]
        reconstructed_scenes = []
        all_keyframe_shots: list[dict[str, Any]] = []
        scene_blocks: list[dict[str, Any]] = []
        for index, scene in enumerate(scenes):
            blueprint = build_scene_blueprint(scene)
            context = {
                "previous_scene": summarize_neighbor_scene(scenes[index - 1]) if index > 0 else None,
                "current_scene": scene,
                "next_scene": summarize_neighbor_scene(scenes[index + 1]) if index + 1 < len(scenes) else None,
                "source_analysis": blueprint["source_analysis"],
                "segment_blueprint": blueprint["segments"],
                "keyframe_blueprint": blueprint["keyframes"],
                "blueprint_rules": [
                    "segment_blueprint 是当前 scene 的权威分段骨架。",
                    "每个 segment 只能使用自己的 source_visual_unit_ids 对应画面，不得拿其它段的画面。",
                    "narration_range 必须使用 source_narration_unit_ids 对应旁白，不得提前或滞后。",
                    "DIRECT_CUT 是允许的，快闪蒙太奇优先 DIRECT_CUT。",
                ],
            }
            director_payload = self.director_provider.reconstruct_scene(context)
            reconstruction = normalize_scene_reconstruction(director_payload, scene=scene, context=context)
            apply_scene_blueprint(reconstruction, blueprint, scene=scene)
            prompt_payload = self.keyframe_prompt_provider.generate_keyframe_prompts(scene=scene, reconstruction=reconstruction)
            merge_keyframe_prompts(reconstruction, prompt_payload)
            repair_reconstruction_timing_and_visuals(reconstruction, scene=scene)
            apply_scene_blueprint(reconstruction, blueprint, scene=scene)
            reconstruction["validation"] = self.validator.validate(reconstruction)
            reconstructed_scenes.append(reconstruction)
            scene_shots = keyframe_shots_for_scene(
                reconstruction,
                scene_index=index + 1,
                shot_offset=len(all_keyframe_shots),
                reconstruction_id=reconstruction_id,
            )
            link_neighbor_shots(scene_shots)
            all_keyframe_shots.extend(scene_shots)
            scene_blocks.append(scene_block_for_reconstruction(reconstruction, scene_index=index + 1))

        title = str(storyboard_script.get("title") or generation.get("topic") or "分镜重构").strip()
        total_duration = round(sum(float(scene.get("duration_sec") or 0) for scene in reconstructed_scenes), 1)
        scene_count = len(reconstructed_scenes)
        segment_count = sum(len(scene.get("segments") or []) for scene in reconstructed_scenes)
        keyframe_count = sum(len(scene.get("keyframes") or []) for scene in reconstructed_scenes)
        reconstruction_payload = {
            "reconstruction_id": reconstruction_id,
            "generation_id": generation.get("generation_id", ""),
            "status": "completed" if all((scene.get("validation") or {}).get("passed") for scene in reconstructed_scenes) else "needs_review",
            "source_title": title,
            "scene_count": scene_count,
            "segment_count": segment_count,
            "keyframe_count": keyframe_count,
            "created_at": current_timestamp(),
            "model": getattr(self.director_provider, "model", "rule-based"),
            "scenes": reconstructed_scenes,
            "json_path": "",
            "markdown_path": "",
        }
        if output_dir:
            write_reconstruction_files(reconstruction_payload, output_dir=Path(output_dir))
        return {
            "reconstruction_id": reconstruction_id,
            "generation_id": generation.get("generation_id", ""),
            "title": f"{title} - 分镜重构",
            "source_type": "standard_storyboard_script",
            "source_filename": source_filename,
            "json_path": reconstruction_payload.get("json_path") or "",
            "markdown_path": reconstruction_payload.get("markdown_path") or "",
            "status": reconstruction_payload["status"],
            "target_duration_sec": int(round(total_duration)),
            "actual_duration_sec": total_duration,
            "scene_count": scene_count,
            "segment_count": segment_count,
            "keyframe_count": keyframe_count,
            "shot_count": keyframe_count,
            "style_policy": DEFAULT_STYLE_POLICY,
            "missing_subject_candidates": [],
            "missing_scene_candidates": [],
            "review_notes": validation_notes(reconstructed_scenes),
            "coverage": {
                "source_scene_count": len(raw_scenes),
                "planned_scene_count": scene_count,
                "segment_count": segment_count,
                "keyframe_count": keyframe_count,
                "coverage_ratio": 1.0 if raw_scenes else 0.0,
            },
            "script_feedback": [],
            "scene_blocks": scene_blocks,
            "scenes": reconstructed_scenes,
            "shots": all_keyframe_shots,
            "raw": {
                "format": "scene_reconstruction",
                "source_storyboard_format": storyboard_script.get("format") or "",
                "scene_reconstruction": reconstruction_payload,
                "scenes": reconstructed_scenes,
                "scene_blocks": scene_blocks,
                "agent_systems": {
                    "director": "deepseek_v4_pro_scene_reconstruction"
                    if isinstance(self.director_provider, DeepSeekSceneReconstructionProvider)
                    else "rule_based_scene_reconstruction",
                    "keyframe_prompt": "deepseek_v4_pro_keyframe_prompt"
                    if isinstance(self.keyframe_prompt_provider, DeepSeekKeyframePromptProvider)
                    else "rule_based_keyframe_prompt",
                },
            },
        }


class DeepSeekSceneReconstructionProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str | None = None,
        base_url: str = "https://api.deepseek.com/chat/completions",
        timeout: int | None = None,
    ):
        self.api_key = api_key
        self.model = (
            model
            or os.environ.get("DEEPSEEK_SCENE_RECONSTRUCTION_MODEL")
            or os.environ.get("DEEPSEEK_ADAPTATION_MODEL")
            or os.environ.get("DEEPSEEK_SCRIPT_MODEL")
            or os.environ.get("DEEPSEEK_MODEL")
            or "deepseek-v4-pro"
        )
        self.base_url = base_url
        self.timeout = timeout or int(os.environ.get("DEEPSEEK_TIMEOUT", "240"))

    def reconstruct_scene(self, context: dict[str, Any]) -> dict[str, Any]:
        return post_deepseek_json(
            api_key=self.api_key,
            model=self.model,
            base_url=self.base_url,
            timeout=self.timeout,
            system_prompt=DIRECTOR_SYSTEM_PROMPT,
            user_payload={
                "task": "根据 previous/current/next scene 生成当前 scene 的分镜重构导演层结构。",
                "context": context,
                "output_schema": director_output_schema(),
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
        self.model = (
            model
            or os.environ.get("DEEPSEEK_KEYFRAME_PROMPT_MODEL")
            or os.environ.get("DEEPSEEK_SCENE_RECONSTRUCTION_MODEL")
            or os.environ.get("DEEPSEEK_MODEL")
            or "deepseek-v4-pro"
        )
        self.base_url = base_url
        self.timeout = timeout or int(os.environ.get("DEEPSEEK_TIMEOUT", "240"))

    def generate_keyframe_prompts(self, *, scene: dict[str, Any], reconstruction: dict[str, Any]) -> dict[str, Any]:
        return post_deepseek_json(
            api_key=self.api_key,
            model=self.model,
            base_url=self.base_url,
            timeout=self.timeout,
            system_prompt=KEYFRAME_PROMPT_SYSTEM_PROMPT,
            user_payload={
                "task": "为分镜重构结果中的每个 keyframe 生成关键帧绘图提示词。",
                "scene": scene,
                "reconstruction": reconstruction,
                "output_schema": {
                    "scene_id": scene.get("scene_id") or "",
                    "keyframes": [
                        {
                            "keyframe_id": "S01-KF01",
                            "keyframe_prompt": "...",
                            "negative_prompt": "...",
                        }
                    ],
                },
            },
        )


class RuleBasedSceneReconstructionProvider:
    model = "rule-based"

    def reconstruct_scene(self, context: dict[str, Any]) -> dict[str, Any]:
        scene = context["current_scene"]
        beats = visual_beats(scene)
        count = desired_segment_count(scene, beats)
        beats = visual_segments_for_count(scene, count)
        ranges = time_ranges(scene["duration_sec"], count)
        keyframes = []
        for index in range(count + 1):
            time_sec = ranges[index][0] if index < count else scene["duration_sec"]
            source_beat = beats[min(index, count - 1)] if beats else scene["visual_description"]
            keyframes.append(
                {
                    "keyframe_id": f"{scene['scene_id']}-KF{index + 1:02d}",
                    "scene_id": scene["scene_id"],
                    "time_sec": time_sec,
                    "position": "scene_start" if index == 0 else "scene_end" if index == count else "segment_boundary",
                    "frame_role": frame_role_for_index(index, count, source_beat),
                    "visual_purpose": source_beat,
                    "visual_content": source_beat,
                    "subjects": subjects_for_text(source_beat),
                    "environment": environment_for_text(source_beat),
                    "composition": "主体清晰，背景辅助叙事。",
                    "camera_state": camera_for_text(source_beat),
                    "mood": mood_for_text(source_beat),
                    "screen_text": distribute_items(scene["screen_text"], index=index + 1, count=count + 1),
                    "subtitle_text": distribute_items(scene["narration"], index=index + 1, count=count + 1)[0]
                    if distribute_items(scene["narration"], index=index + 1, count=count + 1)
                    else "",
                    "continuity": {},
                    "asset": {"status": "not_generated", "candidate_images": [], "selected_image": ""},
                }
            )
        segments = []
        for index, beat in enumerate(beats, start=1):
            start_sec, end_sec = ranges[index - 1]
            connection_type = connection_type_for_beat(beat)
            segments.append(
                {
                    "segment_id": f"{scene['scene_id']}-SEG{index:02d}",
                    "scene_id": scene["scene_id"],
                    "start_sec": start_sec,
                    "end_sec": end_sec,
                    "duration_sec": max(0, end_sec - start_sec),
                    "from_keyframe_id": keyframes[index - 1]["keyframe_id"],
                    "to_keyframe_id": keyframes[index]["keyframe_id"],
                    "segment_role": segment_role_for_beat(beat),
                    "visual_purpose": beat,
                    "narration_range": {"full_text": narration_for_segment(scene, index=index, count=count)},
                    "subtitle_lines": subtitle_lines_for_segment(scene, index=index, count=count, start_sec=start_sec, end_sec=end_sec),
                    "screen_text": [
                        {"text": item, "start_sec": start_sec, "end_sec": end_sec, "position": "auto"}
                        for item in distribute_items(scene["screen_text"], index=index, count=count)
                    ],
                    "visual_action": beat,
                    "camera_motion": camera_for_text(beat),
                    "transition_type": connection_type.lower(),
                    "connection": {
                        "type": connection_type,
                        "render_method": render_method_for_connection(connection_type),
                        "reason": reason_for_connection(connection_type),
                    },
                    "motion_prompt": "",
                    "must_keep_points": distribute_items(scene["must_keep_points"], index=index, count=count),
                    "quality_notes": [],
                }
            )
        bridge_needed = bool(context.get("next_scene")) and should_bridge_to_next(scene, context.get("next_scene") or {})
        strategy = "GENERATE_BRIDGE_KEYFRAME" if bridge_needed else "DIRECT_CUT"
        return {
            "scene_id": scene["scene_id"],
            "title": scene["title"],
            "duration_sec": scene["duration_sec"],
            "reconstruction_decision": {
                "segment_count": len(segments),
                "keyframe_count": len(keyframes),
                "needs_inter_scene_bridge": bridge_needed,
                "bridge_strategy": strategy,
                "reason": "根据画面演绎阶段、旁白节奏和相邻镜头衔接动态决定。",
            },
            "keyframes": keyframes,
            "segments": segments,
            "inter_scene_bridge": {
                "needed": bridge_needed,
                "strategy": strategy,
                "reason": "当前镜头结尾需要承接下一镜头。" if bridge_needed else "当前镜头可以直接切到下一镜头。",
                "bridge_keyframe": {},
                "bridge_segment": {},
            },
        }


class RuleBasedKeyframePromptProvider:
    model = "rule-based"

    def generate_keyframe_prompts(self, *, scene: dict[str, Any], reconstruction: dict[str, Any]) -> dict[str, Any]:
        return {
            "scene_id": scene["scene_id"],
            "keyframes": [
                {
                    "keyframe_id": keyframe["keyframe_id"],
                    "keyframe_prompt": build_keyframe_prompt(scene=scene, keyframe=keyframe),
                    "negative_prompt": negative_prompt_for_scene(scene, keyframe),
                }
                for keyframe in reconstruction.get("keyframes") or []
            ],
        }


class SceneReconstructionValidator:
    def validate(self, scene: dict[str, Any]) -> dict[str, Any]:
        issues: list[str] = []
        if not scene.get("scene_id"):
            issues.append("scene_id 缺失")
        segments = scene.get("segments") if isinstance(scene.get("segments"), list) else []
        keyframes = scene.get("keyframes") if isinstance(scene.get("keyframes"), list) else []
        decision = scene.get("reconstruction_decision") if isinstance(scene.get("reconstruction_decision"), dict) else {}
        if int(decision.get("segment_count") or 0) != len(segments):
            issues.append("segment_count 与 segments 数量不一致")
        if int(decision.get("keyframe_count") or 0) != len(keyframes):
            issues.append("keyframe_count 与 keyframes 数量不一致")
        keyframe_ids = {str(keyframe.get("keyframe_id") or "") for keyframe in keyframes}
        for segment in segments:
            if str(segment.get("from_keyframe_id") or "") not in keyframe_ids:
                issues.append(f"{segment.get('segment_id') or ''} from_keyframe_id 不存在")
            if str(segment.get("to_keyframe_id") or "") not in keyframe_ids:
                issues.append(f"{segment.get('segment_id') or ''} to_keyframe_id 不存在")
            connection = segment.get("connection") if isinstance(segment.get("connection"), dict) else {}
            connection_type = str(connection.get("type") or segment.get("transition_type") or "").upper()
            if connection_type and connection_type not in ALLOWED_CONNECTION_TYPES:
                issues.append(f"{segment.get('segment_id') or ''} connection.type 不允许")
        issues.extend(validate_segments_against_blueprint(scene, segments))
        for keyframe in keyframes:
            prompt = str(keyframe.get("keyframe_prompt") or "")
            negative_prompt = str(keyframe.get("negative_prompt") or "")
            if not prompt:
                issues.append(f"{keyframe.get('keyframe_id') or ''} keyframe_prompt 缺失")
            if prompt and "单张关键帧" not in prompt:
                issues.append(f"{keyframe.get('keyframe_id') or ''} keyframe_prompt 缺少单张关键帧约束")
            if prompt and keyframe_prompt_contains_motion(prompt):
                issues.append(f"{keyframe.get('keyframe_id') or ''} keyframe_prompt 含有视频运动描述")
            if not negative_prompt:
                issues.append(f"{keyframe.get('keyframe_id') or ''} negative_prompt 缺失")
            for marker in ["现代建筑", "现代服装", "水印"]:
                if negative_prompt and marker not in negative_prompt:
                    issues.append(f"{keyframe.get('keyframe_id') or ''} negative_prompt 缺少 {marker}")
        if screen_text_copied_to_every_segment(segments):
            issues.append("screen_text 疑似被无脑复制到每个 segment")
        bridge = scene.get("inter_scene_bridge") if isinstance(scene.get("inter_scene_bridge"), dict) else {}
        bridge_strategy = str(bridge.get("strategy") or decision.get("bridge_strategy") or "").upper()
        if bridge_strategy and bridge_strategy not in ALLOWED_BRIDGE_STRATEGIES:
            issues.append("bridge_strategy 不允许")
        if bridge.get("needed") is True and not str(bridge.get("reason") or "").strip():
            issues.append("inter_scene_bridge.needed 为 true 时必须有 reason")
        return {"passed": not issues, "issues": issues}


def validate_segments_against_blueprint(scene: dict[str, Any], segments: list[dict[str, Any]]) -> list[str]:
    blueprints = scene.get("segment_blueprint") if isinstance(scene.get("segment_blueprint"), list) else []
    if not blueprints:
        return []
    issues = []
    by_id = {str(item.get("segment_id") or ""): item for item in blueprints if isinstance(item, dict)}
    for index, segment in enumerate(segments):
        blueprint = by_id.get(str(segment.get("segment_id") or "")) or (
            blueprints[index] if index < len(blueprints) and isinstance(blueprints[index], dict) else {}
        )
        if not blueprint:
            issues.append(f"{segment.get('segment_id') or ''} 缺少 segment_blueprint")
            continue
        if normalize_string_list(segment.get("source_visual_unit_ids")) != normalize_string_list(blueprint.get("source_visual_unit_ids")):
            issues.append(f"{segment.get('segment_id') or ''} source_visual_unit_ids 与 blueprint 不一致")
        if normalize_string_list(segment.get("source_narration_unit_ids")) != normalize_string_list(blueprint.get("source_narration_unit_ids")):
            issues.append(f"{segment.get('segment_id') or ''} source_narration_unit_ids 与 blueprint 不一致")
        if compact_text(segment.get("visual_action")) != compact_text(blueprint.get("visual_action_seed")):
            issues.append(f"{segment.get('segment_id') or ''} visual_action 与 blueprint 不一致")
        narration_range = segment.get("narration_range") if isinstance(segment.get("narration_range"), dict) else {}
        if compact_text(narration_range.get("full_text")) != compact_text(blueprint.get("narration_seed")):
            issues.append(f"{segment.get('segment_id') or ''} narration_range 与 blueprint 不一致")
    return issues


def post_deepseek_json(
    *,
    api_key: str,
    model: str,
    base_url: str,
    timeout: int,
    system_prompt: str,
    user_payload: dict[str, Any],
) -> dict[str, Any]:
    content = call_deepseek(
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout=timeout,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, indent=2)},
        ],
    )
    try:
        return parse_json_object(content)
    except json.JSONDecodeError:
        repaired = call_deepseek(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
            messages=[
                {"role": "system", "content": "你只负责修复 JSON。必须输出严格合法 JSON object，不要 Markdown，不要解释。"},
                {"role": "user", "content": content},
            ],
        )
        try:
            return parse_json_object(repaired)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"DeepSeek 返回的 JSON 无法解析：{content[:300]}") from exc


def call_deepseek(
    *,
    api_key: str,
    model: str,
    base_url: str,
    timeout: int,
    messages: list[dict[str, str]],
) -> str:
    body = {
        "model": model,
        "messages": messages,
        "temperature": 0.18,
        "max_tokens": int(os.environ.get("SCENE_RECONSTRUCTION_MAX_TOKENS", "12000")),
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
    return str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")


def normalize_scene_reconstruction(payload: dict[str, Any], *, scene: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    keyframes = normalize_keyframes(payload.get("keyframes"), scene=scene)
    segments = normalize_segments(payload.get("segments"), scene=scene, keyframes=keyframes)
    decision = payload.get("reconstruction_decision") if isinstance(payload.get("reconstruction_decision"), dict) else {}
    bridge = payload.get("inter_scene_bridge") if isinstance(payload.get("inter_scene_bridge"), dict) else {}
    bridge_strategy = normalize_bridge_strategy(bridge.get("strategy") or decision.get("bridge_strategy"))
    result = {
        "scene_id": scene["scene_id"],
        "title": str(payload.get("title") or scene["title"]),
        "duration_sec": parse_duration_seconds(payload.get("duration_sec") or scene["duration_sec"]),
        "scene_type": scene["scene_type"],
        "function": scene["function"],
        "source_scene": {
            "narration": "\n".join(scene["narration"]),
            "visual_description": scene["visual_description"],
            "screen_text": scene["screen_text"],
            "dialogue": scene["dialogue"],
            "must_keep_points": scene["must_keep_points"],
        },
        "continuity_context": {
            "previous_scene": context.get("previous_scene"),
            "next_scene": context.get("next_scene"),
        },
        "reconstruction_decision": {
            "segment_count": len(segments),
            "keyframe_count": len(keyframes),
            "needs_inter_scene_bridge": bool(bridge.get("needed") or decision.get("needs_inter_scene_bridge")),
            "bridge_strategy": bridge_strategy,
            "reason": str(decision.get("reason") or bridge.get("reason") or "根据画面连续性和叙事节奏判断。"),
        },
        "keyframes": keyframes,
        "segments": segments,
        "inter_scene_bridge": {
            "needed": bool(bridge.get("needed") or decision.get("needs_inter_scene_bridge")),
            "strategy": bridge_strategy,
            "reason": str(bridge.get("reason") or decision.get("reason") or ""),
            "bridge_keyframe": bridge.get("bridge_keyframe") if isinstance(bridge.get("bridge_keyframe"), dict) else {},
            "bridge_segment": bridge.get("bridge_segment") if isinstance(bridge.get("bridge_segment"), dict) else {},
        },
        "validation": {"passed": False, "issues": ["尚未校验"]},
    }
    add_keyframe_continuity(result["keyframes"])
    return result


def normalize_keyframes(value: Any, *, scene: dict[str, Any]) -> list[dict[str, Any]]:
    raw_keyframes = value if isinstance(value, list) and value else fallback_keyframes(scene)
    keyframes = []
    for index, item in enumerate(raw_keyframes, start=1):
        if not isinstance(item, dict):
            continue
        keyframe_id = str(item.get("keyframe_id") or f"{scene['scene_id']}-KF{index:02d}")
        keyframes.append(
            {
                "keyframe_id": keyframe_id,
                "scene_id": scene["scene_id"],
                "time_sec": parse_duration_seconds(item.get("time_sec")) if item.get("time_sec") is not None else 0,
                "position": str(item.get("position") or "segment_boundary"),
                "frame_role": str(item.get("frame_role") or "KEYFRAME"),
                "visual_purpose": str(item.get("visual_purpose") or item.get("visual_content") or ""),
                "visual_content": str(item.get("visual_content") or item.get("visual_purpose") or ""),
                "subjects": normalize_string_list(item.get("subjects")),
                "environment": str(item.get("environment") or ""),
                "composition": str(item.get("composition") or ""),
                "camera_state": str(item.get("camera_state") or item.get("camera") or ""),
                "mood": str(item.get("mood") or ""),
                "screen_text": normalize_string_list(item.get("screen_text")),
                "subtitle_text": str(item.get("subtitle_text") or ""),
                "continuity": item.get("continuity") if isinstance(item.get("continuity"), dict) else {},
                "keyframe_prompt": str(item.get("keyframe_prompt") or ""),
                "negative_prompt": str(item.get("negative_prompt") or ""),
                "asset": item.get("asset") if isinstance(item.get("asset"), dict) else {"status": "not_generated", "candidate_images": [], "selected_image": ""},
            }
        )
    return keyframes


def normalize_segments(value: Any, *, scene: dict[str, Any], keyframes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_segments = value if isinstance(value, list) and value else fallback_segments(scene, keyframes)
    keyframe_ids = [keyframe["keyframe_id"] for keyframe in keyframes]
    segments = []
    for index, item in enumerate(raw_segments, start=1):
        if not isinstance(item, dict):
            continue
        start_sec = parse_duration_seconds(item.get("start_sec")) if item.get("start_sec") is not None else 0
        end_sec = parse_duration_seconds(item.get("end_sec")) if item.get("end_sec") is not None else start_sec + 1
        connection = item.get("connection") if isinstance(item.get("connection"), dict) else {}
        connection_type = normalize_connection_type(connection.get("type") or item.get("transition_type"))
        from_id = str(item.get("from_keyframe_id") or keyframe_ids[max(0, min(index - 1, len(keyframe_ids) - 1))])
        to_id = str(item.get("to_keyframe_id") or keyframe_ids[max(0, min(index, len(keyframe_ids) - 1))])
        segments.append(
            {
                "segment_id": str(item.get("segment_id") or f"{scene['scene_id']}-SEG{index:02d}"),
                "scene_id": scene["scene_id"],
                "start_sec": start_sec,
                "end_sec": end_sec,
                "duration_sec": max(0, end_sec - start_sec),
                "from_keyframe_id": from_id,
                "to_keyframe_id": to_id,
                "segment_role": str(item.get("segment_role") or "SCENE_SEGMENT"),
                "visual_purpose": str(item.get("visual_purpose") or item.get("visual_action") or ""),
                "narration_range": item.get("narration_range") if isinstance(item.get("narration_range"), dict) else {"full_text": str(item.get("narration") or "")},
                "subtitle_lines": item.get("subtitle_lines") if isinstance(item.get("subtitle_lines"), list) else [],
                "screen_text": normalize_screen_text_items(item.get("screen_text")),
                "visual_action": str(item.get("visual_action") or ""),
                "camera_motion": str(item.get("camera_motion") or ""),
                "transition_type": str(item.get("transition_type") or connection_type.lower()),
                "connection": {
                    "type": connection_type,
                    "render_method": str(connection.get("render_method") or render_method_for_connection(connection_type)),
                    "reason": str(connection.get("reason") or reason_for_connection(connection_type)),
                },
                "motion_prompt": str(item.get("motion_prompt") or ""),
                "must_keep_points": normalize_string_list(item.get("must_keep_points")),
                "quality_notes": normalize_string_list(item.get("quality_notes")),
            }
        )
    return segments


def merge_keyframe_prompts(reconstruction: dict[str, Any], prompt_payload: dict[str, Any]) -> None:
    prompt_lookup = {
        str(item.get("keyframe_id") or ""): item
        for item in (prompt_payload.get("keyframes") if isinstance(prompt_payload.get("keyframes"), list) else [])
        if isinstance(item, dict)
    }
    for keyframe in reconstruction.get("keyframes") or []:
        item = prompt_lookup.get(keyframe["keyframe_id"]) or {}
        keyframe["keyframe_prompt"] = str(item.get("keyframe_prompt") or keyframe.get("keyframe_prompt") or build_keyframe_prompt(scene=reconstruction, keyframe=keyframe))
        keyframe["negative_prompt"] = str(item.get("negative_prompt") or keyframe.get("negative_prompt") or negative_prompt_for_scene(reconstruction, keyframe))
        ensure_keyframe_prompt_contract(reconstruction, keyframe)


def ensure_keyframe_prompt_contract(scene: dict[str, Any], keyframe: dict[str, Any]) -> None:
    prompt = str(keyframe.get("keyframe_prompt") or "")
    marker_count = sum(1 for marker in KEYFRAME_PROMPT_REQUIRED_MARKERS if marker in prompt)
    if marker_count < 6 or keyframe_prompt_contains_motion(prompt):
        prompt = build_keyframe_prompt(scene=scene, keyframe=keyframe)
    elif MANDATORY_SINGLE_KEYFRAME_SENTENCE not in prompt:
        prompt = f"{prompt.rstrip('。')}。{MANDATORY_SINGLE_KEYFRAME_SENTENCE}"
    keyframe["keyframe_prompt"] = prompt
    keyframe["negative_prompt"] = ensure_negative_prompt_terms(scene, keyframe)


def ensure_negative_prompt_terms(scene: dict[str, Any], keyframe: dict[str, Any]) -> str:
    current = str(keyframe.get("negative_prompt") or "")
    pieces = split_negative_prompt_terms(current)
    current_text = "，".join(pieces)
    for term in split_negative_prompt_terms(negative_prompt_for_scene(scene, keyframe)):
        if term and term not in current_text:
            pieces.append(term)
            current_text = "，".join(pieces)
    return "，".join(dict.fromkeys(piece for piece in pieces if piece))


def split_negative_prompt_terms(value: Any) -> list[str]:
    return [part.strip() for part in re.split(r"[，,、]", str(value or "")) if part.strip()]


def keyframe_prompt_contains_motion(prompt: str) -> bool:
    text = str(prompt or "")
    return "动作：" in text or any(term in text for term in KEYFRAME_MOTION_FORBIDDEN_TERMS)


def repair_reconstruction_timing_and_visuals(reconstruction: dict[str, Any], *, scene: dict[str, Any]) -> None:
    segments = reconstruction.get("segments") if isinstance(reconstruction.get("segments"), list) else []
    keyframes = reconstruction.get("keyframes") if isinstance(reconstruction.get("keyframes"), list) else []
    duration = parse_duration_seconds(reconstruction.get("duration_sec") or scene.get("duration_sec"))
    if segments and should_rescale_segments(segments, duration):
        ranges = time_ranges(duration, len(segments))
        for segment, (start_sec, end_sec) in zip(segments, ranges):
            segment["start_sec"] = start_sec
            segment["end_sec"] = end_sec
            segment["duration_sec"] = max(0, end_sec - start_sec)
            for item in segment.get("screen_text") or []:
                if isinstance(item, dict):
                    item["start_sec"] = start_sec
                    item["end_sec"] = end_sec
            for item in segment.get("subtitle_lines") or []:
                if isinstance(item, dict):
                    item["start_sec"] = start_sec
                    item["end_sec"] = end_sec
    if keyframes:
        if segments and len(keyframes) == len(segments) + 1:
            keyframes[0]["time_sec"] = segments[0]["start_sec"]
            for index, segment in enumerate(segments, start=1):
                keyframes[index]["time_sec"] = segment["end_sec"]
        elif should_rescale_keyframes(keyframes, duration):
            ranges = time_ranges(duration, max(1, len(keyframes) - 1))
            keyframes[0]["time_sec"] = 0
            for index in range(1, len(keyframes)):
                keyframes[index]["time_sec"] = ranges[index - 1][1]
    source_beats = fit_items_to_count(visual_beats(scene), max(1, len(keyframes)))
    for index, keyframe in enumerate(keyframes):
        fallback = source_beats[min(index, len(source_beats) - 1)] if source_beats else scene.get("visual_description", "")
        prompt_hint = visual_hint_from_prompt(keyframe.get("keyframe_prompt"))
        if not keyframe.get("visual_content"):
            keyframe["visual_content"] = prompt_hint or fallback
        if not keyframe.get("visual_purpose"):
            keyframe["visual_purpose"] = preview_text(keyframe.get("visual_content"), limit=80)
        if not keyframe.get("screen_text"):
            keyframe["screen_text"] = distribute_items(scene.get("screen_text") or [], index=index + 1, count=max(1, len(keyframes)))
        if not keyframe.get("subtitle_text"):
            narration = distribute_items(scene.get("narration") or [], index=index + 1, count=max(1, len(keyframes)))
            keyframe["subtitle_text"] = narration[0] if narration else ""
    segment_beats = visual_segments_for_count(scene, max(1, len(segments)))
    for index, segment in enumerate(segments):
        fallback = segment_beats[min(index, len(segment_beats) - 1)] if segment_beats else scene.get("visual_description", "")
        repaired_visual = segment_visual_needs_repair(
            segment.get("visual_action"),
            expected=fallback,
            expected_segments=segment_beats,
            segment_index=index,
        )
        if repaired_visual:
            segment["visual_action"] = fallback
            segment["segment_role"] = segment_role_for_beat(fallback)
            segment["camera_motion"] = camera_for_text(fallback)
            connection_type = connection_type_for_beat(fallback)
            segment["transition_type"] = connection_type.lower()
            segment["connection"] = {
                "type": connection_type,
                "render_method": render_method_for_connection(connection_type),
                "reason": reason_for_connection(connection_type),
            }
        if repaired_visual or not segment.get("visual_purpose"):
            segment["visual_purpose"] = preview_text(segment.get("visual_action"), limit=80)
        narration_range = segment.get("narration_range") if isinstance(segment.get("narration_range"), dict) else {}
        expected_narration = narration_for_segment(scene, index=index + 1, count=max(1, len(segments)))
        if expected_narration:
            narration_range["full_text"] = expected_narration
            segment["narration_range"] = narration_range
        if expected_narration:
            segment["subtitle_lines"] = [
                {
                    "text": expected_narration,
                    "start_sec": segment.get("start_sec", 0),
                    "end_sec": segment.get("end_sec", 0),
                }
            ]


def should_rescale_segments(segments: list[dict[str, Any]], duration: int) -> bool:
    if not segments:
        return False
    starts = [parse_duration_seconds(segment.get("start_sec")) for segment in segments]
    ends = [parse_duration_seconds(segment.get("end_sec")) for segment in segments]
    if starts[0] != 0 or ends[-1] != duration:
        return True
    return any(end <= start for start, end in zip(starts, ends))


def should_rescale_keyframes(keyframes: list[dict[str, Any]], duration: int) -> bool:
    if not keyframes:
        return False
    times = [parse_duration_seconds(keyframe.get("time_sec")) for keyframe in keyframes]
    if times[0] != 0 or times[-1] != duration:
        return True
    return any(later < earlier for earlier, later in zip(times, times[1:]))


def visual_hint_from_prompt(prompt: Any) -> str:
    text = re.sub(r"\s+", " ", str(prompt or "")).strip()
    if not text:
        return ""
    for marker in ["画面：", "画面用途：", "画面主体：", "动作："]:
        if marker in text:
            after = text.split(marker, 1)[1]
            return preview_text(after.split("。", 1)[0], limit=120)
    return preview_text(text, limit=120)


def keyframe_shots_for_scene(
    reconstruction: dict[str, Any],
    *,
    scene_index: int,
    shot_offset: int,
    reconstruction_id: str,
) -> list[dict[str, Any]]:
    keyframes = reconstruction.get("keyframes") or []
    shots = []
    suffix = reconstruction_id.rsplit("-", 1)[-1][:8]
    for index, keyframe in enumerate(keyframes, start=1):
        next_keyframe = keyframes[index] if index < len(keyframes) else None
        duration = max(0.5, float((next_keyframe or {}).get("time_sec", keyframe.get("time_sec", 0)) or 0) - float(keyframe.get("time_sec") or 0))
        keyframe_id = keyframe["keyframe_id"]
        shots.append(
            {
                "shot_id": f"{keyframe_id}-{suffix}",
                "shot_index": shot_offset + index,
                "narration": str(keyframe.get("subtitle_text") or ""),
                "subtitle_text": str(keyframe.get("subtitle_text") or ""),
                "shot_type": shot_type_for_keyframe(keyframe),
                "visual_goal": str(keyframe.get("visual_purpose") or keyframe.get("visual_content") or ""),
                "scene_id": "",
                "scene_name": str(keyframe.get("environment") or ""),
                "subject_ids": [],
                "subject_names": normalize_string_list(keyframe.get("subjects")),
                "visual_elements": normalize_string_list(keyframe.get("visual_content")),
                "reference_assets": {},
                "camera": {"description": str(keyframe.get("camera_state") or "")},
                "duration_sec": duration,
                "keyframe_prompt": str(keyframe.get("keyframe_prompt") or ""),
                "video_prompt": "",
                "negative_prompt": str(keyframe.get("negative_prompt") or ""),
                "fact_safety_note": str((reconstruction.get("source_scene") or {}).get("fact_boundary") or "标准镜头生产稿，需人工核对史实边界。"),
                "asset_status": "missing_keyframe",
                "keyframe_asset_id": "",
                "video_asset_id": "",
                "needs_manual_review": not (reconstruction.get("validation") or {}).get("passed", False),
                "source_paragraph_index": scene_index,
                "source_text_start": int(keyframe.get("time_sec") or 0),
                "source_text_end": int((next_keyframe or keyframe).get("time_sec") or keyframe.get("time_sec") or 0),
                "source_excerpt": str(keyframe.get("visual_content") or ""),
                "is_supplemental": False,
                "supplemental_reason": "",
                "scene_block_id": reconstruction["scene_id"],
                "scene_block_title": reconstruction["title"],
                "scene_block_index": scene_index,
                "sequence_id": reconstruction["scene_id"],
                "sequence_title": reconstruction["title"],
                "beat_id": keyframe_id,
                "beat_title": str(keyframe.get("frame_role") or ""),
                "prev_shot_id": "",
                "next_shot_id": "",
                "transition": "keyframe_boundary",
                "continuity": keyframe.get("continuity") if isinstance(keyframe.get("continuity"), dict) else {},
                "production_plan": {
                    "render_method": "keyframe_image",
                    "needs_keyframe": True,
                    "needs_video": False,
                    "reason": "分镜重构中的关键帧任务，视频段落由 raw.scenes[].segments 定义。",
                },
                "prompt_parts": {},
                "raw": dict(keyframe),
            }
        )
    return shots


def scene_block_for_reconstruction(reconstruction: dict[str, Any], *, scene_index: int) -> dict[str, Any]:
    return {
        "scene_block_id": reconstruction["scene_id"],
        "scene_block_index": scene_index,
        "title": reconstruction["title"],
        "summary": preview_text((reconstruction.get("source_scene") or {}).get("visual_description"), limit=120),
        "dramatic_purpose": " / ".join(reconstruction.get("function") or []),
        "location": "",
        "time_context": "",
        "source_paragraph_indexes": [scene_index],
        "source_text_start": 0,
        "source_text_end": len(str((reconstruction.get("source_scene") or {}).get("visual_description") or "")),
        "source_excerpt": preview_text((reconstruction.get("source_scene") or {}).get("visual_description"), limit=220),
        "scene_ids": [],
        "scene_names": [],
        "key_beats": [segment.get("visual_action", "") for segment in (reconstruction.get("segments") or [])[:4]],
        "estimated_duration_sec": reconstruction.get("duration_sec") or 0,
        "shot_count": len(reconstruction.get("keyframes") or []),
        "keyframe_count": len(reconstruction.get("keyframes") or []),
        "segment_count": len(reconstruction.get("segments") or []),
        "bridge_strategy": (reconstruction.get("inter_scene_bridge") or {}).get("strategy") or "",
    }


def link_neighbor_shots(shots: list[dict[str, Any]]) -> None:
    for index, shot in enumerate(shots):
        if index > 0:
            shot["prev_shot_id"] = shots[index - 1]["shot_id"]
        if index < len(shots) - 1:
            shot["next_shot_id"] = shots[index + 1]["shot_id"]


def add_keyframe_continuity(keyframes: list[dict[str, Any]]) -> None:
    for index, keyframe in enumerate(keyframes):
        keyframe["continuity"] = {
            "from_previous_keyframe": keyframes[index - 1]["keyframe_id"] if index > 0 else None,
            "to_next_keyframe": keyframes[index + 1]["keyframe_id"] if index + 1 < len(keyframes) else None,
            **(keyframe.get("continuity") if isinstance(keyframe.get("continuity"), dict) else {}),
        }


def validation_notes(scenes: list[dict[str, Any]]) -> list[str]:
    notes = []
    for scene in scenes:
        validation = scene.get("validation") if isinstance(scene.get("validation"), dict) else {}
        for issue in validation.get("issues") or []:
            notes.append(f"{scene.get('scene_id')}: {issue}")
    return notes


def write_reconstruction_files(payload: dict[str, Any], *, output_dir: Path) -> None:
    reconstruction_id = payload["reconstruction_id"]
    target = output_dir / "scene_reconstructions" / reconstruction_id
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "scene_reconstruction.json"
    markdown_path = target / "scene_reconstruction.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_reconstruction_markdown(payload), encoding="utf-8")
    payload["json_path"] = str(json_path)
    payload["markdown_path"] = str(markdown_path)


def render_reconstruction_markdown(payload: dict[str, Any]) -> str:
    lines = [f"# {payload.get('source_title') or '分镜重构'}", ""]
    for scene in payload.get("scenes") or []:
        lines.extend([f"## {scene.get('scene_id')}｜{scene.get('title')}", ""])
        decision = scene.get("reconstruction_decision") or {}
        lines.append(f"- Segments: {decision.get('segment_count')}")
        lines.append(f"- Keyframes: {decision.get('keyframe_count')}")
        lines.append(f"- Bridge: {decision.get('bridge_strategy')}")
        lines.append("")
        for keyframe in scene.get("keyframes") or []:
            lines.append(f"### {keyframe.get('keyframe_id')} {keyframe.get('frame_role')}")
            lines.append(keyframe.get("visual_content") or "")
            lines.append("")
        for segment in scene.get("segments") or []:
            connection = segment.get("connection") or {}
            lines.append(f"- {segment.get('segment_id')}: {segment.get('from_keyframe_id')} -> {segment.get('to_keyframe_id')} ({connection.get('type')})")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def summarize_neighbor_scene(scene: dict[str, Any]) -> dict[str, Any]:
    return {
        "scene_id": scene["scene_id"],
        "title": scene["title"],
        "opening_visual_summary": preview_text(scene.get("visual_description"), limit=120),
        "screen_text": scene.get("screen_text") or [],
        "scene_type": scene.get("scene_type") or [],
    }


def director_output_schema() -> dict[str, Any]:
    return {
        "scene_id": "S01",
        "title": "...",
        "duration_sec": 25,
        "reconstruction_decision": {
            "segment_count": 4,
            "keyframe_count": 5,
            "needs_inter_scene_bridge": True,
            "bridge_strategy": "GENERATE_BRIDGE_KEYFRAME",
            "reason": "...",
        },
        "keyframes": [{"keyframe_id": "S01-KF01", "time_sec": 0, "frame_role": "OPENING_ANCHOR"}],
        "segments": [{"segment_id": "S01-SEG01", "from_keyframe_id": "S01-KF01", "to_keyframe_id": "S01-KF02"}],
        "inter_scene_bridge": {"needed": True, "strategy": "GENERATE_BRIDGE_KEYFRAME", "reason": "..."},
    }


def fallback_keyframes(scene: dict[str, Any]) -> list[dict[str, Any]]:
    ranges = time_ranges(scene["duration_sec"], 2)
    return [
        {"keyframe_id": f"{scene['scene_id']}-KF01", "time_sec": 0, "frame_role": "OPENING_ANCHOR", "visual_content": scene["visual_description"]},
        {"keyframe_id": f"{scene['scene_id']}-KF02", "time_sec": ranges[0][1], "frame_role": "MIDPOINT_ANCHOR", "visual_content": scene["visual_description"]},
        {"keyframe_id": f"{scene['scene_id']}-KF03", "time_sec": scene["duration_sec"], "frame_role": "ENDING_ANCHOR", "visual_content": scene["visual_description"]},
    ]


def fallback_segments(scene: dict[str, Any], keyframes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "segment_id": f"{scene['scene_id']}-SEG01",
            "start_sec": 0,
            "end_sec": scene["duration_sec"],
            "from_keyframe_id": keyframes[0]["keyframe_id"],
            "to_keyframe_id": keyframes[-1]["keyframe_id"],
            "visual_action": scene["visual_description"],
            "connection": {"type": "IMAGE_TO_VIDEO"},
        }
    ]


def visual_beats(scene: dict[str, Any]) -> list[str]:
    text = str(scene.get("visual_description") or "")
    beats = []
    for line in text.splitlines():
        beats.extend(split_visual_sentence(line))
    return [beat for beat in beats if beat] or scene.get("narration") or ["建立本镜头核心画面。"]


def split_visual_sentence(text: str) -> list[str]:
    parts = [part.strip(" 。；") for part in re.split(r"(?<=[。！？；])\s*", str(text or "")) if part.strip(" 。；")]
    beats = []
    for part in parts:
        if "快闪" in part and "：" in part:
            prefix, rest = part.split("：", 1)
            items = [item.strip(" 。；") for item in re.split(r"[、,，]", rest) if item.strip(" 。；")]
            beats.extend([f"{prefix.strip()}：{item}" for item in items] if items else [part])
        else:
            beats.append(part)
    return beats


def build_scene_blueprint(scene: dict[str, Any]) -> dict[str, Any]:
    visual_units = source_visual_units(scene)
    narration_units = source_narration_units(scene)
    count = desired_blueprint_segment_count(scene, visual_units)
    segments = source_segment_blueprints(scene, visual_units, narration_units, count)
    keyframes = source_keyframe_blueprints(scene, segments)
    return {
        "source_analysis": {
            "visual_units": visual_units,
            "narration_units": narration_units,
            "screen_text_units": [
                {"unit_id": f"{scene['scene_id']}-TU{index:02d}", "text": text}
                for index, text in enumerate(scene.get("screen_text") or [], start=1)
            ],
            "must_keep_units": [
                {"unit_id": f"{scene['scene_id']}-MU{index:02d}", "text": text}
                for index, text in enumerate(scene.get("must_keep_points") or [], start=1)
            ],
        },
        "segments": segments,
        "keyframes": keyframes,
    }


def source_visual_units(scene: dict[str, Any]) -> list[dict[str, Any]]:
    units = []
    for index, beat in enumerate(visual_beats(scene), start=1):
        units.append(
            {
                "unit_id": f"{scene['scene_id']}-VU{index:02d}",
                "text": beat,
                "semantic_role": semantic_group_for_beat(beat),
            }
        )
    return units or [{"unit_id": f"{scene['scene_id']}-VU01", "text": "建立本镜头核心画面。", "semantic_role": "other"}]


def source_narration_units(scene: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"unit_id": f"{scene['scene_id']}-NU{index:02d}", "text": text}
        for index, text in enumerate(scene.get("narration") or [], start=1)
    ]


def desired_blueprint_segment_count(scene: dict[str, Any], visual_units: list[dict[str, Any]]) -> int:
    duration = parse_duration_seconds(scene.get("duration_sec"))
    min_count, max_count = segment_count_bounds(duration)
    grouped = grouped_source_units(visual_units)
    count = max(1, len(grouped))
    if len(visual_units) >= min_count:
        count = max(count, min_count)
    return min(max_count, count)


def source_segment_blueprints(
    scene: dict[str, Any],
    visual_units: list[dict[str, Any]],
    narration_units: list[dict[str, Any]],
    count: int,
) -> list[dict[str, Any]]:
    count = max(1, count)
    groups = fit_source_unit_groups_to_count(grouped_source_units(visual_units), count)
    ranges = time_ranges(parse_duration_seconds(scene.get("duration_sec")), len(groups))
    narration_groups = partition_units_for_count(narration_units, len(groups))
    segments = []
    for index, (group, time_range) in enumerate(zip(groups, ranges), start=1):
        start_sec, end_sec = time_range
        visual_text = "；".join(unit["text"] for unit in group["units"])
        narration_text = "；".join(unit["text"] for unit in narration_groups[index - 1]) if narration_groups else ""
        connection_type = connection_type_for_beat(visual_text)
        segments.append(
            {
                "segment_id": f"{scene['scene_id']}-SEG{index:02d}",
                "scene_id": scene["scene_id"],
                "start_sec": start_sec,
                "end_sec": end_sec,
                "duration_sec": max(0, end_sec - start_sec),
                "from_keyframe_id": f"{scene['scene_id']}-KF{index:02d}",
                "to_keyframe_id": f"{scene['scene_id']}-KF{index + 1:02d}",
                "semantic_role": group["semantic_role"],
                "segment_role": segment_role_for_beat(visual_text),
                "source_visual_unit_ids": [unit["unit_id"] for unit in group["units"]],
                "source_narration_unit_ids": [unit["unit_id"] for unit in narration_groups[index - 1]] if narration_groups else [],
                "visual_action_seed": visual_text,
                "narration_seed": narration_text,
                "screen_text_seed": distribute_items(scene.get("screen_text") or [], index=index, count=len(groups)),
                "must_keep_seed": distribute_items(scene.get("must_keep_points") or [], index=index, count=len(groups)),
                "connection_type": connection_type,
                "connection_reason": reason_for_connection(connection_type),
                "motion_prompt_seed": build_segment_motion_prompt(
                    visual_text=visual_text,
                    connection_type=connection_type,
                    from_keyframe_id=f"{scene['scene_id']}-KF{index:02d}",
                    to_keyframe_id=f"{scene['scene_id']}-KF{index + 1:02d}",
                ),
            }
        )
    return segments


def build_segment_motion_prompt(
    *,
    visual_text: str,
    connection_type: str,
    from_keyframe_id: str,
    to_keyframe_id: str,
) -> str:
    return (
        f"从 {from_keyframe_id} 过渡到 {to_keyframe_id}。"
        f"视频段画面演绎：{visual_text}。"
        f"连接方式：{connection_type}，{reason_for_connection(connection_type)}"
    )


def static_keyframe_seed_for_segment(segment: dict[str, Any], *, scene: dict[str, Any], boundary: str) -> str:
    visual_text = str(segment.get("visual_action_seed") or scene.get("visual_description") or "")
    parts = [part.strip() for part in visual_text.split("；") if part.strip()]
    selected = parts[0] if boundary == "start" and parts else parts[-1] if parts else visual_text
    return static_frame_from_visual_text(selected, boundary=boundary, semantic_role=str(segment.get("semantic_role") or ""))


def static_frame_from_visual_text(text: str, *, boundary: str = "end", semantic_role: str = "") -> str:
    clean = str(text or "").strip(" 。；")
    if not clean:
        return "主体清晰的静态定格画面。"
    if all(marker in clean for marker in ["地球", "非洲"]) and any(marker in clean for marker in ["太空", "推进", "旋转"]):
        if boundary == "start":
            return "宇宙视角下的地球定格在画面中央，非洲大陆清晰可见，东非区域用柔和光圈标注。"
        return "东非稀树草原的开阔定格画面，金色草地、稀疏树木和低矮地平线清晰可见。"
    if "最后全部画面缩回" in clean and "黑板" in clean:
        return static_blackboard_text(clean)
    if "黑板" in clean:
        return static_blackboard_text(clean)
    if "卫星" in clean and "地球" in clean:
        return "地球外侧轨道旁的卫星定格画面，地球作为背景，画面具有科普漫画感。"
    if "火箭" in clean:
        return "火箭离开发射塔的一瞬间定格，尾焰和烟雾以漫画化方式凝固在画面中。"
    if "潜水器" in clean:
        return "深海潜水器悬停在幽蓝海水中的定格画面，周围有少量气泡和海底轮廓。"
    if "城市夜景" in clean:
        return "现代城市夜景的定格画面，楼宇灯光和道路光带形成强烈文明反差。"
    if "快闪" in clean:
        clean = clean.split("：", 1)[-1] if "：" in clean else clean.replace("快闪", "")
    if "逐渐重叠" in clean and "谱系线" in clean:
        return "早期智人轮廓与现代人轮廓重叠后的静态画面，中间形成一条发光的人类谱系线。"
    if "变成" in clean and "谱系线" in clean:
        return "一条发光的人类谱系线位于画面中央，连接早期智人与现代人剪影。"
    return sanitize_static_frame_text(clean)


def static_blackboard_text(text: str) -> str:
    after = text.split("黑板", 1)[-1].strip(" ，。：:；")
    label = after or "关键问题"
    label = label.replace("上写着", "写着").replace("出现", "写着").strip(" ，。：:；")
    return f"虚拟老师身后的黑板静态定格，黑板{label}，画面清楚聚焦文字信息。"


def sanitize_static_frame_text(text: str) -> str:
    replacements = {
        "地球从太空中缓慢旋转，": "宇宙视角下的地球，",
        "地球从太空中缓慢旋转": "宇宙视角下的地球",
        "镜头推进到": "画面中可见",
        "镜头推进": "画面定格",
        "再落到": "画面落点是",
        "缓慢旋转": "静止呈现",
        "画面突然快闪：": "",
        "画面快闪：": "",
        "快闪：": "",
        "快闪": "",
        "最后全部画面缩回": "画面定格在",
        "逐渐": "",
        "依次出现": "排列在远处",
    }
    clean = str(text or "")
    for old, new in replacements.items():
        clean = clean.replace(old, new)
    clean = re.sub(r"\s+", " ", clean).strip(" ，。：:；。")
    return clean or "主体清晰的静态定格画面"


def prompt_clause(value: Any, fallback: str = "") -> str:
    text = re.sub(r"\s+", " ", str(value or fallback or "")).strip(" 。；")
    return text or fallback


def source_keyframe_blueprints(scene: dict[str, Any], segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not segments:
        return []
    keyframes = []
    for index in range(len(segments) + 1):
        segment = segments[0] if index == 0 else segments[index - 1]
        time_sec = segments[0]["start_sec"] if index == 0 else segment["end_sec"]
        boundary = "start" if index == 0 else "end"
        visual_content = static_keyframe_seed_for_segment(segment, scene=scene, boundary=boundary)
        keyframes.append(
            {
                "keyframe_id": f"{scene['scene_id']}-KF{index + 1:02d}",
                "scene_id": scene["scene_id"],
                "time_sec": time_sec,
                "position": "scene_start" if index == 0 else "scene_end" if index == len(segments) else "segment_boundary",
                "frame_role": frame_role_for_index(index, len(segments), visual_content),
                "visual_content_seed": visual_content,
                "visual_purpose_seed": preview_text(visual_content, limit=80),
                "source_visual_unit_ids": segment.get("source_visual_unit_ids") or [],
                "static_boundary": boundary,
                "screen_text_seed": distribute_items(scene.get("screen_text") or [], index=index + 1, count=len(segments) + 1),
            }
        )
    return keyframes


def grouped_source_units(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for unit in units:
        role = str(unit.get("semantic_role") or "other")
        if groups and groups[-1]["semantic_role"] == role:
            groups[-1]["units"].append(unit)
        else:
            groups.append({"semantic_role": role, "units": [unit]})
    return groups


def fit_source_unit_groups_to_count(groups: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    fitted = [{"semantic_role": group["semantic_role"], "units": list(group["units"])} for group in groups]
    while len(fitted) > count:
        merge_index = merge_index_for_source_groups(fitted)
        fitted[merge_index]["units"].extend(fitted.pop(merge_index + 1)["units"])
        fitted[merge_index]["semantic_role"] = dominant_semantic_role(fitted[merge_index]["units"])
    while len(fitted) < count and any(len(group["units"]) > 1 for group in fitted):
        splittable_indexes = [
            index
            for index, group in enumerate(fitted)
            if len(group["units"]) > 1 and group["semantic_role"] not in {"montage", "reveal"}
        ]
        if not splittable_indexes:
            splittable_indexes = [index for index, group in enumerate(fitted) if len(group["units"]) > 1]
        split_index = max(splittable_indexes, key=lambda idx: len(fitted[idx]["units"]))
        units = fitted[split_index]["units"]
        midpoint = max(1, len(units) // 2)
        left = units[:midpoint]
        right = units[midpoint:]
        fitted[split_index] = {"semantic_role": dominant_semantic_role(left), "units": left}
        fitted.insert(split_index + 1, {"semantic_role": dominant_semantic_role(right), "units": right})
    return fitted[:count]


def merge_index_for_source_groups(groups: list[dict[str, Any]]) -> int:
    if len(groups) <= 1:
        return 0
    scores = []
    for index in range(len(groups) - 1):
        pair_len = sum(len(str(unit.get("text") or "")) for unit in groups[index]["units"] + groups[index + 1]["units"])
        same_role_bonus = -100 if groups[index]["semantic_role"] == groups[index + 1]["semantic_role"] else 0
        scores.append((pair_len + same_role_bonus, index))
    return min(scores)[1]


def dominant_semantic_role(units: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for unit in units:
        role = str(unit.get("semantic_role") or "other")
        counts[role] = counts.get(role, 0) + 1
    return max(counts, key=counts.get) if counts else "other"


def partition_units_for_count(units: list[dict[str, Any]], count: int) -> list[list[dict[str, Any]]]:
    if count <= 0:
        return []
    if not units:
        return [[] for _ in range(count)]
    if len(units) <= count:
        groups = [[unit] for unit in units]
        while len(groups) < count:
            groups.append([])
        return groups
    groups = [[unit] for unit in units[: count - 1]]
    groups.append(units[count - 1 :])
    return groups


def apply_scene_blueprint(reconstruction: dict[str, Any], blueprint: dict[str, Any], *, scene: dict[str, Any]) -> None:
    segment_blueprints = blueprint.get("segments") if isinstance(blueprint.get("segments"), list) else []
    keyframe_blueprints = blueprint.get("keyframes") if isinstance(blueprint.get("keyframes"), list) else []
    reconstruction["source_analysis"] = blueprint.get("source_analysis") or {}
    reconstruction["segment_blueprint"] = segment_blueprints
    reconstruction["keyframe_blueprint"] = keyframe_blueprints
    existing_keyframes = reconstruction.get("keyframes") if isinstance(reconstruction.get("keyframes"), list) else []
    existing_segments = reconstruction.get("segments") if isinstance(reconstruction.get("segments"), list) else []
    reconstruction["keyframes"] = apply_keyframe_blueprint(existing_keyframes, keyframe_blueprints, scene=scene)
    reconstruction["segments"] = apply_segment_blueprint(existing_segments, segment_blueprints, scene=scene)
    decision = reconstruction.get("reconstruction_decision") if isinstance(reconstruction.get("reconstruction_decision"), dict) else {}
    decision["segment_count"] = len(reconstruction["segments"])
    decision["keyframe_count"] = len(reconstruction["keyframes"])
    reconstruction["reconstruction_decision"] = decision
    add_keyframe_continuity(reconstruction["keyframes"])


def apply_keyframe_blueprint(
    existing_keyframes: list[dict[str, Any]],
    blueprints: list[dict[str, Any]],
    *,
    scene: dict[str, Any],
) -> list[dict[str, Any]]:
    by_id = {str(item.get("keyframe_id") or ""): item for item in existing_keyframes if isinstance(item, dict)}
    result = []
    for index, blueprint in enumerate(blueprints):
        existing = by_id.get(str(blueprint.get("keyframe_id") or "")) or (
            existing_keyframes[index] if index < len(existing_keyframes) and isinstance(existing_keyframes[index], dict) else {}
        )
        keyframe = dict(existing)
        keyframe["keyframe_id"] = blueprint["keyframe_id"]
        keyframe["scene_id"] = scene["scene_id"]
        keyframe["time_sec"] = blueprint["time_sec"]
        keyframe["position"] = blueprint["position"]
        keyframe["frame_role"] = blueprint["frame_role"]
        keyframe["visual_content"] = str(blueprint.get("visual_content_seed") or keyframe.get("visual_content") or "")
        keyframe["visual_purpose"] = str(blueprint.get("visual_purpose_seed") or keyframe.get("visual_purpose") or "")
        keyframe["source_visual_unit_ids"] = blueprint.get("source_visual_unit_ids") or []
        keyframe["screen_text"] = normalize_string_list(blueprint.get("screen_text_seed") or keyframe.get("screen_text"))
        keyframe.setdefault("subjects", subjects_for_text(keyframe["visual_content"]))
        keyframe.setdefault("environment", environment_for_text(keyframe["visual_content"]))
        keyframe.setdefault("composition", "主体清晰，背景辅助叙事。")
        keyframe.setdefault("camera_state", camera_for_text(keyframe["visual_content"]))
        keyframe.setdefault("mood", mood_for_text(keyframe["visual_content"]))
        keyframe.setdefault("subtitle_text", "")
        keyframe.setdefault("continuity", {})
        keyframe.setdefault("keyframe_prompt", "")
        keyframe.setdefault("negative_prompt", "")
        keyframe.setdefault("asset", {"status": "not_generated", "candidate_images": [], "selected_image": ""})
        result.append(keyframe)
    return result


def apply_segment_blueprint(
    existing_segments: list[dict[str, Any]],
    blueprints: list[dict[str, Any]],
    *,
    scene: dict[str, Any],
) -> list[dict[str, Any]]:
    by_id = {str(item.get("segment_id") or ""): item for item in existing_segments if isinstance(item, dict)}
    result = []
    for index, blueprint in enumerate(blueprints):
        existing = by_id.get(str(blueprint.get("segment_id") or "")) or (
            existing_segments[index] if index < len(existing_segments) and isinstance(existing_segments[index], dict) else {}
        )
        connection_type = normalize_connection_type(blueprint.get("connection_type"))
        segment = dict(existing)
        model_visual_action = str(segment.get("visual_action") or "")
        segment.update(
            {
                "segment_id": blueprint["segment_id"],
                "scene_id": scene["scene_id"],
                "start_sec": blueprint["start_sec"],
                "end_sec": blueprint["end_sec"],
                "duration_sec": blueprint["duration_sec"],
                "from_keyframe_id": blueprint["from_keyframe_id"],
                "to_keyframe_id": blueprint["to_keyframe_id"],
                "segment_role": blueprint["segment_role"],
                "semantic_role": blueprint["semantic_role"],
                "source_visual_unit_ids": blueprint.get("source_visual_unit_ids") or [],
                "source_narration_unit_ids": blueprint.get("source_narration_unit_ids") or [],
                "visual_action": blueprint.get("visual_action_seed") or "",
                "visual_purpose": preview_text(blueprint.get("visual_action_seed"), limit=80),
                "narration_range": {"full_text": blueprint.get("narration_seed") or ""},
                "subtitle_lines": [
                    {
                        "text": blueprint.get("narration_seed") or "",
                        "start_sec": blueprint["start_sec"],
                        "end_sec": blueprint["end_sec"],
                    }
                ]
                if blueprint.get("narration_seed")
                else [],
                "screen_text": [
                    {"text": text, "start_sec": blueprint["start_sec"], "end_sec": blueprint["end_sec"], "position": "auto"}
                    for text in normalize_string_list(blueprint.get("screen_text_seed"))
                ],
                "must_keep_points": normalize_string_list(blueprint.get("must_keep_seed")),
                "camera_motion": camera_for_text(blueprint.get("visual_action_seed") or ""),
                "transition_type": connection_type.lower(),
                "connection": {
                    "type": connection_type,
                    "render_method": render_method_for_connection(connection_type),
                    "reason": blueprint.get("connection_reason") or reason_for_connection(connection_type),
                },
                "motion_prompt": str(blueprint.get("motion_prompt_seed") or segment.get("motion_prompt") or ""),
                "quality_notes": normalize_string_list(segment.get("quality_notes")),
            }
        )
        if model_visual_action and compact_text(model_visual_action) != compact_text(segment["visual_action"]):
            segment["model_visual_action"] = model_visual_action
        result.append(segment)
    return result


def visual_segments_for_count(scene: dict[str, Any], count: int) -> list[str]:
    count = max(1, count)
    units = source_visual_units(scene)
    groups = fit_source_unit_groups_to_count(grouped_source_units(units), count)
    return ["；".join(unit["text"] for unit in group["units"]) for group in groups]


def grouped_visual_beats(beats: list[str]) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    for beat in beats:
        group = semantic_group_for_beat(beat)
        if clusters and clusters[-1]["group"] == group:
            clusters[-1]["beats"].append(beat)
        else:
            clusters.append({"group": group, "beats": [beat]})
    return clusters


def semantic_group_for_beat(text: str) -> str:
    if any(marker in text for marker in ["黑板", "翻盘技能", "他们靠什么", "答案"]):
        return "reveal"
    if any(marker in text for marker in ["快闪", "火箭", "潜水器", "卫星", "城市夜景", "登月", "探海"]):
        return "montage"
    if any(marker in text for marker in ["早期智人", "瘦弱", "狮子", "鬣狗", "水牛", "羚羊", "压迫", "弱小", "弱鸡"]):
        return "weakness"
    if any(marker in text for marker in ["太空", "地球", "非洲", "草原", "推进", "旋转", "落到"]):
        return "establish"
    return "other"


def merge_index_for_visual_segments(items: list[str]) -> int:
    if len(items) <= 1:
        return 0
    pairs = [(len(items[index]) + len(items[index + 1]), index) for index in range(len(items) - 1)]
    return min(pairs)[1]


def split_index_for_visual_segments(items: list[str]) -> int:
    scored = []
    for index, item in enumerate(items):
        part_count = len([part for part in item.split("；") if part])
        scored.append((part_count, len(item), index))
    return max(scored)[2]


def segment_visual_needs_repair(
    value: Any,
    *,
    expected: str,
    expected_segments: list[str],
    segment_index: int,
) -> bool:
    text = compact_text(value)
    expected_text = compact_text(expected)
    if not text:
        return True
    if not expected_text or text == expected_text:
        return False
    if expected_text in text and len(text) > len(expected_text) + 20:
        return True
    for index, segment in enumerate(expected_segments):
        if index == segment_index:
            continue
        marker = compact_text(segment_marker(segment))
        if marker and marker in text and marker not in expected_text:
            return True
    if contains_unexpected_montage_marker(text, expected_text):
        return True
    return segment_semantic_group_conflicts(text, expected_text)


def contains_unexpected_montage_marker(text: str, expected_text: str) -> bool:
    markers = ["火箭", "潜水器", "卫星", "城市夜景", "登月", "探海", "快闪"]
    return any(marker in text and marker not in expected_text for marker in markers)


def segment_semantic_group_conflicts(actual_text: str, expected_text: str) -> bool:
    actual_group = semantic_group_for_beat(actual_text)
    expected_group = semantic_group_for_beat(expected_text)
    if actual_group == expected_group or "other" in {actual_group, expected_group}:
        return False
    strong_groups = {"montage", "reveal"}
    return actual_group in strong_groups or expected_group in strong_groups


def segment_marker(text: str) -> str:
    clean = str(text or "").strip()
    for separator in ["；", "。", "，", ","]:
        if separator in clean:
            clean = clean.split(separator, 1)[0]
            break
    return clean[:24]


def compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def desired_segment_count(scene: dict[str, Any], beats: list[str]) -> int:
    duration = parse_duration_seconds(scene.get("duration_sec"))
    min_count, max_count = segment_count_bounds(duration)
    bonus = 1 if any(tag in {"SYMBOLIC_MONTAGE", "INFOGRAPHIC", "COMPARISON_SPLIT_SCREEN"} for tag in scene.get("scene_type") or []) else 0
    return max(min_count, min(max_count, len(beats) + bonus))


def segment_count_bounds(duration: int) -> tuple[int, int]:
    if duration <= 15:
        return 2, 4
    if duration <= 30:
        return 4, 6
    return 5, 8


def fit_items_to_count(items: list[str], count: int) -> list[str]:
    fitted = list(items)
    while len(fitted) < count:
        fitted.append(fitted[-1] if fitted else "补足本镜头关键画面。")
    if len(fitted) <= count:
        return fitted
    return fitted[: count - 1] + ["；".join(fitted[count - 1 :])]


def time_ranges(duration: int, count: int) -> list[tuple[int, int]]:
    count = max(1, count)
    base = duration // count
    remainder = duration % count
    ranges = []
    cursor = 0
    for index in range(count):
        span = base + (1 if index < remainder else 0)
        end = duration if index == count - 1 else cursor + max(1, span)
        ranges.append((cursor, end))
        cursor = end
    return ranges


def distribute_items(items: list[str], *, index: int, count: int) -> list[str]:
    if not items:
        return []
    if count <= 1:
        return items[:2]
    bucket = min(len(items) - 1, max(0, round((index - 1) * (len(items) - 1) / max(1, count - 1))))
    return [items[bucket]]


def screen_text_copied_to_every_segment(segments: list[dict[str, Any]]) -> bool:
    if len(segments) <= 1:
        return False
    sets = []
    for segment in segments:
        texts = tuple(item.get("text") if isinstance(item, dict) else str(item) for item in segment.get("screen_text") or [])
        if texts:
            sets.append(texts)
    return len(sets) > 1 and len(set(sets)) == 1


def normalize_screen_text_items(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, list):
        items = []
        for item in value:
            if isinstance(item, dict):
                clean = dict(item)
                clean["text"] = str(clean.get("text") or "").strip()
                if clean["text"]:
                    items.append(clean)
            elif str(item).strip():
                items.append({"text": str(item).strip(), "start_sec": 0, "end_sec": 0, "position": "auto"})
        return items
    return [{"text": str(value).strip(), "start_sec": 0, "end_sec": 0, "position": "auto"}] if str(value).strip() else []


def build_keyframe_prompt(*, scene: dict[str, Any], keyframe: dict[str, Any]) -> str:
    screen_text = " / ".join(normalize_string_list(keyframe.get("screen_text"))) or "无，后期按需叠加字幕"
    must_keep = "；".join(normalize_string_list((scene.get("source_scene") or {}).get("must_keep_points") or scene.get("must_keep_points"))) or "体现本关键帧核心信息"
    static_visual = sanitize_static_frame_text(keyframe.get("visual_content") or "")
    visual_purpose = prompt_clause(keyframe.get("visual_purpose") or keyframe.get("frame_role"), "关键帧")
    environment = prompt_clause(keyframe.get("environment"), "符合历史科普短剧的简洁场景")
    composition = prompt_clause(keyframe.get("composition"), "主体清晰，背景辅助叙事")
    camera_state = prompt_clause(keyframe.get("camera_state"), "定格镜头，画面边界清楚，适合生成单张图")
    mood = prompt_clause(keyframe.get("mood"), "清楚、有故事感")
    return (
        "历史科普卡通短剧风格，半扁平漫画插画，不低幼，不是写实电影，也不是过度幼稚的扁平小人。"
        f"画面用途：{visual_purpose}。"
        "时代地点：历史科普叙事场景。"
        f"画面主体：{'、'.join(normalize_string_list(keyframe.get('subjects'))) or '按画面重点呈现主体'}。"
        f"环境背景：{environment}。"
        f"静态画面：{static_visual}。"
        f"构图：{composition}。"
        f"镜头语言：{camera_state}。"
        f"情绪氛围：{mood}。"
        f"屏幕文字：{screen_text}。"
        f"必须体现的信息：{must_keep}。"
        f"{DEFAULT_FRAME_SIZE_PROMPT}"
        "这是单张关键帧，只描述一个清晰瞬间，不要把多个独立事件硬塞进同一画面。"
    )


def negative_prompt_for_scene(scene: dict[str, Any], keyframe: dict[str, Any]) -> str:
    items = [BASE_NEGATIVE_PROMPT]
    scene_types = set(scene.get("scene_type") or [])
    content = str(keyframe.get("visual_content") or "")
    if scene_types & {"HISTORICAL_REENACTMENT", "HOST_OPENING"} or any(marker in content for marker in ["早期智人", "远古", "草原"]):
        items.append("现代城市、现代机器、现代武器、科幻界面")
    if scene_types & {"MAP_ANIMATION", "INFOGRAPHIC"} or any(marker in content for marker in ["地图", "地球", "非洲"]):
        items.append("复杂人物抢主体、真实卫星照片风格、过密文字")
    return "，".join(items)


def shot_type_for_keyframe(keyframe: dict[str, Any]) -> str:
    role = str(keyframe.get("frame_role") or "")
    if "MONTAGE" in role:
        return "montage_shot"
    if "OPENING" in role:
        return "hook_shot"
    return "narrative_shot"


def frame_role_for_index(index: int, count: int, text: str) -> str:
    if index == 0:
        return "OPENING_ANCHOR"
    if index == count:
        return "ENDING_ANCHOR"
    if "快闪" in text or "火箭" in text:
        return "MONTAGE_ANCHOR"
    if "黑板" in text:
        return "EXPLAINER_ANCHOR"
    return "SEGMENT_BOUNDARY"


def segment_role_for_beat(text: str) -> str:
    if any(marker in text for marker in ["地球", "非洲", "草原"]):
        return "ESTABLISH_TIME_PLACE"
    if any(marker in text for marker in ["狮子", "鬣狗", "压迫", "弱"]):
        return "WEAKNESS_CONTRAST"
    if any(marker in text for marker in ["快闪", "火箭", "潜水器", "卫星"]):
        return "MODERN_MONTAGE"
    if "黑板" in text:
        return "QUESTION_REVEAL"
    return "SCENE_BEAT"


def connection_type_for_beat(text: str) -> str:
    if any(marker in text for marker in ["快闪", "火箭", "潜水器", "卫星", "城市夜景"]):
        return "DIRECT_CUT"
    if any(marker in text for marker in ["黑板", "缩回", "箭头", "地图"]):
        return "GRAPHIC_WIPE"
    if any(marker in text for marker in ["推进", "旋转", "落到"]):
        return "CAMERA_MOTION"
    return "IMAGE_TO_VIDEO"


def normalize_connection_type(value: Any) -> str:
    text = str(value or "").upper()
    return text if text in ALLOWED_CONNECTION_TYPES else "IMAGE_TO_VIDEO"


def normalize_bridge_strategy(value: Any) -> str:
    text = str(value or "").upper()
    return text if text in ALLOWED_BRIDGE_STRATEGIES else "DIRECT_CUT"


def render_method_for_connection(connection_type: str) -> str:
    return {
        "DIRECT_CUT": "edit_cut",
        "CAMERA_MOTION": "image_to_video",
        "IMAGE_TO_VIDEO": "image_to_video",
        "MATCH_CUT": "edit_match_cut",
        "GRAPHIC_WIPE": "motion_graphics",
    }.get(connection_type, "image_to_video")


def reason_for_connection(connection_type: str) -> str:
    return {
        "DIRECT_CUT": "硬切更适合快闪、反差或节奏突变。",
        "CAMERA_MOTION": "同一空间内镜头运动可以保持连续。",
        "IMAGE_TO_VIDEO": "用前后关键帧生成连续演绎段。",
        "MATCH_CUT": "用构图或动作相似性连接两帧。",
        "GRAPHIC_WIPE": "用地图、黑板或图形元素完成信息转场。",
    }.get(connection_type, "根据画面连续性决定。")


def narration_for_segment(scene: dict[str, Any], *, index: int, count: int) -> str:
    items = fit_items_to_count(scene.get("narration") or [], count)
    if not items:
        return ""
    return items[max(0, min(index - 1, len(items) - 1))]


def subtitle_lines_for_segment(scene: dict[str, Any], *, index: int, count: int, start_sec: int, end_sec: int) -> list[dict[str, Any]]:
    text = narration_for_segment(scene, index=index, count=count)
    if not text:
        return []
    return [{"text": text, "start_sec": start_sec, "end_sec": end_sec}]


def should_bridge_to_next(scene: dict[str, Any], next_scene: dict[str, Any]) -> bool:
    current_text = " ".join(scene.get("screen_text") or []) + " " + scene.get("visual_description", "")
    next_text = " ".join(next_scene.get("screen_text") or []) + " " + next_scene.get("opening_visual_summary", "")
    return any(marker in current_text for marker in ["黑板", "问题", "他们靠什么"]) or bool(next_text and current_text and current_text[:20] != next_text[:20])


def subjects_for_text(text: str) -> list[str]:
    candidates = ["智人", "早期智人", "虚拟老师", "讲述人", "狮子", "鬣狗", "水牛", "羚羊"]
    return [candidate for candidate in candidates if candidate in text]


def environment_for_text(text: str) -> str:
    if "太空" in text or "地球" in text:
        return "太空与地球"
    if "非洲" in text or "草原" in text:
        return "东非稀树草原"
    if "黑板" in text:
        return "虚拟教室黑板"
    return ""


def camera_for_text(text: str) -> str:
    if "推进" in text:
        return "缓慢推进"
    if "快闪" in text:
        return "快速硬切"
    if "黑板" in text:
        return "稳定中景"
    return "轻微运动"


def mood_for_text(text: str) -> str:
    if any(marker in text for marker in ["压迫", "狮子", "鬣狗"]):
        return "有压迫感但不恐怖"
    if any(marker in text for marker in ["火箭", "潜水器", "卫星", "翻盘"]):
        return "反差强、有冲击力"
    return "清楚、有故事感"


def split_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in re.split(r"\s*\+\s*|[,，、/]+", str(value or "")) if item.strip()]


def split_function(value: Any) -> list[str]:
    return [item.strip() for item in re.split(r"[/／,，、]+", str(value or "")) if item.strip()]


def normalize_dialogue(value: Any) -> list[Any]:
    if not value:
        return []
    if isinstance(value, list):
        return value
    return normalize_string_list(value)


def normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[\n,，、]+", value) if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def parse_duration_seconds(value: Any) -> int:
    if value is None or str(value).strip() == "":
        return 25
    match = re.search(r"\d+", str(value))
    if not match:
        return 25
    return int(match.group(0))


def preview_text(value: Any, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
