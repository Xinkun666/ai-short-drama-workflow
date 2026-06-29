from __future__ import annotations

import json
from io import BytesIO

from drama_agents.storage import MaterialDatabase
from drama_agents.storyboard_agent import RuleBasedStoryboardProvider, StoryboardAgent
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
        assert "镜头缓慢推进" not in shot["keyframe_prompt"]


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


def test_save_storyboard_creates_records_and_shots(tmp_path):
    database = MaterialDatabase(tmp_path / "storyboard.sqlite3")
    generation, _subjects, _scenes = seed_generation_with_assets(database)
    storyboard = generate_storyboard(database)

    saved = database.save_storyboard(generation["generation_id"], storyboard)

    assert saved["storyboard_id"]
    assert saved["shot_count"] == len(storyboard["shots"])
    assert saved["actual_duration_sec"] == sum(shot["duration_sec"] for shot in storyboard["shots"])
    assert database.find_storyboard(saved["storyboard_id"])["title"] == saved["title"]
    assert len(database.list_storyboard_shots(saved["storyboard_id"])) == saved["shot_count"]
    assert database.list_storyboards(generation["generation_id"])[0]["storyboard_id"] == saved["storyboard_id"]


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
    assert "keyframe_prompt" in html
    assert "video_prompt" in html
    assert "生成本镜头关键帧" in html
    assert f"/api/storyboards/{storyboard_id}/shots/" in html
