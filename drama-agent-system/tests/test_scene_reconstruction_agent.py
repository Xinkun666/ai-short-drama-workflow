from __future__ import annotations

from drama_agents.scene_reconstruction_agent import (
    DeepSeekSceneReconstructionProvider,
    ParsedSceneNormalizer,
    SceneReconstructionPipeline,
    SceneReconstructionValidator,
)
from drama_agents.script_agent import parse_manual_storyboard_script


def sample_storyboard_text() -> str:
    return """
S01｜开场：地球上最会讲故事的动物
场景类型
HOST_OPENING + SYMBOLIC_MONTAGE
功能
开场钩子 / 提出核心问题
时长
25 秒
源稿
[1]
讲述人旁白
早在 7 万年前，非洲东部的稀树草原上，生活着一群看起来很不起眼的动物。

它们没有尖牙利爪，跑不过羚羊，力气不如水牛，在狮子和鬣狗眼里，简直就是行走的自助餐。

但你绝对想不到，就是这群草原弱鸡，后来上能登月，下能探海，把整个地球翻了个底朝天。

他们是怎么办到的？

答案简单到让你翻白眼：他们学会了讲故事。

画面演绎
地球从太空中缓慢旋转，镜头推进到非洲大陆，再落到东非稀树草原。
草丛里，一群瘦弱的早期智人小心移动。
远处狮子、鬣狗、水牛、羚羊依次出现，形成压迫感。
画面突然快闪：火箭升空、潜水器下潜、城市夜景、卫星环绕地球。
最后全部画面缩回虚拟老师身后的黑板，黑板上写着：翻盘技能：讲故事

屏幕文字
7 万年前

非洲东部

智人：开局很弱

他们靠什么翻盘？

历史人物对白
无。

保留支撑点
智人一开始并不强。

通过和动物对比建立“弱小开局”。

用登月、探海制造巨大反差。

本集核心问题：弱小智人为什么能改变地球？
""".strip()


def generic_storyboard_text() -> str:
    return """
S09｜盐铁为什么会被国家盯上
场景类型
MAP_ANIMATION + INFOGRAPHIC
功能
制度解释 / 建立经济逻辑
时长
24 秒
源稿
[3]
讲述人旁白
在古代，盐和铁不是普通商品。

盐人人要吃，铁能做农具和武器。

谁控制了盐铁，谁就抓住了财政和秩序的命门。

所以很多王朝都会把盐铁收归国家管理。

画面演绎
一张古代地图展开，镜头推到黄河沿岸城市。
市集里商人搬运盐袋，官员在账本上盖章。
画面快闪：盐井开采、铁炉燃烧、车队入城。
最后黑板上出现：国家为什么要管盐铁？

屏幕文字
盐

铁

财政命门

国家管控

历史人物对白
无。

保留支撑点
盐和铁是古代高频刚需资源。

盐铁关系到财政和秩序。

王朝管控盐铁有制度逻辑。
""".strip()


class FakeDirectorProvider:
    model = "fake-director"

    def reconstruct_scene(self, context):
        scene = context["current_scene"]
        return {
            "scene_id": scene["scene_id"],
            "title": scene["title"],
            "duration_sec": 25,
            "reconstruction_decision": {
                "segment_count": 4,
                "keyframe_count": 5,
                "needs_inter_scene_bridge": True,
                "bridge_strategy": "GENERATE_BRIDGE_KEYFRAME",
                "reason": "太空、草原、弱小对比、现代成就快闪和黑板收束需要分段。",
            },
            "keyframes": [
                {
                    "keyframe_id": "S01-KF01",
                    "time_sec": 0,
                    "position": "scene_start",
                    "frame_role": "OPENING_ANCHOR",
                    "visual_purpose": "建立时间地点",
                    "visual_content": "太空中的地球，非洲大陆可见。",
                    "screen_text": ["7 万年前"],
                    "subtitle_text": scene["narration"][0],
                },
                {
                    "keyframe_id": "S01-KF02",
                    "time_sec": 6,
                    "position": "segment_boundary",
                    "frame_role": "LOCATION_ANCHOR",
                    "visual_purpose": "落到东非草原",
                    "visual_content": "东非稀树草原和早期智人剪影。",
                    "screen_text": ["非洲东部"],
                    "subtitle_text": scene["narration"][1],
                },
                {
                    "keyframe_id": "S01-KF03",
                    "time_sec": 12,
                    "position": "segment_boundary",
                    "frame_role": "CONTRAST_ANCHOR",
                    "visual_purpose": "建立弱小对比",
                    "visual_content": "早期智人被远处动物压迫。",
                    "screen_text": ["智人：开局很弱"],
                    "subtitle_text": scene["narration"][1],
                },
                {
                    "keyframe_id": "S01-KF04",
                    "time_sec": 18,
                    "position": "segment_boundary",
                    "frame_role": "MONTAGE_ANCHOR",
                    "visual_purpose": "现代成就快闪",
                    "visual_content": "火箭、潜水器、城市和卫星的漫画分格。",
                    "screen_text": [],
                    "subtitle_text": scene["narration"][2],
                },
                {
                    "keyframe_id": "S01-KF05",
                    "time_sec": 25,
                    "position": "scene_end",
                    "frame_role": "ENDING_ANCHOR",
                    "visual_purpose": "黑板收束问题",
                    "visual_content": "虚拟老师身后黑板写着翻盘技能：讲故事。",
                    "screen_text": ["他们靠什么翻盘？"],
                    "subtitle_text": scene["narration"][-1],
                },
            ],
            "segments": [
                {
                    "segment_id": "S01-SEG01",
                    "start_sec": 0,
                    "end_sec": 6,
                    "from_keyframe_id": "S01-KF01",
                    "to_keyframe_id": "S01-KF02",
                    "segment_role": "ESTABLISH_TIME_PLACE",
                    "visual_action": "从太空推进到非洲东部。",
                    "screen_text": [{"text": "7 万年前", "start_sec": 0.5, "end_sec": 3, "position": "top_left"}],
                    "connection": {"type": "CAMERA_MOTION", "render_method": "image_to_video", "reason": "地理推进需要连续运动。"},
                },
                {
                    "segment_id": "S01-SEG02",
                    "start_sec": 6,
                    "end_sec": 12,
                    "from_keyframe_id": "S01-KF02",
                    "to_keyframe_id": "S01-KF03",
                    "segment_role": "WEAKNESS_CONTRAST",
                    "visual_action": "早期智人和动物形成压迫对比。",
                    "screen_text": [{"text": "智人：开局很弱", "start_sec": 8, "end_sec": 11, "position": "bottom"}],
                    "connection": {"type": "IMAGE_TO_VIDEO", "render_method": "image_to_video", "reason": "同一草原空间内连续演绎。"},
                },
                {
                    "segment_id": "S01-SEG03",
                    "start_sec": 12,
                    "end_sec": 18,
                    "from_keyframe_id": "S01-KF03",
                    "to_keyframe_id": "S01-KF04",
                    "segment_role": "MODERN_MONTAGE",
                    "visual_action": "现代成就快闪形成反差。",
                    "screen_text": [],
                    "connection": {"type": "DIRECT_CUT", "render_method": "edit_cut", "reason": "快闪蒙太奇硬切更有节奏。"},
                },
                {
                    "segment_id": "S01-SEG04",
                    "start_sec": 18,
                    "end_sec": 25,
                    "from_keyframe_id": "S01-KF04",
                    "to_keyframe_id": "S01-KF05",
                    "segment_role": "QUESTION_REVEAL",
                    "visual_action": "所有画面收回黑板揭示问题。",
                    "screen_text": [{"text": "他们靠什么翻盘？", "start_sec": 22, "end_sec": 25, "position": "center"}],
                    "connection": {"type": "GRAPHIC_WIPE", "render_method": "motion_graphics", "reason": "用黑板收束信息。"},
                },
            ],
            "inter_scene_bridge": {
                "needed": True,
                "strategy": "GENERATE_BRIDGE_KEYFRAME",
                "reason": "黑板问题可以作为下一镜头解释入口。",
                "bridge_keyframe": {"keyframe_id": "S01-BRIDGE-KF01"},
                "bridge_segment": {},
            },
        }


class FakeKeyframePromptProvider:
    model = "fake-keyframe"

    def generate_keyframe_prompts(self, *, scene, reconstruction):
        return {
            "scene_id": scene["scene_id"],
            "keyframes": [
                {
                    "keyframe_id": keyframe["keyframe_id"],
                    "keyframe_prompt": f"画面用途：{keyframe['visual_purpose']}。时代地点：7万年前非洲东部。画面主体：早期智人。环境背景：历史科普卡通短剧风格。动作：{keyframe['visual_content']}。构图：主体清晰。镜头语言：稳定镜头。情绪氛围：清楚、有故事感。屏幕文字：{','.join(keyframe.get('screen_text') or []) or '无'}。必须体现的信息：弱小智人改变地球。这是单张关键帧，只描述一个清晰瞬间，不要把多个独立事件硬塞进同一画面。",
                    "negative_prompt": "写实照片、电影剧照、过度低幼、3D塑料感、现代建筑、现代服装、现代载具、血腥暴力、恐怖画面、文字乱码、水印、logo",
                }
                for keyframe in reconstruction["keyframes"]
            ],
        }


class SparseDirectorProvider:
    model = "sparse-director"

    def reconstruct_scene(self, context):
        scene = context["current_scene"]
        return {
            "scene_id": scene["scene_id"],
            "title": scene["title"],
            "duration_sec": 25,
            "reconstruction_decision": {
                "segment_count": 2,
                "keyframe_count": 3,
                "needs_inter_scene_bridge": False,
                "bridge_strategy": "DIRECT_CUT",
                "reason": "模型返回了占位时间和空视觉字段，后端需要修复。",
            },
            "keyframes": [
                {"keyframe_id": "S01-KF01", "time_sec": 0, "frame_role": "OPENING_ANCHOR"},
                {"keyframe_id": "S01-KF02", "time_sec": 1, "frame_role": "MIDPOINT"},
                {"keyframe_id": "S01-KF03", "time_sec": 1, "frame_role": "ENDING_ANCHOR"},
            ],
            "segments": [
                {"segment_id": "S01-SEG01", "start_sec": 0, "end_sec": 1, "from_keyframe_id": "S01-KF01", "to_keyframe_id": "S01-KF02", "connection": {"type": "CAMERA_MOTION"}},
                {"segment_id": "S01-SEG02", "start_sec": 0, "end_sec": 1, "from_keyframe_id": "S01-KF02", "to_keyframe_id": "S01-KF03", "connection": {"type": "DIRECT_CUT"}},
            ],
            "inter_scene_bridge": {"needed": False, "strategy": "DIRECT_CUT", "reason": "直接切。"},
        }


class OverstuffedDirectorProvider:
    model = "overstuffed-director"

    def reconstruct_scene(self, context):
        scene = context["current_scene"]
        repeated_prefix = (
            "地球从太空中缓慢旋转，镜头推进到非洲大陆，再落到东非稀树草原。"
            "草丛里，一群瘦弱的早期智人小心移动。"
            "远处狮子、鬣狗、水牛、羚羊依次出现，形成压迫感。 画面突然快闪："
        )
        return {
            "scene_id": scene["scene_id"],
            "title": scene["title"],
            "duration_sec": 25,
            "reconstruction_decision": {
                "segment_count": 4,
                "keyframe_count": 5,
                "needs_inter_scene_bridge": True,
                "bridge_strategy": "GENERATE_BRIDGE_KEYFRAME",
                "reason": "模型把整段画面演绎重复塞进了多个 segment。",
            },
            "keyframes": [
                {"keyframe_id": "S01-KF01", "time_sec": 0, "frame_role": "OPENING_ANCHOR"},
                {"keyframe_id": "S01-KF02", "time_sec": 7, "frame_role": "LOCATION_ANCHOR"},
                {"keyframe_id": "S01-KF03", "time_sec": 13, "frame_role": "CONTRAST_ANCHOR"},
                {"keyframe_id": "S01-KF04", "time_sec": 19, "frame_role": "MONTAGE_ANCHOR"},
                {"keyframe_id": "S01-KF05", "time_sec": 25, "frame_role": "ENDING_ANCHOR"},
            ],
            "segments": [
                {
                    "segment_id": "S01-SEG01",
                    "start_sec": 0,
                    "end_sec": 7,
                    "from_keyframe_id": "S01-KF01",
                    "to_keyframe_id": "S01-KF02",
                    "visual_action": repeated_prefix + "火箭升空",
                    "narration_range": {"full_text": scene["narration"][0]},
                    "connection": {"type": "CAMERA_MOTION"},
                },
                {
                    "segment_id": "S01-SEG02",
                    "start_sec": 7,
                    "end_sec": 13,
                    "from_keyframe_id": "S01-KF02",
                    "to_keyframe_id": "S01-KF03",
                    "visual_action": repeated_prefix + "潜水器下潜",
                    "narration_range": {"full_text": scene["narration"][1]},
                    "connection": {"type": "DIRECT_CUT"},
                },
                {
                    "segment_id": "S01-SEG03",
                    "start_sec": 13,
                    "end_sec": 19,
                    "from_keyframe_id": "S01-KF03",
                    "to_keyframe_id": "S01-KF04",
                    "visual_action": repeated_prefix + "城市夜景",
                    "narration_range": {"full_text": "他们是怎么办到的？"},
                    "connection": {"type": "GRAPHIC_WIPE"},
                },
                {
                    "segment_id": "S01-SEG04",
                    "start_sec": 19,
                    "end_sec": 25,
                    "from_keyframe_id": "S01-KF04",
                    "to_keyframe_id": "S01-KF05",
                    "visual_action": repeated_prefix + "卫星环绕地球。最后全部画面缩回虚拟老师身后的黑板，黑板上写着：翻盘技能：讲故事",
                    "narration_range": {"full_text": scene["narration"][-1]},
                    "connection": {"type": "DIRECT_CUT"},
                },
            ],
            "inter_scene_bridge": {
                "needed": True,
                "strategy": "GENERATE_BRIDGE_KEYFRAME",
                "reason": "黑板承接下一场。",
            },
        }


class RoleMismatchedDirectorProvider:
    model = "role-mismatched-director"

    def reconstruct_scene(self, context):
        scene = context["current_scene"]
        return {
            "scene_id": scene["scene_id"],
            "title": scene["title"],
            "duration_sec": 25,
            "reconstruction_decision": {
                "segment_count": 4,
                "keyframe_count": 5,
                "needs_inter_scene_bridge": True,
                "bridge_strategy": "GENERATE_BRIDGE_KEYFRAME",
                "reason": "模型把第三段反差蒙太奇误写成了黑板收束。",
            },
            "keyframes": [
                {"keyframe_id": "S01-KF01", "time_sec": 0, "frame_role": "OPENING_ANCHOR"},
                {"keyframe_id": "S01-KF02", "time_sec": 7, "frame_role": "LOCATION_ANCHOR"},
                {"keyframe_id": "S01-KF03", "time_sec": 13, "frame_role": "CONTRAST_ANCHOR"},
                {"keyframe_id": "S01-KF04", "time_sec": 19, "frame_role": "MONTAGE_ANCHOR"},
                {"keyframe_id": "S01-KF05", "time_sec": 25, "frame_role": "ENDING_ANCHOR"},
            ],
            "segments": [
                {"segment_id": "S01-SEG01", "start_sec": 0, "end_sec": 7, "from_keyframe_id": "S01-KF01", "to_keyframe_id": "S01-KF02", "visual_action": "镜头从太空推进到东非草原。", "connection": {"type": "CAMERA_MOTION"}},
                {"segment_id": "S01-SEG02", "start_sec": 7, "end_sec": 13, "from_keyframe_id": "S01-KF02", "to_keyframe_id": "S01-KF03", "visual_action": "草原上的早期智人与猛兽形成压迫对比。", "connection": {"type": "IMAGE_TO_VIDEO"}},
                {
                    "segment_id": "S01-SEG03",
                    "start_sec": 13,
                    "end_sec": 19,
                    "from_keyframe_id": "S01-KF03",
                    "to_keyframe_id": "S01-KF04",
                    "visual_action": "卫星画面缩小并融入黑板背景，黑板上浮现问题文字“他们靠什么翻盘？”。",
                    "connection": {"type": "MATCH_CUT"},
                },
                {"segment_id": "S01-SEG04", "start_sec": 19, "end_sec": 25, "from_keyframe_id": "S01-KF04", "to_keyframe_id": "S01-KF05", "visual_action": "黑板上浮现“翻盘技能：讲故事”。", "connection": {"type": "GRAPHIC_WIPE"}},
            ],
            "inter_scene_bridge": {"needed": True, "strategy": "GENERATE_BRIDGE_KEYFRAME", "reason": "黑板承接下一场。"},
        }


class GenericOverstuffedDirectorProvider:
    model = "generic-overstuffed-director"

    def reconstruct_scene(self, context):
        scene = context["current_scene"]
        repeated = (
            "一张古代地图展开，镜头推到黄河沿岸城市。"
            "市集里商人搬运盐袋，官员在账本上盖章。"
            "画面快闪：盐井开采、铁炉燃烧、车队入城。"
            "最后黑板上出现：国家为什么要管盐铁？"
        )
        return {
            "scene_id": scene["scene_id"],
            "title": scene["title"],
            "duration_sec": scene["duration_sec"],
            "reconstruction_decision": {
                "segment_count": 4,
                "keyframe_count": 5,
                "needs_inter_scene_bridge": False,
                "bridge_strategy": "DIRECT_CUT",
                "reason": "模拟模型把完整画面演绎复制到每段。",
            },
            "keyframes": [{"keyframe_id": f"{scene['scene_id']}-KF{index:02d}", "time_sec": 0} for index in range(1, 6)],
            "segments": [
                {
                    "segment_id": f"{scene['scene_id']}-SEG{index:02d}",
                    "start_sec": index - 1,
                    "end_sec": index,
                    "from_keyframe_id": f"{scene['scene_id']}-KF{index:02d}",
                    "to_keyframe_id": f"{scene['scene_id']}-KF{index + 1:02d}",
                    "visual_action": repeated,
                    "narration_range": {"full_text": scene["narration"][0]},
                    "connection": {"type": "IMAGE_TO_VIDEO"},
                }
                for index in range(1, 5)
            ],
            "inter_scene_bridge": {"needed": False, "strategy": "DIRECT_CUT", "reason": "可直接切换。"},
        }


class PromptOnlyKeyframeProvider:
    model = "prompt-only"

    def generate_keyframe_prompts(self, *, scene, reconstruction):
        return {
            "scene_id": scene["scene_id"],
            "keyframes": [
                {
                    "keyframe_id": keyframe["keyframe_id"],
                    "keyframe_prompt": f"画面用途：修复空视觉字段。时代地点：7万年前非洲东部。画面主体：早期智人。环境背景：东非草原。动作：{scene['visual_description']}。构图：主体清晰。镜头语言：稳定镜头。情绪氛围：清楚。屏幕文字：无。必须体现的信息：智人开局弱。这是单张关键帧，只描述一个清晰瞬间，不要把多个独立事件硬塞进同一画面。",
                    "negative_prompt": "写实照片、电影剧照、过度低幼、3D塑料感、现代建筑、现代服装、现代载具、血腥暴力、恐怖画面、文字乱码、水印、logo",
                }
                for keyframe in reconstruction["keyframes"]
            ],
        }


class NonCompliantKeyframePromptProvider:
    model = "non-compliant-prompt"

    def generate_keyframe_prompts(self, *, scene, reconstruction):
        return {
            "scene_id": scene["scene_id"],
            "keyframes": [
                {
                    "keyframe_id": keyframe["keyframe_id"],
                    "keyframe_prompt": "Half-flat cartoon illustration, educational history short video frame.",
                    "negative_prompt": "realistic photograph, movie still, watermark, logo",
                }
                for keyframe in reconstruction["keyframes"]
            ],
        }


def test_parsed_scene_normalizer_accepts_multiline_metadata_from_standard_storyboard():
    storyboard_script = parse_manual_storyboard_script(sample_storyboard_text(), title="会讲故事的动物")
    scene = ParsedSceneNormalizer().normalize(storyboard_script["scene_script"][0], index=1)

    assert scene["scene_id"] == "S01"
    assert scene["title"] == "开场：地球上最会讲故事的动物"
    assert scene["scene_type"] == ["HOST_OPENING", "SYMBOLIC_MONTAGE"]
    assert scene["function"] == ["开场钩子", "提出核心问题"]
    assert scene["duration_sec"] == 25
    assert scene["screen_text"] == ["7 万年前", "非洲东部", "智人：开局很弱", "他们靠什么翻盘？"]
    assert "地球从太空中缓慢旋转" in scene["visual_description"]
    assert "智人一开始并不强" in scene["must_keep_points"][0]


def test_scene_reconstruction_pipeline_builds_keyframe_chain_with_direct_cut(tmp_path):
    storyboard_script = parse_manual_storyboard_script(sample_storyboard_text(), title="会讲故事的动物")
    pipeline = SceneReconstructionPipeline(
        director_provider=FakeDirectorProvider(),
        keyframe_prompt_provider=FakeKeyframePromptProvider(),
    )

    payload = pipeline.generate(
        generation={"generation_id": "gen-001", "topic": "会讲故事的动物"},
        storyboard_script=storyboard_script,
        output_dir=tmp_path,
    )

    scene = payload["scenes"][0]
    frame_ids = {keyframe["keyframe_id"] for keyframe in scene["keyframes"]}
    assert payload["source_type"] == "standard_storyboard_script"
    assert payload["raw"]["format"] == "scene_reconstruction"
    assert payload["scene_count"] == 1
    assert payload["segment_count"] == 4
    assert payload["keyframe_count"] == 5
    assert scene["reconstruction_decision"]["segment_count"] == 4
    assert scene["reconstruction_decision"]["keyframe_count"] == 5
    assert scene["reconstruction_decision"]["keyframe_count"] == scene["reconstruction_decision"]["segment_count"] + 1
    assert any(segment["connection"]["type"] == "DIRECT_CUT" for segment in scene["segments"])
    assert all(segment["from_keyframe_id"] in frame_ids and segment["to_keyframe_id"] in frame_ids for segment in scene["segments"])
    assert scene["segments"][0]["start_sec"] == 0
    assert all(segment["end_sec"] > segment["start_sec"] for segment in scene["segments"])
    assert scene["keyframes"][0]["time_sec"] == 0
    assert len({tuple(item["text"] for item in segment.get("screen_text", [])) for segment in scene["segments"]}) > 1
    assert scene["inter_scene_bridge"]["strategy"] == "GENERATE_BRIDGE_KEYFRAME"
    assert scene["validation"]["passed"] is True
    assert all("这是单张关键帧" in keyframe["keyframe_prompt"] for keyframe in scene["keyframes"])
    assert all("现代建筑" in keyframe["negative_prompt"] and "现代服装" in keyframe["negative_prompt"] and "水印" in keyframe["negative_prompt"] for keyframe in scene["keyframes"])
    assert payload["shots"][0]["shot_id"].startswith("S01-KF01-")
    assert payload["shots"][0]["raw"]["keyframe_id"] == "S01-KF01"
    assert payload["shots"][0]["raw"]["frame_role"] == "OPENING_ANCHOR"
    assert payload["json_path"].endswith("scene_reconstruction.json")
    assert payload["markdown_path"].endswith("scene_reconstruction.md")
    assert "S01-KF01" in (tmp_path / "scene_reconstructions" / payload["reconstruction_id"] / "scene_reconstruction.md").read_text(encoding="utf-8")


def test_scene_reconstruction_pipeline_repairs_sparse_model_timing_and_visual_fields():
    storyboard_script = parse_manual_storyboard_script(sample_storyboard_text(), title="会讲故事的动物")
    pipeline = SceneReconstructionPipeline(
        director_provider=SparseDirectorProvider(),
        keyframe_prompt_provider=PromptOnlyKeyframeProvider(),
    )

    payload = pipeline.generate(
        generation={"generation_id": "gen-001", "topic": "会讲故事的动物"},
        storyboard_script=storyboard_script,
    )

    scene = payload["scenes"][0]
    assert scene["segments"][0]["start_sec"] == 0
    assert scene["segments"][-1]["end_sec"] == 25
    assert all(segment["end_sec"] > segment["start_sec"] for segment in scene["segments"])
    assert scene["keyframes"][0]["time_sec"] == 0
    assert scene["keyframes"][-1]["time_sec"] == 25
    assert all(keyframe["visual_content"] for keyframe in scene["keyframes"])
    assert all(segment["visual_action"] for segment in scene["segments"])
    assert scene["validation"]["passed"] is True


def test_scene_reconstruction_pipeline_repairs_overstuffed_segment_visuals_and_narration():
    storyboard_script = parse_manual_storyboard_script(sample_storyboard_text(), title="会讲故事的动物")
    pipeline = SceneReconstructionPipeline(
        director_provider=OverstuffedDirectorProvider(),
        keyframe_prompt_provider=PromptOnlyKeyframeProvider(),
    )

    payload = pipeline.generate(
        generation={"generation_id": "gen-001", "topic": "会讲故事的动物"},
        storyboard_script=storyboard_script,
    )

    scene = payload["scenes"][0]
    segments = scene["segments"]
    assert "火箭升空" not in segments[0]["visual_action"]
    assert "地球从太空中缓慢旋转" not in segments[1]["visual_action"]
    assert "地球从太空中缓慢旋转" not in segments[2]["visual_action"]
    assert "画面突然快闪" in segments[2]["visual_action"]
    assert "但你绝对想不到" in segments[2]["narration_range"]["full_text"]
    assert "他们是怎么办到的" not in segments[2]["narration_range"]["full_text"]


def test_scene_reconstruction_pipeline_repairs_segment_role_mismatch():
    storyboard_script = parse_manual_storyboard_script(sample_storyboard_text(), title="会讲故事的动物")
    pipeline = SceneReconstructionPipeline(
        director_provider=RoleMismatchedDirectorProvider(),
        keyframe_prompt_provider=PromptOnlyKeyframeProvider(),
    )

    payload = pipeline.generate(
        generation={"generation_id": "gen-001", "topic": "会讲故事的动物"},
        storyboard_script=storyboard_script,
    )

    segment = payload["scenes"][0]["segments"][2]
    assert "火箭升空" in segment["visual_action"]
    assert "潜水器下潜" in segment["visual_action"]
    assert "城市夜景" in segment["visual_action"]
    assert "黑板" not in segment["visual_action"]
    assert segment["connection"]["type"] == "DIRECT_CUT"


def test_scene_reconstruction_pipeline_uses_global_blueprint_for_unrelated_scene():
    storyboard_script = parse_manual_storyboard_script(generic_storyboard_text(), title="盐铁制度")
    pipeline = SceneReconstructionPipeline(
        director_provider=GenericOverstuffedDirectorProvider(),
        keyframe_prompt_provider=PromptOnlyKeyframeProvider(),
    )

    payload = pipeline.generate(
        generation={"generation_id": "gen-009", "topic": "盐铁制度"},
        storyboard_script=storyboard_script,
    )

    scene = payload["scenes"][0]
    segments = scene["segments"]
    assert scene["validation"]["passed"] is True
    assert len(scene["source_analysis"]["visual_units"]) >= 4
    assert len(scene["segment_blueprint"]) == len(segments)
    assert "盐井开采" not in segments[0]["visual_action"]
    assert "国家为什么要管盐铁" not in segments[1]["visual_action"]
    assert "盐井开采" in segments[2]["visual_action"]
    assert "铁炉燃烧" in segments[2]["visual_action"]
    assert "车队入城" in segments[2]["visual_action"]
    assert "最后黑板" in segments[3]["visual_action"]
    assert segments[2]["narration_range"]["full_text"] == "谁控制了盐铁，谁就抓住了财政和秩序的命门。"
    assert all(segment.get("source_visual_unit_ids") for segment in segments)


def test_scene_reconstruction_pipeline_enforces_keyframe_prompt_contract():
    storyboard_script = parse_manual_storyboard_script(sample_storyboard_text(), title="会讲故事的动物")
    pipeline = SceneReconstructionPipeline(
        director_provider=FakeDirectorProvider(),
        keyframe_prompt_provider=NonCompliantKeyframePromptProvider(),
    )

    payload = pipeline.generate(
        generation={"generation_id": "gen-001", "topic": "会讲故事的动物"},
        storyboard_script=storyboard_script,
    )

    scene = payload["scenes"][0]
    assert scene["validation"]["passed"] is True
    assert all("画面用途：" in keyframe["keyframe_prompt"] for keyframe in scene["keyframes"])
    assert all("这是单张关键帧" in keyframe["keyframe_prompt"] for keyframe in scene["keyframes"])
    assert all("现代建筑" in keyframe["negative_prompt"] for keyframe in scene["keyframes"])
    assert all("现代服装" in keyframe["negative_prompt"] for keyframe in scene["keyframes"])
    assert all("水印" in keyframe["negative_prompt"] for keyframe in scene["keyframes"])


def test_scene_reconstruction_pipeline_keeps_keyframes_static_and_motion_on_segments():
    storyboard_script = parse_manual_storyboard_script(sample_storyboard_text(), title="会讲故事的动物")
    pipeline = SceneReconstructionPipeline(
        director_provider=FakeDirectorProvider(),
        keyframe_prompt_provider=NonCompliantKeyframePromptProvider(),
    )

    payload = pipeline.generate(
        generation={"generation_id": "gen-001", "topic": "会讲故事的动物"},
        storyboard_script=storyboard_script,
    )

    scene = payload["scenes"][0]
    first_keyframe = scene["keyframes"][0]
    second_keyframe = scene["keyframes"][1]
    first_segment = scene["segments"][0]

    assert "地球" in first_keyframe["visual_content"]
    assert "非洲" in first_keyframe["visual_content"]
    assert "东非稀树草原" in second_keyframe["visual_content"]
    for keyframe in scene["keyframes"]:
        prompt = keyframe["keyframe_prompt"]
        assert "静态画面：" in prompt
        assert "动作：" not in prompt
        assert "镜头推进" not in prompt
        assert "缓慢旋转" not in prompt
        assert "再落到" not in prompt
        assert "快闪" not in prompt
        assert "缩回" not in prompt
        assert "。。" not in prompt
    assert "镜头推进" in first_segment["motion_prompt"]
    assert "再落到" in first_segment["motion_prompt"]


def test_scene_reconstruction_validator_reports_invalid_bridge_strategy_and_missing_prompts():
    scene = {
        "scene_id": "S01",
        "reconstruction_decision": {"segment_count": 1, "keyframe_count": 2, "bridge_strategy": "FADE_OUT"},
        "keyframes": [
            {"keyframe_id": "S01-KF01", "keyframe_prompt": "", "negative_prompt": "现代建筑、现代服装、水印"},
            {"keyframe_id": "S01-KF02", "keyframe_prompt": "这是单张关键帧", "negative_prompt": ""},
        ],
        "segments": [{"segment_id": "S01-SEG01", "from_keyframe_id": "S01-KF01", "to_keyframe_id": "S01-KF99"}],
        "inter_scene_bridge": {"needed": True, "strategy": "FADE_OUT", "reason": ""},
    }

    validation = SceneReconstructionValidator().validate(scene)

    assert validation["passed"] is False
    assert any("bridge_strategy" in issue for issue in validation["issues"])
    assert any("to_keyframe_id" in issue for issue in validation["issues"])
    assert any("keyframe_prompt" in issue for issue in validation["issues"])
    assert any("negative_prompt" in issue for issue in validation["issues"])
    assert any("reason" in issue for issue in validation["issues"])


def test_deepseek_scene_reconstruction_provider_defaults_to_v4_pro():
    assert DeepSeekSceneReconstructionProvider(api_key="test-key").model == "deepseek-v4-pro"
