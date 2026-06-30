from __future__ import annotations

import json
from io import BytesIO

from drama_agents.storage import MaterialDatabase
from drama_agents.storyboard_agent import DeepSeekStoryboardProvider, RuleBasedStoryboardProvider, StoryboardAgent
from drama_agents.visual_anchor_agent import build_subject_anchor_prompt
from drama_agents.webapp.app import create_app


def sample_generation() -> dict:
    return {
        "generation_id": "storyboard-demo-script",
        "created_at": "2026-06-28 10:00:00",
        "topic": "智人为什么从非洲走向世界",
        "time_range": "约 20 万年前 - 5 万年前",
        "status": "completed",
        "script": {
            "title": "智人为什么从非洲走向世界 - 短剧稿",
            "article": (
                "智人开局，装备一般，但故事系统已经上线。\n\n"
                "镜头从非洲东部的稀树草原拉开，开阔枯草地、低矮灌木和远处动物剪影形成本集的开场环境。\n\n"
                "非洲智人部落营地里，整个部落一起拉扯孩子、制作工具、分配食物。\n\n"
                "他们拥有耗能巨大的大脑，能量被身体重新分配，产道和早产儿也带来新的协作压力。\n\n"
                "早期智人群体围着火光讲故事，智人猎人群体在狮子和鬣狗的威胁下协作。\n\n"
                "布隆伯斯洞穴里，贝壳珠子和赭石板被摆在岩壁旁，成为符号能力的稳定空间。\n\n"
                "黎凡特地区和地中海东部进入冰河期，智人与尼安德特人在寒冷遭遇地带擦肩而过。\n\n"
                "多巴火山喷发后，火山灰遮天蔽日，南亚暗无天日，全球气温骤降。\n\n"
                "索马里一侧的红海海口迁徙渡口前，智人跨过红海望向阿拉伯半岛。\n\n"
                "他们沿印度洋海岸线前进，经过印度河、恒河、湄公河和多个河口。\n\n"
                "巽他大陆尽头海峡前，100公里宽的汪洋隔开澳大利亚和巴布亚新几内亚。\n\n"
                "最后，洞穴壁画与葬礼仪式空间里，岩壁、壁画、红花和葬礼仪式连接成共同想象。\n\n"
                "股票市场、CPU、汽车油箱、贝壳、船和弓箭只作为比喻或道具出现，不能进入古史主体池。"
            ),
            "fact_cards": [{"id": "F1", "fact": "智人相关事件", "confidence": "high"}],
            "causal_chain": ["智人开局 -> 故事系统上线"],
            "outline": [{"title": "开场", "core_point": "智人开局"}],
            "fact_boundaries": {"needs_manual_check": ["多巴火山影响范围需谨慎表述"]},
        },
        "selected_record_ids": [],
        "matched_events": [],
        "subjects": [],
        "map_shots": [],
    }


def seed_generation_with_assets(database: MaterialDatabase) -> tuple[dict, list[dict], list[dict]]:
    generation = sample_generation()
    database.save_script_generation(generation)
    subjects = database.save_visual_subject_extraction(
        generation["generation_id"],
        {
            "subjects": [
                {
                    "canonical_name": "智人",
                    "subject_type": "species",
                    "role_in_script": "核心主角。",
                    "importance": 5,
                    "anchor_asset_id": "/outputs/visual_subject_anchors/zhiren/anchor.jpg",
                    "visual_prompt": "早期智人主体设定，兽皮和植物纤维披挂。",
                },
                {
                    "canonical_name": "早期智人群体",
                    "subject_type": "group",
                    "role_in_script": "围火讲述和迁徙群像。",
                    "importance": 5,
                    "anchor_asset_id": "/outputs/visual_subject_anchors/group/anchor.jpg",
                    "visual_prompt": "早期智人群体，协作迁徙。",
                },
                {
                    "canonical_name": "尼安德特人",
                    "subject_type": "species",
                    "role_in_script": "与智人形成对照。",
                    "importance": 4,
                },
            ]
        },
    )
    scenes = database.save_visual_scene_extraction(
        generation["generation_id"],
        {
            "scenes": [
                {
                    "canonical_name": "东非稀树草原",
                    "scene_type": "natural_environment",
                    "role_in_script": "开场核心环境。",
                    "importance": 5,
                    "anchor_asset_id": "/outputs/visual_scene_anchors/savanna/anchor.jpg",
                    "visual_prompt": "约7万年前的东非稀树草原，黄褐色草地和稀疏灌木。",
                },
                {
                    "canonical_name": "非洲智人部落营地",
                    "scene_type": "settlement_camp",
                    "role_in_script": "智人协作生活空间。",
                    "importance": 5,
                },
                {
                    "canonical_name": "布隆伯斯洞穴",
                    "scene_type": "cave_site",
                    "role_in_script": "符号能力场景。",
                    "importance": 5,
                },
                {
                    "canonical_name": "红海海口迁徙渡口",
                    "scene_type": "migration_crossing",
                    "role_in_script": "跨海迁徙节点。",
                    "importance": 5,
                },
            ]
        },
    )
    return generation, subjects, scenes


def generate_storyboard(database: MaterialDatabase) -> dict:
    generation, subjects, scenes = seed_generation_with_assets(database)
    return StoryboardAgent(RuleBasedStoryboardProvider()).generate(
        generation=generation,
        subjects=subjects,
        scenes=scenes,
        target_duration_sec=90,
    )


class FakeKeyframeProvider:
    def __init__(self):
        self.calls = []

    def generate_image(self, *, prompt, negative_prompt, reference_images=None):
        self.calls.append(
            {
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "reference_images": list(reference_images or []),
            }
        )
        return {
            "image_bytes": b"fake-keyframe-image",
            "mime_type": "image/png",
            "model": "fake-seedream",
            "provider": "ark",
        }


def test_storyboard_agent_preserves_full_script_coverage(tmp_path):
    storyboard = generate_storyboard(MaterialDatabase(tmp_path / "storyboard.sqlite3"))
    shots = storyboard["shots"]

    assert len(shots) >= 10
    narrations = "\n".join(shot["narration"] for shot in shots)
    for phrase in ["智人开局", "大脑", "布隆伯斯洞穴", "红海海口", "洞穴壁画", "股票市场"]:
        assert phrase in narrations


def test_storyboard_agent_uses_existing_subjects_and_scenes(tmp_path):
    database = MaterialDatabase(tmp_path / "storyboard.sqlite3")
    generation, subjects, scenes = seed_generation_with_assets(database)

    storyboard = StoryboardAgent(RuleBasedStoryboardProvider()).generate(
        generation=generation,
        subjects=subjects,
        scenes=scenes,
        target_duration_sec=90,
    )

    subject_ids = {subject["subject_id"] for subject in subjects}
    scene_ids = {scene["scene_id"] for scene in scenes}
    assert any(set(shot["subject_ids"]) & subject_ids for shot in storyboard["shots"])
    assert any(shot["scene_id"] in scene_ids for shot in storyboard["shots"] if shot["scene_id"])
    savanna = next(scene for scene in scenes if scene["canonical_name"] == "东非稀树草原")
    assert any(shot["scene_id"] == savanna["scene_id"] for shot in storyboard["shots"])


def test_storyboard_agent_does_not_treat_props_as_subjects(tmp_path):
    database = MaterialDatabase(tmp_path / "storyboard.sqlite3")
    generation, subjects, scenes = seed_generation_with_assets(database)
    storyboard = StoryboardAgent(RuleBasedStoryboardProvider()).generate(
        generation=generation,
        subjects=subjects,
        scenes=scenes,
    )

    subject_names = {name for shot in storyboard["shots"] for name in shot["subject_names"]}
    for prop in ["火", "贝壳", "船", "弓箭", "CPU", "汽车油箱"]:
        assert prop not in subject_names
    assert any("贝壳" in shot["visual_elements"] or "弓箭" in shot["visual_elements"] for shot in storyboard["shots"])


def test_storyboard_agent_generates_seedream_keyframe_prompt(tmp_path):
    storyboard = generate_storyboard(MaterialDatabase(tmp_path / "storyboard.sqlite3"))

    for shot in storyboard["shots"]:
        assert shot["keyframe_prompt"]
        assert "历史科普卡通短剧风格" in shot["keyframe_prompt"]
        assert "首帧" in shot["keyframe_prompt"] or "画面" in shot["keyframe_prompt"]
        assert "16:9 横版" in shot["keyframe_prompt"]
        assert "固定 1280 * 720" in shot["keyframe_prompt"]
        assert "1920x1080" not in shot["keyframe_prompt"]
        assert "到 1080p" not in shot["keyframe_prompt"]
        assert "或 1920x1080" not in shot["keyframe_prompt"]
        assert "不要 1080p、2K、4K" in shot["keyframe_prompt"]
        assert "镜头缓慢推进" not in shot["keyframe_prompt"]


def test_storyboard_keyframe_prompt_does_not_reuse_subject_anchor_task_prompt(tmp_path):
    database = MaterialDatabase(tmp_path / "storyboard.sqlite3")
    generation, subjects, scenes = seed_generation_with_assets(database)
    subjects[0]["visual_prompt"] = build_subject_anchor_prompt(
        {
            "canonical_name": "智人",
            "subject_type": "species",
            "short_description": "现代人类的祖先。",
            "visual_identity": {
                "era": "旧石器时代中晚期",
                "region": "非洲东部",
                "appearance": "深色皮肤，身材较纤细。",
                "clothing": "简单兽皮披挂。",
                "props": ["木矛"],
            },
            "consistency_rules": {"must_keep": ["深色皮肤", "纤细体型"]},
        }
    )

    storyboard = StoryboardAgent(RuleBasedStoryboardProvider()).generate(
        generation=generation,
        subjects=subjects,
        scenes=scenes,
        target_duration_sec=90,
    )
    prompt = next(shot["keyframe_prompt"] for shot in storyboard["shots"] if "智人" in shot["subject_names"])

    assert "参考主体一致性：智人" in prompt
    assert "主体锚点图" not in prompt
    assert "纯主体参考图" not in prompt
    assert "只生成一个主体" not in prompt


def test_storyboard_keyframe_prompt_explains_frame_progression(tmp_path):
    storyboard = generate_storyboard(MaterialDatabase(tmp_path / "storyboard.sqlite3"))

    for index, shot in enumerate(storyboard["shots"]):
        assert "本帧剧情任务：" in shot["keyframe_prompt"]
        if index > 0:
            assert "相对上一帧变化：" in shot["keyframe_prompt"]


def test_storyboard_replaces_generic_visual_goals_with_specific_story_beats(tmp_path):
    class GenericGoalProvider(RuleBasedStoryboardProvider):
        def generate_storyboard(self, payload):
            subject_id = payload["provided_subjects"][0]["subject_id"]
            return {
                "storyboard": {
                    "title": "泛化目标测试",
                    "shots": [
                        {
                            "shot_index": 1,
                            "narration": "这群动物的学名叫智人。",
                            "subtitle_text": "这群动物的学名叫智人。",
                            "shot_type": "narrative_shot",
                            "visual_goal": "推进旁白叙事",
                            "subject_ids": [subject_id],
                        },
                        {
                            "shot_index": 2,
                            "narration": "别被这个名字骗了，他们不是某种和我们无关的远古怪物。",
                            "subtitle_text": "别被这个名字骗了，他们不是某种和我们无关的远古怪物。",
                            "shot_type": "narrative_shot",
                            "visual_goal": "推进旁白叙事",
                            "subject_ids": [subject_id],
                        },
                    ],
                }
            }

    database = MaterialDatabase(tmp_path / "storyboard.sqlite3")
    generation, subjects, scenes = seed_generation_with_assets(database)

    storyboard = StoryboardAgent(GenericGoalProvider()).generate(
        generation=generation,
        subjects=subjects,
        scenes=scenes,
        target_duration_sec=90,
    )
    first_prompt = storyboard["shots"][0]["keyframe_prompt"]
    second_prompt = storyboard["shots"][1]["keyframe_prompt"]

    assert "旁白对应画面：推进旁白叙事。" not in first_prompt
    assert "完成身份揭示" in first_prompt
    assert "纠正误解" in second_prompt
    assert first_prompt != second_prompt


def test_storyboard_agent_generates_video_prompt(tmp_path):
    storyboard = generate_storyboard(MaterialDatabase(tmp_path / "storyboard.sqlite3"))

    for shot in storyboard["shots"]:
        assert shot["video_prompt"]
        assert "基于首帧" in shot["video_prompt"]
        assert "保持" in shot["video_prompt"]
        assert str(int(shot["duration_sec"])) in shot["video_prompt"] or f"{shot['duration_sec']}" in shot["video_prompt"]


def test_storyboard_agent_outputs_reasonable_shot_types(tmp_path):
    storyboard = generate_storyboard(MaterialDatabase(tmp_path / "storyboard.sqlite3"))

    savanna = next(shot for shot in storyboard["shots"] if "稀树草原" in shot["narration"])
    brain = next(shot for shot in storyboard["shots"] if "大脑" in shot["narration"])
    migration = next(shot for shot in storyboard["shots"] if "红海" in shot["narration"])

    assert savanna["shot_type"] in {"hook_shot", "narrative_shot"}
    assert brain["shot_type"] == "explainer_shot"
    assert migration["shot_type"] in {"map_shot", "narrative_shot"}


def test_storyboard_agent_derives_duration_from_content_when_no_target(tmp_path):
    database = MaterialDatabase(tmp_path / "storyboard.sqlite3")
    generation, subjects, scenes = seed_generation_with_assets(database)

    storyboard = StoryboardAgent(RuleBasedStoryboardProvider()).generate(
        generation=generation,
        subjects=subjects,
        scenes=scenes,
    )

    actual_duration = sum(shot["duration_sec"] for shot in storyboard["shots"])
    assert storyboard["actual_duration_sec"] == actual_duration
    assert storyboard["target_duration_sec"] == round(actual_duration)
    assert not any("目标时长" in note for note in storyboard["review_notes"])


def test_storyboard_has_coverage_json(tmp_path):
    storyboard = generate_storyboard(MaterialDatabase(tmp_path / "storyboard.sqlite3"))

    assert storyboard["coverage"]["coverage_ratio"] >= 0.9
    assert storyboard["coverage"]["paragraph_count"] >= 1
    assert storyboard["coverage"]["covered_paragraph_count"] == storyboard["coverage"]["paragraph_count"]
    assert "coverage_ratio" in storyboard["coverage"]


def test_storyboard_shots_have_source_spans(tmp_path):
    storyboard = generate_storyboard(MaterialDatabase(tmp_path / "storyboard.sqlite3"))

    for shot in storyboard["shots"]:
        if shot["is_supplemental"]:
            continue
        assert shot["source_paragraph_index"] >= 1
        assert shot["source_text_start"] >= 0
        assert shot["source_text_end"] > shot["source_text_start"]
        assert shot["source_excerpt"]
        assert shot["source_excerpt"] in sample_generation()["script"]["article"]


def test_supplemental_shots_are_marked(tmp_path):
    storyboard = generate_storyboard(MaterialDatabase(tmp_path / "storyboard.sqlite3"))
    supplemental = [shot for shot in storyboard["shots"] if shot["is_supplemental"]]

    assert supplemental
    assert any(shot["shot_type"] in {"map_shot", "concept_shot", "transition_shot"} for shot in supplemental)
    assert all(shot["supplemental_reason"] for shot in supplemental)
    assert storyboard["coverage"]["supplemental_shot_count"] == len(supplemental)


def test_storyboard_has_sequences_and_beats(tmp_path):
    storyboard = generate_storyboard(MaterialDatabase(tmp_path / "storyboard.sqlite3"))

    assert all(shot["sequence_id"] for shot in storyboard["shots"])
    assert all(shot["sequence_title"] for shot in storyboard["shots"])
    assert all(shot["beat_id"] for shot in storyboard["shots"])
    assert all(shot["beat_title"] for shot in storyboard["shots"])
    assert storyboard["shots"][0]["next_shot_id"] == storyboard["shots"][1]["shot_id"]
    assert storyboard["shots"][1]["prev_shot_id"] == storyboard["shots"][0]["shot_id"]


def test_storyboard_has_main_scene_blocks_with_shot_membership(tmp_path):
    storyboard = generate_storyboard(MaterialDatabase(tmp_path / "storyboard.sqlite3"))
    scene_blocks = storyboard["scene_blocks"]
    scene_block_ids = {block["scene_block_id"] for block in scene_blocks}

    assert 3 <= len(scene_blocks) <= 8
    assert len(scene_blocks) < len(storyboard["shots"])
    assert all(block["title"] for block in scene_blocks)
    assert all(block["source_excerpt"] for block in scene_blocks)
    assert all(block["key_beats"] for block in scene_blocks)
    assert all(shot["scene_block_id"] in scene_block_ids for shot in storyboard["shots"])
    assert max(block["shot_count"] for block in scene_blocks) > 1
    assert storyboard["shots"][0]["sequence_id"] == storyboard["shots"][0]["scene_block_id"]
    assert storyboard["shots"][0]["sequence_title"] == storyboard["shots"][0]["scene_block_title"]


def test_storyboard_shots_have_continuity(tmp_path):
    storyboard = generate_storyboard(MaterialDatabase(tmp_path / "storyboard.sqlite3"))

    for shot in storyboard["shots"]:
        continuity = shot["continuity"]
        assert continuity["previous_shot_relation"]
        assert continuity["screen_direction"]
        assert continuity["continuity_axis"]
        assert "spatial_continuity_note" in continuity
        assert "visual_bridge" in continuity


def test_storyboard_keyframe_prompts_include_neighbor_continuity(tmp_path):
    storyboard = generate_storyboard(MaterialDatabase(tmp_path / "storyboard.sqlite3"))
    shots = storyboard["shots"]
    shots_by_id = {shot["shot_id"]: shot for shot in shots}
    shot = next(
        item
        for item in shots
        if item["prev_shot_id"] and item["scene_block_id"] == shots_by_id[item["prev_shot_id"]]["scene_block_id"]
    )
    previous = shots_by_id[shot["prev_shot_id"]]

    assert "上一镜头承接" in shot["keyframe_prompt"]
    assert previous["scene_block_title"] in shot["keyframe_prompt"]
    assert "主场景内连续" in shot["keyframe_prompt"]
    assert "画面连续性" in shot["keyframe_prompt"]


def test_storyboard_scene_block_boundaries_choose_transition_methods(tmp_path):
    storyboard = generate_storyboard(MaterialDatabase(tmp_path / "storyboard.sqlite3"))
    shots = storyboard["shots"]
    shots_by_id = {shot["shot_id"]: shot for shot in shots}
    boundary_shots = [
        shot
        for shot in shots
        if shot["prev_shot_id"] and shot["scene_block_id"] != shots_by_id[shot["prev_shot_id"]]["scene_block_id"]
    ]

    assert boundary_shots
    for shot in boundary_shots:
        previous = shots_by_id[shot["prev_shot_id"]]
        continuity = shot["continuity"]
        assert shot["transition"] in {"map_transition", "narration_bridge", "visual_bridge", "concept_bridge"}
        assert continuity["transition_method"] == shot["transition"]
        assert continuity["previous_scene_block_title"] == previous["scene_block_title"]
        assert continuity["current_scene_block_title"] == shot["scene_block_title"]
        assert continuity["transition_guidance"]


def test_storyboard_shots_have_production_plan(tmp_path):
    storyboard = generate_storyboard(MaterialDatabase(tmp_path / "storyboard.sqlite3"))

    for shot in storyboard["shots"]:
        production_plan = shot["production_plan"]
        assert production_plan["render_method"]
        assert production_plan["cost_tier"] in {"low", "medium", "high"}
        assert isinstance(production_plan["needs_keyframe"], bool)
        assert isinstance(production_plan["needs_video"], bool)
        assert production_plan["recommended_tool"]
        assert production_plan["reason"]
    assert next(shot for shot in storyboard["shots"] if shot["shot_type"] == "map_shot")["production_plan"]["render_method"] == "map_animation"


def test_storyboard_prompt_parts_are_saved(tmp_path):
    database = MaterialDatabase(tmp_path / "storyboard.sqlite3")
    generation, _subjects, _scenes = seed_generation_with_assets(database)
    storyboard = generate_storyboard(database)
    saved = database.save_storyboard(generation["generation_id"], storyboard)
    shots = database.list_storyboard_shots(saved["storyboard_id"])

    for shot in shots:
        prompt_parts = shot["prompt_parts"]
        assert prompt_parts["style_prompt"]
        assert prompt_parts["scene_prompt"]
        assert prompt_parts["composition_prompt"]
        assert prompt_parts["frame_size_prompt"]
        assert prompt_parts["negative_prompt"]
        assert prompt_parts["style_prompt"] in shot["keyframe_prompt"]
        assert prompt_parts["frame_size_prompt"] in shot["keyframe_prompt"]


def test_deepseek_bad_json_repairs_before_fallback(monkeypatch):
    monkeypatch.setenv("STORYBOARD_CHUNK_FIRST_MIN_CHARS", "99999")
    monkeypatch.setenv("STORYBOARD_CHUNK_FIRST_MIN_PARAGRAPHS", "99999")

    class RepairingStoryboardProvider(DeepSeekStoryboardProvider):
        def __init__(self):
            self.calls = []

        def _post_chat(self, messages, *, max_tokens):
            self.calls.append(messages[-1]["content"])
            if len(self.calls) == 1:
                return '{"storyboard": {"shots": ['
            return json.dumps(
                {
                    "storyboard": {
                        "title": "修复后的分镜",
                        "shots": [
                            {
                                "shot_index": 1,
                                "narration": "智人开局，装备一般，但故事系统已经上线。",
                                "subtitle_text": "智人开局，装备一般，但故事系统已经上线。",
                                "shot_type": "hook_shot",
                                "visual_goal": "开场",
                                "duration_sec": 4,
                            }
                        ],
                    }
                },
                ensure_ascii=False,
            )

    provider = RepairingStoryboardProvider()
    payload = provider.generate_storyboard({"full_script": sample_generation()["script"]["article"], "provided_subjects": [], "provided_scenes": []})

    assert len(provider.calls) == 2
    assert "修复" in provider.calls[1]
    assert payload["storyboard"]["title"] == "修复后的分镜"


def test_deepseek_long_scripts_use_chunked_generation_first():
    class ChunkFirstProvider(DeepSeekStoryboardProvider):
        def __init__(self):
            self.used_chunked = False

        def _post_chat(self, messages, *, max_tokens):
            raise AssertionError("long scripts should not use one monolithic storyboard call")

        def generate_storyboard_chunks(self, payload):
            self.used_chunked = True
            return {"storyboard": {"title": "分段分镜", "shots": [{"narration": "第一段。", "duration_sec": 4}]}}

    provider = ChunkFirstProvider()
    payload = {"full_script": "很长的一段剧本。" * 260, "provided_subjects": [], "provided_scenes": []}

    result = provider.generate_storyboard(payload)

    assert provider.used_chunked
    assert result["storyboard"]["title"] == "分段分镜"


def test_deepseek_chunk_generation_maps_source_paragraphs():
    class ChunkSourceProvider(DeepSeekStoryboardProvider):
        def __init__(self):
            self.calls = 0

        def _post_chat(self, messages, *, max_tokens):
            self.calls += 1
            return json.dumps(
                {
                    "storyboard": {
                        "shots": [
                            {
                                "narration": f"第 {self.calls} 段镜头。",
                                "source_paragraph_index": 1,
                                "duration_sec": 4,
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            )

    provider = ChunkSourceProvider()
    result = provider.generate_storyboard_chunks({"full_script": "第一段。\n\n第二段。"})

    assert [shot["source_paragraph_index"] for shot in result["storyboard"]["shots"]] == [1, 2]


def test_rule_based_fallback_sets_needs_review(tmp_path):
    class BrokenStoryboardProvider:
        def generate_storyboard(self, payload):
            raise json.JSONDecodeError("bad", "{", 0)

        def repair_storyboard_json(self, content):
            raise json.JSONDecodeError("bad repair", "{", 0)

        def generate_storyboard_chunks(self, payload):
            raise json.JSONDecodeError("bad chunks", "{", 0)

    database = MaterialDatabase(tmp_path / "storyboard.sqlite3")
    generation, subjects, scenes = seed_generation_with_assets(database)
    storyboard = StoryboardAgent(BrokenStoryboardProvider()).generate(
        generation=generation,
        subjects=subjects,
        scenes=scenes,
    )

    assert storyboard["status"] == "needs_review"
    assert any("本地规则分镜兜底" in note for note in storyboard["review_notes"])
    assert storyboard["raw"]["fallback"]["stage"] == "rule_based"


def test_compressed_llm_storyboard_uses_quality_fallback(tmp_path):
    class CompressedStoryboardProvider:
        def generate_storyboard(self, payload):
            paragraphs = [item.strip() for item in payload["full_script"].split("\n\n") if item.strip()]
            shots = []
            for index, paragraph in enumerate(paragraphs[:11]):
                shots.append(
                    {
                        "shot_index": index + 1,
                        "source_paragraph_index": index,
                        "source_text_start": 0,
                        "source_text_end": min(len(paragraph), 120),
                        "source_excerpt": paragraph[:120],
                        "is_supplemental": False,
                        "shot_type": "narrative_shot",
                        "visual_goal": "压缩后的大纲镜头",
                    }
                )
            return {
                "storyboard": {
                    "title": "压缩分镜",
                    "coverage": {
                        "total_shots": 42,
                        "paragraphs_covered": "0-10",
                        "estimated_total_duration_seconds": 150,
                    },
                    "review_notes": [],
                    "shots": shots,
                }
            }

    database = MaterialDatabase(tmp_path / "storyboard.sqlite3")
    generation, subjects, scenes = seed_generation_with_assets(database)

    storyboard = StoryboardAgent(CompressedStoryboardProvider()).generate(
        generation=generation,
        subjects=subjects,
        scenes=scenes,
    )

    assert len(storyboard["shots"]) > 11
    assert storyboard["actual_duration_sec"] > 44
    assert storyboard["coverage"]["coverage_ratio"] >= 0.9
    assert storyboard["status"] == "needs_review"
    assert any("分镜过度压缩" in note for note in storyboard["review_notes"])


def test_save_storyboard_creates_records_and_shots(tmp_path):
    database = MaterialDatabase(tmp_path / "storyboard.sqlite3")
    generation, _subjects, _scenes = seed_generation_with_assets(database)
    storyboard = generate_storyboard(database)

    saved = database.save_storyboard(generation["generation_id"], storyboard)

    assert saved["storyboard_id"]
    assert saved["shot_count"] == len(storyboard["shots"])
    assert saved["coverage"]["coverage_ratio"] >= 0.9
    assert saved["script_feedback"] == storyboard["script_feedback"]
    assert saved["scene_blocks"] == storyboard["scene_blocks"]
    assert saved["actual_duration_sec"] == sum(shot["duration_sec"] for shot in storyboard["shots"])
    assert database.find_storyboard(saved["storyboard_id"])["title"] == saved["title"]
    saved_shots = database.list_storyboard_shots(saved["storyboard_id"])
    assert len(saved_shots) == saved["shot_count"]
    assert all(shot["scene_block_id"] for shot in saved_shots)
    assert database.list_storyboards(generation["generation_id"])[0]["storyboard_id"] == saved["storyboard_id"]


def test_save_storyboard_creates_asset_candidates_and_preparation_state(tmp_path):
    database = MaterialDatabase(tmp_path / "storyboard.sqlite3")
    generation, _subjects, _scenes = seed_generation_with_assets(database)
    storyboard = generate_storyboard(database)

    saved = database.save_storyboard(generation["generation_id"], storyboard)
    shots = database.list_storyboard_shots(saved["storyboard_id"])
    shot = next(item for item in shots if item["visual_elements"])
    all_candidates = database.list_storyboard_shot_candidates(saved["storyboard_id"])
    candidates = database.list_storyboard_shot_candidates(saved["storyboard_id"], shot["shot_id"])

    assert candidates
    assert any(item["candidate_type"] == "scene" and item["candidate_status"] == "linked" for item in all_candidates)
    assert any(item["candidate_type"] == "subject" and item["candidate_status"] == "linked" for item in all_candidates)
    assert any(item["candidate_type"] == "visual_element" and item["candidate_status"] == "pending" for item in candidates)

    state = database.build_storyboard_shot_preparation_state(saved["storyboard_id"], shot["shot_id"])

    assert state["shot"]["shot_id"] == shot["shot_id"]
    assert state["candidate_count"] == len(candidates)
    assert state["pending_candidate_count"] == len([item for item in candidates if item["candidate_status"] == "pending"])
    assert state["prompt_ready"] is True
    assert state["ready_for_keyframe"] is False


def test_storyboard_candidate_sync_preserves_confirmed_status(tmp_path):
    database = MaterialDatabase(tmp_path / "storyboard.sqlite3")
    generation, _subjects, _scenes = seed_generation_with_assets(database)
    storyboard = generate_storyboard(database)
    saved = database.save_storyboard(generation["generation_id"], storyboard)
    shot = next(item for item in database.list_storyboard_shots(saved["storyboard_id"]) if item["visual_elements"])
    pending = next(
        item
        for item in database.list_storyboard_shot_candidates(saved["storyboard_id"], shot["shot_id"])
        if item["candidate_status"] == "pending"
    )

    database.mark_storyboard_shot_candidate_linked(
        saved["storyboard_id"],
        shot["shot_id"],
        pending["candidate_id"],
        linked_entity_id="manual-prop-anchor-1",
    )
    storyboard["storyboard_id"] = saved["storyboard_id"]
    database.save_storyboard(generation["generation_id"], storyboard)

    synced = database.list_storyboard_shot_candidates(saved["storyboard_id"], shot["shot_id"])
    preserved = next(item for item in synced if item["candidate_id"] == pending["candidate_id"])
    assert preserved["candidate_status"] == "linked"
    assert preserved["linked_entity_id"] == "manual-prop-anchor-1"


def test_storyboard_preparation_state_ready_after_candidates_confirmed(tmp_path):
    database = MaterialDatabase(tmp_path / "storyboard.sqlite3")
    generation, _subjects, _scenes = seed_generation_with_assets(database)
    storyboard = generate_storyboard(database)
    saved = database.save_storyboard(generation["generation_id"], storyboard)
    shot = next(item for item in database.list_storyboard_shots(saved["storyboard_id"]) if item["visual_elements"])

    for candidate in database.list_storyboard_shot_candidates(saved["storyboard_id"], shot["shot_id"]):
        if candidate["candidate_status"] == "pending":
            database.mark_storyboard_shot_candidate_ignored(saved["storyboard_id"], shot["shot_id"], candidate["candidate_id"])

    state = database.build_storyboard_shot_preparation_state(saved["storyboard_id"], shot["shot_id"])

    assert state["pending_candidate_count"] == 0
    assert state["basic_info_ready"] is True
    assert state["prompt_ready"] is True
    assert state["ready_for_keyframe"] is True


def test_storyboard_api_generate_from_script(tmp_path):
    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", storyboard_provider=RuleBasedStoryboardProvider())
    client = app.test_client()
    database = MaterialDatabase(tmp_path / "outputs" / "material_workstation.sqlite3")
    generation, _subjects, _scenes = seed_generation_with_assets(database)

    response = client.post(f"/api/script/generations/{generation['generation_id']}/storyboard/generate")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["storyboard"]["storyboard_id"]
    assert payload["storyboard"]["shot_count"] == len(payload["shots"])
    assert payload["storyboard"]["target_duration_sec"] == round(payload["storyboard"]["actual_duration_sec"])
    assert payload["storyboard"]["scene_blocks"]
    assert payload["shots"][0]["scene_block_id"]
    assert payload["shots"][0]["keyframe_prompt"]
    assert client.get(f"/api/storyboards/{payload['storyboard']['storyboard_id']}").status_code == 200


def test_storyboard_api_falls_back_when_provider_returns_malformed_json(tmp_path):
    class MalformedJsonStoryboardProvider:
        def generate_storyboard(self, payload):
            raise json.JSONDecodeError("Unterminated string", '{"storyboard": {"shots": ["bad', 28)

    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        storyboard_provider=MalformedJsonStoryboardProvider(),
    )
    client = app.test_client()
    database = MaterialDatabase(tmp_path / "outputs" / "material_workstation.sqlite3")
    generation, _subjects, _scenes = seed_generation_with_assets(database)

    response = client.post(f"/api/script/generations/{generation['generation_id']}/storyboard/generate")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["storyboard"]["storyboard_id"]
    assert payload["storyboard"]["shot_count"] == len(payload["shots"])
    assert payload["shots"][0]["keyframe_prompt"]
    assert any("本地规则分镜兜底" in note for note in payload["storyboard"]["review_notes"])


def test_storyboard_upload_script(tmp_path):
    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", storyboard_provider=RuleBasedStoryboardProvider())
    client = app.test_client()

    response = client.post(
        "/api/storyboards/extract-from-upload",
        data={"file": (BytesIO("智人开局。\n\n索马里红海海口迁徙。".encode("utf-8")), "uploaded.md")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["generation"]["generation_id"].startswith("uploaded-script-")
    assert payload["storyboard"]["source_type"] == "upload"
    assert payload["storyboard"]["source_filename"] == "uploaded.md"
    assert payload["shots"]


def test_storyboard_delete(tmp_path):
    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", storyboard_provider=RuleBasedStoryboardProvider())
    client = app.test_client()
    database = MaterialDatabase(tmp_path / "outputs" / "material_workstation.sqlite3")
    generation, _subjects, _scenes = seed_generation_with_assets(database)
    created = client.post(f"/api/script/generations/{generation['generation_id']}/storyboard/generate").get_json()
    storyboard_id = created["storyboard"]["storyboard_id"]

    response = client.delete(f"/api/storyboards/{storyboard_id}")

    assert response.status_code == 200
    assert response.get_json()["deleted"] == storyboard_id
    assert client.get("/api/storyboards").get_json()["storyboards"] == []


def test_storyboard_shot_patch_updates_prompt(tmp_path):
    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", storyboard_provider=RuleBasedStoryboardProvider())
    client = app.test_client()
    database = MaterialDatabase(tmp_path / "outputs" / "material_workstation.sqlite3")
    generation, _subjects, scenes = seed_generation_with_assets(database)
    created = client.post(f"/api/script/generations/{generation['generation_id']}/storyboard/generate").get_json()
    storyboard_id = created["storyboard"]["storyboard_id"]
    shot = created["shots"][0]
    scene_id = scenes[0]["scene_id"]

    response = client.patch(
        f"/api/storyboards/{storyboard_id}/shots/{shot['shot_id']}",
        json={"keyframe_prompt": "用户修改后的 Seedream 首帧 prompt", "scene_id": scene_id},
    )

    assert response.status_code == 200
    updated = response.get_json()["shot"]
    assert updated["keyframe_prompt"] == "用户修改后的 Seedream 首帧 prompt"
    assert updated["scene_id"] == scene_id
    assert "production_plan" in updated
    assert "prompt_parts" in updated


def test_storyboard_candidate_api_links_and_ignores_candidates(tmp_path):
    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", storyboard_provider=RuleBasedStoryboardProvider())
    client = app.test_client()
    database = MaterialDatabase(tmp_path / "outputs" / "material_workstation.sqlite3")
    generation, _subjects, _scenes = seed_generation_with_assets(database)
    created = client.post(f"/api/script/generations/{generation['generation_id']}/storyboard/generate").get_json()
    storyboard_id = created["storyboard"]["storyboard_id"]
    shot = next(item for item in created["shots"] if item["visual_elements"])

    candidates_response = client.get(f"/api/storyboards/{storyboard_id}/shots/{shot['shot_id']}/candidates")
    assert candidates_response.status_code == 200
    candidates = candidates_response.get_json()["candidates"]
    pending_candidates = [item for item in candidates if item["candidate_status"] == "pending"]
    assert pending_candidates

    link_response = client.patch(
        f"/api/storyboards/{storyboard_id}/shots/{shot['shot_id']}/candidates/{pending_candidates[0]['candidate_id']}/link",
        json={"linked_entity_id": "manual-prop-anchor-api"},
    )
    assert link_response.status_code == 200
    assert link_response.get_json()["candidate"]["candidate_status"] == "linked"
    assert link_response.get_json()["candidate"]["linked_entity_id"] == "manual-prop-anchor-api"

    if len(pending_candidates) > 1:
        ignore_response = client.patch(
            f"/api/storyboards/{storyboard_id}/shots/{shot['shot_id']}/candidates/{pending_candidates[1]['candidate_id']}/ignore"
        )
        assert ignore_response.status_code == 200
        assert ignore_response.get_json()["candidate"]["candidate_status"] == "ignored"

    state_response = client.get(f"/api/storyboards/{storyboard_id}/shots/{shot['shot_id']}/preparation-state")
    assert state_response.status_code == 200
    assert state_response.get_json()["state"]["candidate_count"] == len(candidates)


def test_existing_subject_and_scene_pool_not_modified_by_storyboard(tmp_path):
    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", storyboard_provider=RuleBasedStoryboardProvider())
    client = app.test_client()
    database = MaterialDatabase(tmp_path / "outputs" / "material_workstation.sqlite3")
    generation, _subjects, _scenes = seed_generation_with_assets(database)
    before_subjects = database.list_visual_subjects()
    before_scenes = database.list_visual_scenes()

    response = client.post(f"/api/script/generations/{generation['generation_id']}/storyboard/generate")

    assert response.status_code == 200
    assert database.list_visual_subjects() == before_subjects
    assert database.list_visual_scenes() == before_scenes


def test_storyboard_panel_renders_operation_area_and_records(tmp_path):
    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", storyboard_provider=RuleBasedStoryboardProvider())
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="storyboardScriptInput"' in html
    assert "storyboardTargetDuration" not in html
    assert "60 秒" not in html
    assert "90 秒" not in html
    assert "120 秒" not in html
    assert 'id="storyboardUploadButton" type="button">上传剧本' in html
    assert 'id="storyboardSelectButton" type="button">选择剧本' in html
    assert 'id="storyboardGenerateButton" type="button">解析' in html
    assert "分镜记录" in html
    assert "scene-placeholder" not in html


def test_storyboard_detail_page_renders_shot_editor(tmp_path):
    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", storyboard_provider=RuleBasedStoryboardProvider())
    client = app.test_client()
    database = MaterialDatabase(tmp_path / "outputs" / "material_workstation.sqlite3")
    generation, _subjects, _scenes = seed_generation_with_assets(database)
    created = client.post(f"/api/script/generations/{generation['generation_id']}/storyboard/generate").get_json()
    storyboard_id = created["storyboard"]["storyboard_id"]

    response = client.get(f"/storyboards/{storyboard_id}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "分镜详情" in html
    assert "storyboardShotList" in html
    assert "storyboardShotEditor" in html
    assert "storyboardCoverageSummary" in html
    assert "storyboardShotCandidates" in html
    assert "storyboardPreparationStatus" in html
    assert "候选确认" in html
    assert "主场景结构" in html
    assert "storyboard-scene-block-strip" in html
    assert "storyboard-main-scene-head" in html
    assert "source_excerpt" in html
    assert "continuity" in html
    assert "production_plan" in html
    assert "prompt_parts" in html
    assert "keyframe_prompt" in html
    assert "video_prompt" in html
    assert "生成本镜头关键帧" in html
    assert f"/api/storyboards/{storyboard_id}/shots/" in html


def test_storyboard_detail_has_top_batch_keyframe_progress(tmp_path):
    app = create_app(workspace=tmp_path, outputs=tmp_path / "outputs", storyboard_provider=RuleBasedStoryboardProvider())
    client = app.test_client()
    database = MaterialDatabase(tmp_path / "outputs" / "material_workstation.sqlite3")
    generation, _subjects, _scenes = seed_generation_with_assets(database)
    created = client.post(f"/api/script/generations/{generation['generation_id']}/storyboard/generate").get_json()
    storyboard_id = created["storyboard"]["storyboard_id"]

    html = client.get(f"/storyboards/{storyboard_id}").get_data(as_text=True)
    header = html[html.index('class="storyboard-detail-actions"') : html.index("</header>")]

    assert 'id="batchKeyframeButton"' in header
    assert "批量生成关键帧" in header
    assert "storyboardKeyframeBatchPanel" in html
    assert "storyboardKeyframeProgressFill" in html
    assert "storyboardKeyframeProgressList" in html


def test_storyboard_shot_keyframe_api_generates_and_saves_asset(tmp_path):
    provider = FakeKeyframeProvider()
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        anchor_provider=provider,
        storyboard_provider=RuleBasedStoryboardProvider(),
    )
    client = app.test_client()
    database = MaterialDatabase(tmp_path / "outputs" / "material_workstation.sqlite3")
    generation, _subjects, _scenes = seed_generation_with_assets(database)
    created = client.post(f"/api/script/generations/{generation['generation_id']}/storyboard/generate").get_json()
    storyboard_id = created["storyboard"]["storyboard_id"]
    shot = created["shots"][0]

    response = client.post(f"/api/storyboards/{storyboard_id}/shots/{shot['shot_id']}/keyframe")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "completed"
    assert payload["provider"] == "ark"
    assert payload["model"] == "fake-seedream"
    assert payload["asset_url"].startswith(f"/outputs/storyboard_keyframes/{storyboard_id}/{shot['shot_id']}/")
    assert (tmp_path / "outputs" / payload["asset_url"].removeprefix("/outputs/")).read_bytes() == b"fake-keyframe-image"
    assert provider.calls[0]["prompt"] == shot["keyframe_prompt"]
    assert provider.calls[0]["negative_prompt"] == shot["negative_prompt"]
    updated = database.list_storyboard_shots(storyboard_id)[0]
    assert updated["keyframe_asset_id"] == payload["asset_url"]
    assert updated["asset_status"] == "keyframe_ready"


def test_storyboard_shot_keyframe_api_uses_previous_keyframe_reference(tmp_path):
    provider = FakeKeyframeProvider()
    app = create_app(
        workspace=tmp_path,
        outputs=tmp_path / "outputs",
        anchor_provider=provider,
        storyboard_provider=RuleBasedStoryboardProvider(),
    )
    client = app.test_client()
    database = MaterialDatabase(tmp_path / "outputs" / "material_workstation.sqlite3")
    generation, _subjects, _scenes = seed_generation_with_assets(database)
    created = client.post(f"/api/script/generations/{generation['generation_id']}/storyboard/generate").get_json()
    storyboard_id = created["storyboard"]["storyboard_id"]
    first, second = created["shots"][0], created["shots"][1]

    first_response = client.post(f"/api/storyboards/{storyboard_id}/shots/{first['shot_id']}/keyframe")
    second_response = client.post(f"/api/storyboards/{storyboard_id}/shots/{second['shot_id']}/keyframe")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert provider.calls[0]["reference_images"] == []
    assert provider.calls[1]["reference_images"]
    assert provider.calls[1]["reference_images"][0].startswith("data:image/png;base64,")
    assert "上一镜头关键帧参考图" in provider.calls[1]["prompt"]
