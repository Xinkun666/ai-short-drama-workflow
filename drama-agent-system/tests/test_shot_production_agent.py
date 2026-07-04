from __future__ import annotations

from drama_agents.script_agent import parse_manual_storyboard_script
from drama_agents.shot_production_agent import (
    DeepSeekKeyframePromptProvider,
    DeepSeekShotPlannerProvider,
    RuleBasedKeyframePromptProvider,
    RuleBasedShotPlannerProvider,
    ShotProductionAgent,
)


def sample_standard_storyboard_text() -> str:
    return """
S01｜开场：地球上最会讲故事的动物

场景类型： HOST_OPENING + SYMBOLIC_MONTAGE
功能： 开场钩子 / 提出核心问题
时长： 25 秒
源稿： [1]

讲述人旁白

早在 7 万年前，非洲东部的稀树草原上，生活着一群看起来很不起眼的动物。

它们没有尖牙利爪，跑不过羚羊，力气不如水牛。

但后来它们上能登月，下能探海，把整个地球翻了个底朝天。

画面演绎

地球从太空中缓慢旋转，镜头推进到非洲大陆，再落到东非稀树草原。
草丛里，一群瘦弱的早期智人小心移动。远处狮子、鬣狗、水牛、羚羊依次出现，形成压迫感。
画面突然快闪：火箭升空、潜水器下潜、城市夜景、卫星环绕地球。
最后全部画面缩回虚拟老师身后的黑板，黑板上写着：翻盘技能：讲故事

屏幕文字
7 万年前
非洲东部
智人：开局很弱
他们靠什么翻盘？

保留支撑点
智人一开始并不强。
通过和动物对比建立“弱小开局”。
用登月、探海制造巨大反差。
本集核心问题：弱小智人为什么能改变地球？
""".strip()


def test_standard_storyboard_scene_becomes_internal_shot_plan_with_keyframes():
    storyboard_script = parse_manual_storyboard_script(
        sample_standard_storyboard_text(),
        title="会讲故事的动物",
    )
    agent = ShotProductionAgent(
        shot_planner_provider=RuleBasedShotPlannerProvider(),
        keyframe_prompt_provider=RuleBasedKeyframePromptProvider(),
    )

    payload = agent.generate(
        generation={"generation_id": "gen-001", "topic": "会讲故事的动物", "script": {}},
        storyboard_script=storyboard_script,
    )

    shots = payload["shots"]
    assert payload["source_type"] == "standard_storyboard_script"
    assert payload["scene_blocks"][0]["scene_block_id"] == "S01"
    assert 4 <= len(shots) <= 6
    assert shots[0]["shot_id"] == "S01-SH01"
    assert {shot["scene_block_id"] for shot in shots} == {"S01"}
    assert shots[0]["raw"]["start_sec"] == 0
    assert shots[-1]["raw"]["end_sec"] == 25
    assert sum(shot["duration_sec"] for shot in shots) == 25
    assert all("这是单张关键帧" in shot["keyframe_prompt"] for shot in shots)
    assert all("静态画面：" in shot["keyframe_prompt"] for shot in shots)
    assert all("动作：" not in shot["keyframe_prompt"] for shot in shots)
    assert all("镜头推进" not in shot["keyframe_prompt"] for shot in shots)
    assert all("缓慢旋转" not in shot["keyframe_prompt"] for shot in shots)
    assert all("写实照片" in shot["negative_prompt"] for shot in shots)
    assert any("虚拟老师" in shot["visual_goal"] for shot in shots)
    assert not any(shot["narration"] == "讲述人旁白" for shot in shots)


def test_deepseek_production_agents_default_to_v4_pro():
    assert DeepSeekShotPlannerProvider(api_key="test-key").model == "deepseek-v4-pro"
    assert DeepSeekKeyframePromptProvider(api_key="test-key").model == "deepseek-v4-pro"
