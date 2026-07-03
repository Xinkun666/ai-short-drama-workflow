from drama_agents.script_agent import (
    DeepSeekScriptProvider,
    ScriptAgent,
    build_content_atoms_extraction_prompt,
    build_narrator_scene_adaptation_prompt,
    build_narrator_scene_batch_prompt,
    build_narrator_scene_plan_prompt,
    build_repair_missing_atoms_prompt,
    build_storyboard_script_adaptation_prompt,
    merge_repaired_scenes,
    normalize_content_atoms_payload,
    normalize_historical_character_dialogue,
    normalize_narrator_scene_script_payload,
    normalize_scene_plan_payload,
    normalize_visual_layer,
    parse_manual_storyboard_script,
    scene_script_to_readable_storyboard_text,
    scene_script_to_legacy_adapted_segments,
    validate_content_retention,
    validate_narrator_scene_script,
)


def sample_scene_payload():
    return {
        "title": "会讲故事的动物",
        "format": "narrator_led_science_comic",
        "narrator_profile": {
            "role": "虚拟科普老师",
            "tone": "幽默、清楚、有纪录片感",
            "visual_presence": "avatar",
        },
        "episode_structure": {
            "main_question": "智人为什么能扩大合作？",
            "core_thesis": "故事让陌生人围绕共同想象协作。",
            "target_duration_min": 8,
            "estimated_scene_count": 16,
        },
        "scene_script": [
            {
                "scene_id": "custom-id",
                "scene_title": "开场问题",
                "scene_type": "HOST_OPENING + MAP_ANIMATION",
                "duration_sec": 20,
                "beat_function": "开场钩子",
                "source_atoms": ["P01-A01"],
                "knowledge_payload": {
                    "core_question": "智人为什么能扩大合作？",
                    "reasoning_chain": "生存压力增加 → 需要更大规模协作 → 故事提供共同想象。",
                    "must_keep_details": ["故事让陌生人围绕共同想象协作"],
                    "audience_takeaway": "故事能力把合作半径放大。",
                },
                "knowledge_point": "智人不是天生霸主",
                "narrator_lines": ["先别急着把智人想成主角光环。", "这集的问题是：他们凭什么合作变大？"],
                "visual_layer": {
                    "main_visual": "非洲地图上出现一小群智人剪影",
                    "character_action": "小群体围火观察远处",
                    "environment": "史前非洲，夜色和篝火",
                    "camera": "从地图推到人群",
                    "animation_logic": "问号和箭头从人群上方浮现",
                    "transition": "问号变成下一场标题",
                },
                "screen_text": ["核心问题", "合作如何变大"],
                "historical_character_dialogue": [
                    {
                        "speaker": "部落成员",
                        "line": "有动静。",
                        "purpose": "气氛",
                        "evidence_level": "合理场景化",
                    }
                ],
                "audio_hint": "轻悬疑音乐",
                "fact_boundary": "合理场景化",
                "source_trace": ["outline:问题出现"],
                "next_scene_hook": "下一场解释困境。",
            }
        ],
        "adaptation_notes": ["改成讲述人驱动"],
        "review_notes": ["核对具体时间"],
    }


def test_normalize_narrator_scene_script_payload_normalizes_complete_payload():
    result = normalize_narrator_scene_script_payload(sample_scene_payload(), fallback_article="备用正文")

    assert result["format"] == "narrator_led_science_comic"
    assert result["narrator_profile"]["role"] == "虚拟科普老师"
    assert result["episode_structure"]["main_question"] == "智人为什么能扩大合作？"
    assert result["scene_script"][0]["scene_id"] == "custom-id"
    assert result["scene_script"][0]["source_atoms"] == ["P01-A01"]
    assert result["scene_script"][0]["knowledge_payload"]["reasoning_chain"].startswith("生存压力")
    assert result["scene_script"][0]["narrator_lines"] == ["先别急着把智人想成主角光环。", "这集的问题是：他们凭什么合作变大？"]
    assert result["scene_script"][0]["visual_layer"]["main_visual"] == "非洲地图上出现一小群智人剪影"
    assert result["scene_script"][0]["screen_text"] == ["核心问题", "合作如何变大"]
    assert result["adapted_article"] == "先别急着把智人想成主角光环。\n这集的问题是：他们凭什么合作变大？"
    assert result["adapted_segments"][0]["voiceover"] == "先别急着把智人想成主角光环。\n这集的问题是：他们凭什么合作变大？"
    assert result["scene_review"]["passed"] is True


def test_normalize_narrator_scene_script_payload_fills_missing_fields():
    result = normalize_narrator_scene_script_payload(
        {
            "scene_script": [
                {
                    "narrator_lines": "一句讲述人台词",
                    "visual_layer": "篝火旁的人群",
                }
            ]
        },
        fallback_article="备用正文",
    )

    scene = result["scene_script"][0]
    assert result["title"] == "分场脚本"
    assert result["format"] == "narrator_led_science_comic"
    assert result["narrator_profile"]["visual_presence"] == "mixed"
    assert result["episode_structure"]["target_duration_min"] == 8
    assert scene["scene_id"] == "S01"
    assert scene["scene_title"] == "未命名场景"
    assert scene["scene_type"] == "HOST_EXPLANATION"
    assert scene["duration_sec"] == 25
    assert scene["source_atoms"] == []
    assert scene["knowledge_payload"]["core_question"] == ""
    assert scene["knowledge_payload"]["reasoning_chain"] == ""
    assert scene["knowledge_payload"]["must_keep_details"] == []
    assert scene["knowledge_payload"]["audience_takeaway"] == ""
    assert scene["narrator_lines"] == ["一句讲述人台词"]
    assert scene["screen_text"] == []
    assert scene["visual_layer"]["main_visual"] == "篝火旁的人群"
    assert scene["visual_layer"]["transition"] == ""
    assert scene["historical_character_dialogue"] == []


def test_scene_script_to_legacy_adapted_segments_derives_segments():
    segments = scene_script_to_legacy_adapted_segments(sample_scene_payload()["scene_script"])

    assert segments == [
        {
            "segment_id": "seg-001",
            "voiceover": "先别急着把智人想成主角光环。\n这集的问题是：他们凭什么合作变大？",
            "dramatic_function": "开场钩子",
            "visual_goal": "非洲地图上出现一小群智人剪影",
            "visual_progression": "问号和箭头从人群上方浮现",
            "scene_intent": "HOST_OPENING + MAP_ANIMATION + 智人为什么能扩大合作？",
            "continuity_hint": "下一场解释困境。",
            "fact_boundary": "合理场景化",
        }
    ]


def test_validate_narrator_scene_script_flags_modern_knowledge_in_character_dialogue():
    payload = sample_scene_payload()
    payload["scene_script"][0]["historical_character_dialogue"][0]["line"] = "认知革命证明语言能扩大社会分工。"

    review = validate_narrator_scene_script(payload)

    assert review["passed"] is False
    assert review["score"] <= 3
    assert any(issue["category"] == "dialogue" and issue["severity"] == "major" for issue in review["issues"])


def test_validate_narrator_scene_script_flags_empty_scene_script():
    review = validate_narrator_scene_script({"scene_script": []})

    assert review["passed"] is False
    assert review["score"] == 1
    assert review["issues"][0]["severity"] == "critical"
    assert review["issues"][0]["category"] == "structure"


def test_storyboard_adaptation_prompt_contains_narrator_led_constraints():
    prompt = build_storyboard_script_adaptation_prompt(
        {
            "topic": "地球上出现了一种会讲故事的动物",
            "time_range": "约 30 万年前 — 7 万年前",
            "article": "文章式旁白",
            "script": {"title": "会讲故事的动物"},
        }
    )

    assert "讲述人驱动" in prompt
    assert "历史人物不能承担知识解释" in prompt
    assert "不要按原文自然段机械切场" in prompt
    assert "narrator_led_science_comic" in prompt
    assert "HOST_OPENING" in prompt
    assert "THEME_CALLBACK" in prompt
    assert "scene_script" in prompt


def test_normalize_content_atoms_payload_full():
    payload = {
        "content_atoms": [
            {
                "atom_id": "P02-A01",
                "source_paragraph": 2,
                "atom_type": "comparison",
                "text": "智人大脑休息时消耗约25%能量，大猩猩约8%。",
                "reasoning_role": "contrast",
                "must_keep": True,
                "compression_allowed": False,
                "visual_potential": "能量条对比",
                "narrator_hint": "用怠速烧油解释",
            }
        ],
        "causal_chain": [
            {"step_id": "C01", "from_atoms": ["P02-A01"], "to_atoms": ["P02-A02"], "logic": "大脑耗能高带来找食压力。"}
        ],
        "retention_requirements": {"must_keep_atom_ids": ["P02-A01"], "minimum_retention_ratio": 0.95},
    }

    result = normalize_content_atoms_payload(payload, fallback_article="备用原文")

    assert result["content_atoms"][0]["atom_id"] == "P02-A01"
    assert result["content_atoms"][0]["atom_type"] == "comparison"
    assert result["content_atoms"][0]["reasoning_role"] == "contrast"
    assert result["retention_requirements"]["minimum_retention_ratio"] == 0.95
    assert result["retention_requirements"]["must_keep_atom_ids"] == ["P02-A01"]


def test_normalize_content_atoms_payload_missing_fields():
    result = normalize_content_atoms_payload({"content_atoms": [{"text": "智人需要协作。"}]})

    atom = result["content_atoms"][0]
    assert atom["atom_id"] == "A001"
    assert atom["source_paragraph"] == 0
    assert atom["atom_type"] == "claim"
    assert atom["reasoning_role"] == "premise"
    assert atom["must_keep"] is True
    assert atom["compression_allowed"] is False
    assert atom["visual_potential"] == ""
    assert atom["narrator_hint"] == ""
    assert result["causal_chain"] == []
    assert result["retention_requirements"]["minimum_retention_ratio"] == 0.92


def test_validate_content_retention_missing_must_keep():
    atoms_payload = normalize_content_atoms_payload(
        {
            "content_atoms": [
                {"atom_id": "P01-A01", "text": "智人大脑休息时消耗约25%能量", "atom_type": "fact"},
                {"atom_id": "P01-A02", "text": "大猩猩大脑约8%", "atom_type": "comparison"},
            ]
        }
    )
    scene_script = [
        {
            "source_atoms": ["P01-A01"],
            "knowledge_payload": {"reasoning_chain": "25% 的负担说明大脑昂贵。"},
            "narrator_lines": ["智人大脑休息时消耗约25%能量。"],
            "screen_text": ["25%"],
        }
    ]

    review = validate_content_retention(atoms_payload, scene_script)

    assert review["passed"] is False
    assert review["missing_must_keep_atoms"] == ["P01-A02"]
    assert any(issue["category"] == "coverage" for issue in review["issues"])


def test_validate_content_retention_low_coverage():
    atoms_payload = normalize_content_atoms_payload(
        {
            "content_atoms": [
                {"atom_id": "A1", "text": "事实一", "must_keep": False},
                {"atom_id": "A2", "text": "事实二", "must_keep": False},
                {"atom_id": "A3", "text": "事实三", "must_keep": False},
            ],
            "retention_requirements": {"minimum_retention_ratio": 0.9},
        }
    )

    review = validate_content_retention(atoms_payload, [{"source_atoms": ["A1"], "knowledge_payload": {"reasoning_chain": "一到二。"}}])

    assert review["passed"] is False
    assert review["coverage_ratio"] < 0.9


def test_validate_content_retention_missing_mechanism():
    atoms_payload = normalize_content_atoms_payload(
        {
            "content_atoms": [
                {"atom_id": "M1", "text": "火降低消化成本", "atom_type": "mechanism"},
                {"atom_id": "C1", "text": "因此能量分配改变", "atom_type": "cause_effect"},
            ]
        }
    )

    review = validate_content_retention(atoms_payload, [{"source_atoms": [], "knowledge_payload": {"reasoning_chain": ""}}])

    assert review["passed"] is False
    assert any(issue["severity"] == "major" and issue["category"] == "detail_loss" for issue in review["issues"])


def test_scene_script_to_legacy_adapted_segments():
    segments = scene_script_to_legacy_adapted_segments(sample_scene_payload()["scene_script"])

    assert segments[0]["voiceover"] == "先别急着把智人想成主角光环。\n这集的问题是：他们凭什么合作变大？"
    assert segments[0]["visual_goal"] == "非洲地图上出现一小群智人剪影"
    assert segments[0]["dramatic_function"] == "开场钩子"


def test_normalize_narrator_scene_script_payload_generates_legacy_fields():
    result = normalize_narrator_scene_script_payload({"scene_script": sample_scene_payload()["scene_script"]}, fallback_article="备用正文")

    assert result["adapted_article"].startswith("先别急着")
    assert result["adapted_segments"][0]["scene_intent"] == "HOST_OPENING + MAP_ANIMATION + 智人为什么能扩大合作？"


def test_parse_manual_storyboard_script_formats_target_style():
    raw_text = """
S01｜开场：地球上最会讲故事的动物

场景类型： HOST_OPENING + SYMBOLIC_MONTAGE
功能： 开场钩子 / 提出核心问题
时长： 25 秒
源稿： [1]

讲述人旁白

早在 7 万年前，非洲东部的稀树草原上，生活着一群看起来很不起眼的动物。

他们是怎么办到的？

画面演绎

地球从太空中缓慢旋转，镜头推进到非洲大陆。

屏幕文字
7 万年前
非洲东部
智人：开局很弱

历史人物对白

无。

保留支撑点
智人一开始并不强。
通过和动物对比建立“弱小开局”。
""".strip()

    payload = parse_manual_storyboard_script(raw_text, title="会讲故事的动物")
    scene = payload["scene_script"][0]
    readable = scene_script_to_readable_storyboard_text(payload["scene_script"])

    assert payload["title"] == "会讲故事的动物"
    assert payload["format"] == "manual_storyboard_script"
    assert scene["scene_id"] == "S01"
    assert scene["scene_title"] == "开场：地球上最会讲故事的动物"
    assert scene["scene_type"] == "HOST_OPENING + SYMBOLIC_MONTAGE"
    assert scene["duration_sec"] == 25
    assert scene["beat_function"] == "开场钩子 / 提出核心问题"
    assert scene["source_atoms"] == ["1"]
    assert scene["narrator_lines"] == [
        "早在 7 万年前，非洲东部的稀树草原上，生活着一群看起来很不起眼的动物。",
        "他们是怎么办到的？",
    ]
    assert scene["visual_layer"]["main_visual"].startswith("地球从太空中")
    assert scene["screen_text"] == ["7 万年前", "非洲东部", "智人：开局很弱"]
    assert scene["historical_character_dialogue"] == []
    assert scene["knowledge_payload"]["must_keep_details"] == [
        "智人一开始并不强。",
        "通过和动物对比建立“弱小开局”。",
    ]
    assert "S01｜开场：地球上最会讲故事的动物" in readable
    assert "讲述人旁白" in readable
    assert "画面演绎" in readable
    assert "保留支撑点" in readable
    assert payload["adapted_article"] == readable


def test_build_content_atoms_extraction_prompt_contains_constraints():
    prompt = build_content_atoms_extraction_prompt("现代智人平均脑容量1400毫升。", title="脑子太贵")

    assert "你不是摘要器" in prompt
    assert "不要压缩成大纲" in prompt
    assert "每个数字、对比、因果、机制、例子、类比、历史转折" in prompt
    assert "不要新增原文没有的史实" in prompt
    assert "数字对比、机制解释、关键例子、关键类比、历史转折默认 must_keep=true" in prompt


def test_build_narrator_scene_adaptation_prompt_contains_constraints():
    atoms_payload = normalize_content_atoms_payload({"content_atoms": [{"atom_id": "A001", "text": "智人大脑昂贵。"}]})
    prompt = build_narrator_scene_adaptation_prompt("原文", atoms_payload, title="会讲故事的动物")

    assert "不要为了短而删掉推理链" in prompt
    assert "不要只保留历史主线" in prompt
    assert "每个 scene 必须引用 source_atoms" in prompt
    assert "优先增加场景数量，不要删除 must_keep atoms" in prompt
    assert "历史人物只能做画面演绎，不能承担现代知识解释" in prompt


def test_build_narrator_scene_plan_prompt_requires_source_atoms():
    atoms_payload = normalize_content_atoms_payload(
        {
            "content_atoms": [
                {"atom_id": "P01-A01", "text": "智人大脑耗能高。"},
                {"atom_id": "P01-A02", "text": "大猩猩大脑耗能约8%。", "atom_type": "comparison"},
            ]
        }
    )
    prompt = build_narrator_scene_plan_prompt({"article": "原文", **atoms_payload})

    assert "不要写最终旁白" in prompt
    assert "每个 scene 必须引用 source_atoms" in prompt
    assert "优先增加场景数量，不要删除 must_keep atom" in prompt
    assert "must_keep=true 的 atom 必须全部进入某个 scene" in prompt


def test_normalize_scene_plan_payload():
    result = normalize_scene_plan_payload(
        {
            "scene_plan": [
                {
                    "scene_title": "脑子太贵",
                    "source_atoms": "P01-A01",
                    "must_keep_details": "25% vs 8%",
                    "estimated_duration_sec": "35",
                }
            ],
            "episode_structure": {"main_question": "为什么聪明也是负担？"},
        }
    )

    assert result["episode_structure"]["main_question"] == "为什么聪明也是负担？"
    assert result["episode_structure"]["target_duration_min"] == 10
    assert result["scene_plan"][0]["scene_id"] == "S01"
    assert result["scene_plan"][0]["scene_title"] == "脑子太贵"
    assert result["scene_plan"][0]["scene_type"] == "HOST_EXPLANATION"
    assert result["scene_plan"][0]["source_atoms"] == ["P01-A01"]
    assert result["scene_plan"][0]["must_keep_details"] == ["25% vs 8%"]
    assert result["scene_plan"][0]["estimated_duration_sec"] == 35


def test_build_narrator_scene_batch_prompt_contains_constraints():
    prompt = build_narrator_scene_batch_prompt(
        {
            "article": "原文",
            "content_atoms": [{"atom_id": "P01-A01", "text": "智人大脑耗能高。"}],
            "scene_plan": [{"scene_id": "S01", "source_atoms": ["P01-A01"]}],
        }
    )

    assert "你不是摘要器" in prompt
    assert "不要为了短而删掉推理链" in prompt
    assert "每个场景必须引用 source_atoms" in prompt
    assert "must_keep_details 必须进入 narrator_lines、visual_layer 或 screen_text" in prompt
    assert "输出严格 json object" in prompt


def test_build_repair_missing_atoms_prompt_and_merge_repaired_scenes():
    original = normalize_narrator_scene_script_payload(sample_scene_payload(), fallback_article="原文")
    repair_prompt = build_repair_missing_atoms_prompt(
        {
            "original_payload": original,
            "missing_must_keep_atoms": ["P02-A01"],
            "content_atoms": [{"atom_id": "P02-A01", "text": "火降低消化成本。"}],
        }
    )
    repaired = merge_repaired_scenes(
        original,
        {
            "scene_script": [
                {
                    "scene_id": "S02",
                    "scene_title": "火让身体省电",
                    "scene_type": "HOST_EXPLANATION + INFOGRAPHIC",
                    "source_atoms": ["P02-A01"],
                    "knowledge_payload": {"reasoning_chain": "火 → 熟食 → 消化成本下降。"},
                    "narrator_lines": ["火让食物更容易消化。"],
                    "visual_layer": {"main_visual": "篝火和能量条", "animation_logic": "能量条下降"},
                    "screen_text": ["消化成本下降"],
                    "fact_boundary": "需人工核对",
                }
            ]
        },
    )

    assert "不要重写全片" in repair_prompt
    assert "只补 missing_must_keep_atoms" in repair_prompt
    assert "每个新增或修复场景必须引用 source_atoms" in repair_prompt
    assert [scene["scene_id"] for scene in repaired["scene_script"]] == ["custom-id", "S02"]


def test_normalize_visual_layer_defaults():
    visual_layer = normalize_visual_layer({})

    assert set(visual_layer) >= {"main_visual", "character_action", "environment", "camera", "animation_logic", "transition"}


def test_normalize_historical_character_dialogue():
    dialogue = normalize_historical_character_dialogue(["来了。", {"speaker": "同伴", "line": "快跑。"}, 42])

    assert dialogue == [
        {"speaker": "角色", "line": "来了。", "purpose": "", "evidence_level": ""},
        {"speaker": "同伴", "line": "快跑。", "purpose": "", "evidence_level": ""},
    ]


class FakeStoryboardProvider:
    def adapt_script_for_storyboard(self, payload):
        return sample_scene_payload()


class MultiStageStoryboardProvider:
    def __init__(self):
        self.extract_args = None
        self.plan_args = None
        self.batch_payloads = []
        self.repair_payload = None

    def extract_content_atoms(self, payload):
        self.extract_args = payload
        return {
            "content_atoms": [
                {
                    "atom_id": "P01-A01",
                    "source_paragraph": 1,
                    "atom_type": "mechanism",
                    "text": "故事让陌生人围绕共同想象协作。",
                    "reasoning_role": "mechanism",
                }
                ,
                {
                    "atom_id": "P01-A02",
                    "source_paragraph": 1,
                    "atom_type": "comparison",
                    "text": "共同想象让合作人数从熟人小圈扩大到陌生人大群。",
                    "reasoning_role": "contrast",
                }
            ],
            "causal_chain": [
                {"step_id": "C01", "from_atoms": ["P01-A01"], "to_atoms": ["P01-A02"], "logic": "共同想象扩大合作。"}
            ],
        }

    def plan_narrator_scenes(self, payload):
        self.plan_args = payload
        return {
            "episode_structure": {"main_question": "故事怎样扩大合作？", "core_thesis": "共同想象扩大合作半径。"},
            "scene_plan": [
                {"scene_id": "S01", "scene_title": "故事机制", "source_atoms": ["P01-A01"], "knowledge_task": "讲清机制"},
                {"scene_id": "S02", "scene_title": "合作放大", "source_atoms": ["P01-A02"], "knowledge_task": "讲清对比"},
            ],
        }

    def write_narrator_scene_batch(self, payload):
        self.batch_payloads.append(payload)
        first_scene_id = payload["scene_plan"][0]["scene_id"]
        if first_scene_id != "S01":
            return {"scene_script": []}
        return sample_scene_payload()

    def repair_narrator_scene_script(self, payload):
        self.repair_payload = payload
        return {
            "scene_script": [
                {
                    "scene_id": "S02",
                    "scene_title": "合作放大",
                    "scene_type": "HOST_EXPLANATION + COMPARISON_SPLIT_SCREEN",
                    "duration_sec": 25,
                    "beat_function": "机制解释",
                    "source_atoms": ["P01-A02"],
                    "knowledge_payload": {
                        "core_question": "故事怎样让合作人数变多？",
                        "reasoning_chain": "共同想象 → 陌生人有同一套规则 → 合作半径扩大。",
                        "must_keep_details": ["从熟人小圈扩大到陌生人大群"],
                        "audience_takeaway": "故事让合作从认识的人扩展到不认识的人。",
                    },
                    "narrator_lines": ["共同想象把合作从熟人小圈，扩展到陌生人大群。"],
                    "visual_layer": {
                        "main_visual": "左侧小圈熟人，右侧大群陌生人围绕同一符号协作",
                        "animation_logic": "小圆圈扩展成大网络",
                    },
                    "screen_text": ["熟人小圈", "陌生人大群"],
                    "fact_boundary": "合理场景化",
                }
            ]
        }


def test_adapt_for_storyboard_attaches_scene_review():
    generation = {
        "topic": "会讲故事的动物",
        "time_range": "约 30 万年前 — 7 万年前",
        "matched_events": [],
        "script": {"title": "原剧本", "article": "原始文章式旁白"},
    }

    result = ScriptAgent(provider=FakeStoryboardProvider()).adapt_for_storyboard(generation=generation)

    assert result["scene_review"]["passed"] is True
    assert result["scene_script"][0]["scene_id"] == "custom-id"
    assert "adapted_article" in result
    assert "adapted_segments" in result


def test_adapt_for_storyboard_uses_multistage_batches_and_repair(monkeypatch):
    provider = MultiStageStoryboardProvider()
    monkeypatch.setenv("SCRIPT_SCENE_BATCH_SIZE", "1")
    generation = {
        "topic": "会讲故事的动物",
        "time_range": "约 30 万年前 — 7 万年前",
        "matched_events": [],
        "script": {"title": "原剧本", "article": "原始文章式旁白"},
    }

    result = ScriptAgent(provider=provider).adapt_for_storyboard(generation=generation)

    assert provider.extract_args["article"] == "原始文章式旁白"
    assert provider.extract_args["title"] == "原剧本"
    assert provider.plan_args["content_atoms"][0]["atom_id"] == "P01-A01"
    assert len(provider.batch_payloads) == 2
    assert provider.repair_payload["retention_review"]["missing_must_keep_atoms"] == ["P01-A02"]
    assert result["content_atoms"][0]["atom_type"] == "mechanism"
    assert result["scene_plan"][1]["scene_id"] == "S02"
    assert [scene["scene_id"] for scene in result["scene_script"]] == ["custom-id", "S02"]
    assert result["causal_chain"][0]["logic"] == "共同想象扩大合作。"
    assert result["retention_review"]["passed"] is True
    assert result["scene_review"]["passed"] is True


class RecordingDeepSeekProvider(DeepSeekScriptProvider):
    def __init__(self):
        super().__init__(api_key="test-key", model="base-model")
        self.calls = []

    def _complete_json(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True}


def test_adaptation_model_priority(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-env")
    monkeypatch.setenv("DEEPSEEK_SCRIPT_MODEL", "deepseek-script")
    monkeypatch.setenv("DEEPSEEK_ADAPTATION_MODEL", "deepseek-adapt")
    provider = RecordingDeepSeekProvider()

    assert provider.get_adaptation_model() == "deepseek-adapt"

    provider.plan_narrator_scenes({"article": "原文", "content_atoms": [], "causal_chain": []})

    assert provider.calls[-1]["model"] == "deepseek-adapt"
