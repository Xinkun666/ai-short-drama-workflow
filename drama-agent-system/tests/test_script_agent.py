import json
from pathlib import Path

from drama_agents.script_agent import (
    DeepSeekScriptProvider,
    ScriptAgent,
    build_review_prompt,
    build_script_prompt,
    format_year_label,
    parse_llm_json_object,
    parse_time_range,
)
from drama_agents.storage import MaterialDatabase


class FakeScriptProvider:
    def __init__(self):
        self.last_payload = None
        self.last_review_draft = None
        self.review_calls = 0
        self.revision_payload = None

    def generate_script(self, payload):
        self.last_payload = payload
        return {
            "title": "地球上出现了一种会讲故事的动物",
            "logline": "一群智人靠故事把生存难题讲成了团队副本。",
            "fact_cards": [
                {
                    "id": "F1",
                    "fact": "智人在非洲逐步形成",
                    "time": "约30万年前至7万年前",
                    "place": "非洲",
                    "source_basis": "来自 ch01-e001 content",
                    "confidence": "高",
                    "drama_direction": "作为会讲故事动物的开局",
                    "do_not_overstate": "不要写成唯一原因",
                }
            ],
            "causal_chain": ["智人形成 → 协作压力增加 → 语言和故事能力变重要"],
            "outline": [
                {
                    "title": "问题出现",
                    "core_point": "智人并非天生霸主",
                    "opening_image": "非洲草原",
                    "human_action": "小群体协作",
                    "conflict": "生存压力",
                    "change": "发展协作",
                    "cost": "抚养和学习成本",
                    "transition": "引出故事能力",
                }
            ],
            "article": "30万年前，智人登场。别看装备简陋，脑内系统已经开始更新。\n\n这不是版本更新，这是整个人类服务器开服。",
            "fact_boundaries": {
                "explicitly_supported": ["智人在非洲逐步形成"],
                "dramatized_inference": ["服务器开服是比喻"],
                "needs_manual_check": [],
                "possible_overstatement": [],
                "suggested_sources": [],
            },
            "subjects": [
                {
                    "name": "智人",
                    "type": "人群",
                    "intro": "解剖学意义上的现代人，是本集主角。",
                    "visual_modeling": "用轻量卡通人物表现，突出大脑、语言和群体协作。",
                    "script_usage": "负责承担故事开局和认知能力变化。",
                },
                {
                    "name": "石器",
                    "type": "物件",
                    "intro": "早期人类加工和利用环境的重要工具。",
                    "visual_modeling": "做成手持工具、地面道具和图标。",
                    "script_usage": "用于表现生存工具箱。",
                },
            ],
            "map_shots": [
                {
                    "title": "非洲开局图",
                    "region": "africa",
                    "places": ["非洲"],
                    "route": None,
                    "description": "展示智人在非洲出现与活动的背景。",
                    "script_scene": 1,
                }
            ],
        }

    def review_script(self, source_payload, draft):
        self.review_calls += 1
        self.last_review_draft = draft
        return {
            "passed": True,
            "score": 5,
            "verdict": "主题完整，材料使用充分。",
            "theme_alignment": "贴合主题。",
            "story_completeness": "前因、过程、结果完整。",
            "continuity": "场景连贯。",
            "material_usage": "使用了时间线 content。",
            "key_node_depth": "关键节点展开充分。",
            "simplicity_risk": "不简陋。",
            "missing_content": [],
            "issues": [],
            "revision_brief": "",
        }

    def revise_script(self, source_payload, draft, review):
        self.revision_payload = {"source_payload": source_payload, "draft": draft, "review": review}
        return self.generate_script(source_payload)


class RevisingFakeScriptProvider(FakeScriptProvider):
    def review_script(self, source_payload, draft):
        self.review_calls += 1
        if self.review_calls == 1:
            return {
                "passed": False,
                "score": 2,
                "verdict": "初稿只写了开局，主题核心太薄。",
                "theme_alignment": "没有充分围绕会讲故事的动物展开。",
                "story_completeness": "缺少认知革命的诞生过程与影响。",
                "continuity": "从铺垫跳到结论。",
                "material_usage": "没有充分使用 event.content。",
                "key_node_depth": "关键节点只是一笔带过。",
                "simplicity_risk": "内容简陋。",
                "missing_content": ["语言和故事能力的形成过程", "虚构故事如何扩大合作"],
                "issues": [
                    {
                        "severity": "major",
                        "category": "completeness",
                        "description": "核心机制缺失。",
                        "suggestion": "补写认知革命的过程、结果和后续影响。",
                    }
                ],
                "revision_brief": "围绕主题核心大幅返修。",
            }
        return {
            "passed": True,
            "score": 5,
            "verdict": "返修后通过。",
            "theme_alignment": "主题核心明确。",
            "story_completeness": "完整。",
            "continuity": "连贯。",
            "material_usage": "充分。",
            "key_node_depth": "充分。",
            "simplicity_risk": "不简陋。",
            "missing_content": [],
            "issues": [],
            "revision_brief": "",
        }

    def revise_script(self, source_payload, draft, review):
        self.revision_payload = {"source_payload": source_payload, "draft": draft, "review": review}
        revised = self.generate_script(source_payload)
        revised["article"] = "返修后，剧本把认知革命为什么出现、语言和虚构故事如何扩大合作、这些能力如何改变智人处境讲清楚。"
        return revised


def seed_timeline(database: MaterialDatabase) -> None:
    database.upsert_parse(
        {
            "record_id": "demo",
            "parsed_at": "2026-06-20 10:00:00",
            "book_name": "人类简史材料",
            "source_relative_path": "资料库/demo.md",
            "chapter_count": 1,
            "timeline_status": "not_run",
            "output_relative_path": "material_splits/demo",
        },
        {"chapters": [], "excluded_sections": [], "warnings": []},
    )
    database.update_timeline(
        "demo",
        {
            "status": "completed",
            "message": "ok",
            "event_count": 2,
            "events": [
                {
                    "event_id": "ch01-e001",
                    "chapter_id": "ch01",
                    "title": "智人在非洲逐步形成",
                    "content": "原文围绕智人在非洲出现、逐步适应环境并发展协作能力展开。",
                    "time_label": "约 30 万年前至 7 万年前",
                    "time_start_year": -300000,
                    "time_end_year": -70000,
                    "time_precision": "range",
                    "place_label": "非洲",
                    "place_scope": "continent",
                    "places": ["非洲"],
                    "source_pages": [1, 2],
                    "importance": 5,
                    "confidence": "high",
                    "evidence_note": "时间、地点、内容均来自原文时间线。",
                    "drama_potential": "适合作为人类登场开场。",
                },
                {
                    "event_id": "ch02-e001",
                    "chapter_id": "ch02",
                    "title": "农业村落扩张",
                    "content": "原文围绕农业出现之后的定居生活展开。",
                    "time_label": "约公元前 10000 年至公元前 8000 年",
                    "time_start_year": -10000,
                    "time_end_year": -8000,
                    "time_precision": "range",
                    "place_label": "西亚",
                    "place_scope": "region",
                    "places": ["西亚"],
                    "source_pages": [20],
                    "importance": 4,
                    "confidence": "high",
                    "evidence_note": "来自农业章节。",
                    "drama_potential": "适合作为农业转折。",
                },
            ],
        },
    )


def test_parse_time_range_understands_before_present_and_bce():
    assert parse_time_range("约 30 万年前 — 7 万年前") == (-300000, -70000)
    assert parse_time_range("30万年前到 7 万年前") == (-300000, -70000)
    assert parse_time_range("30万年前至7万年前") == (-300000, -70000)
    assert parse_time_range("约公元前 10000 年 — 公元前 8000 年") == (-10000, -8000)
    assert parse_time_range("公元 2000 年 — 至今")[0] == 2000


def test_format_year_label_uses_wan_years_for_deep_prehistory():
    assert format_year_label(-300000) == "30万年前"
    assert format_year_label(-70000) == "7万年前"
    assert format_year_label(-3000) == "公元前 3000 年"
    assert format_year_label(2000) == "公元 2000 年"


def test_script_agent_from_environment_uses_deepseek_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")

    agent = ScriptAgent.from_environment()

    assert isinstance(agent.provider, DeepSeekScriptProvider)
    assert agent.provider.api_key == "test-deepseek-key"


def test_deepseek_script_provider_repairs_invalid_json_once(monkeypatch):
    provider = DeepSeekScriptProvider(api_key="test-key", model="deepseek-test")
    prompts = []

    def fake_complete_once(*, system, prompt, temperature, max_tokens):
        prompts.append(prompt)
        if len(prompts) == 1:
            raise json.JSONDecodeError("Unterminated string starting at", '{"article": "abc', 12)
        assert temperature == 0.2
        return {"ok": True}

    monkeypatch.setattr(provider, "_complete_json_once", fake_complete_once)

    result = provider._complete_json(system="system", prompt="original prompt", temperature=0.75, max_tokens=1000)

    assert result == {"ok": True}
    assert prompts[0] == "original prompt"
    assert "上一次输出不是合法 JSON" in prompts[1]
    assert "Unterminated string starting at" in prompts[1]
    assert "使用 \\n\\n" in prompts[1]
    assert "不要截断字符串" in prompts[1]


def test_deepseek_script_provider_reports_preview_when_repair_still_fails(monkeypatch):
    provider = DeepSeekScriptProvider(api_key="test-key", model="deepseek-test")

    def fake_complete_once(*, system, prompt, temperature, max_tokens):
        provider._last_parse_error_detail = "finish_reason=length；model=deepseek-test；content=<空内容>"
        raise json.JSONDecodeError("Expecting value", "", 0)

    monkeypatch.setattr(provider, "_complete_json_once", fake_complete_once)

    try:
        provider._complete_json(system="system", prompt="original prompt", temperature=0.75, max_tokens=1000)
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected RuntimeError")

    assert "DeepSeek 返回的 JSON 仍无法解析" in message
    assert "finish_reason=length" in message
    assert "content=<空内容>" in message


def test_parse_llm_json_object_accepts_prefixed_model_text():
    result = parse_llm_json_object('好的，下面是 JSON：\n{"title": "测试", "article": "正文"}\n请查收。')

    assert result == {"title": "测试", "article": "正文"}


def test_script_agent_filters_events_and_writes_artifacts(tmp_path):
    database = MaterialDatabase(tmp_path / "workstation.sqlite3")
    seed_timeline(database)
    provider = FakeScriptProvider()

    result = ScriptAgent(provider=provider).generate(
        topic="地球上出现了一种会讲故事的动物",
        time_range_text="约 30 万年前 — 7 万年前",
        record_ids=["demo"],
        database=database,
        output_dir=tmp_path / "scripts",
    )

    assert result["status"] == "completed"
    assert result["topic"] == "地球上出现了一种会讲故事的动物"
    assert [event["event_id"] for event in result["matched_events"]] == ["ch01-e001"]
    assert provider.last_payload["topic"] == "地球上出现了一种会讲故事的动物"
    assert provider.last_payload["events"][0]["title"] == "智人在非洲逐步形成"
    assert result["script"]["title"] == "地球上出现了一种会讲故事的动物"
    assert result["script"]["fact_cards"][0]["id"] == "F1"
    assert result["script"]["causal_chain"][0].startswith("智人形成")
    assert result["script"]["outline"][0]["title"] == "问题出现"
    assert "服务器开服" in result["script"]["article"]
    assert result["script"]["fact_boundaries"]["explicitly_supported"] == ["智人在非洲逐步形成"]
    assert result["script"]["scenes"] == []
    assert result["subjects"] == []
    assert result["map_shots"] == []
    assert "subjects" not in provider.last_review_draft
    assert "map_shots" not in provider.last_review_draft
    assert result["script_review"]["passed"] is True
    assert result["revision_count"] == 0
    assert provider.review_calls == 1
    assert Path(result["json_path"]).exists()


def test_script_agent_accepts_explicit_numeric_year_range(tmp_path):
    database = MaterialDatabase(tmp_path / "workstation.sqlite3")
    seed_timeline(database)
    provider = FakeScriptProvider()

    result = ScriptAgent(provider=provider).generate(
        topic="地球上出现了一种会讲故事的动物",
        time_range_text="",
        time_start_year=-300000,
        time_end_year=-70000,
        record_ids=["demo"],
        database=database,
        output_dir=tmp_path / "scripts",
    )

    assert result["time_start_year"] == -300000
    assert result["time_end_year"] == -70000
    assert result["time_range"] == "30万年前 — 7万年前"
    assert provider.last_payload["time_start_year"] == -300000
    assert provider.last_payload["time_end_year"] == -70000
    assert provider.last_payload["time_range"] == "30万年前 — 7万年前"
    assert [event["event_id"] for event in result["matched_events"]] == ["ch01-e001"]


def test_script_agent_reviews_and_revises_weak_draft(tmp_path):
    database = MaterialDatabase(tmp_path / "workstation.sqlite3")
    seed_timeline(database)
    provider = RevisingFakeScriptProvider()

    result = ScriptAgent(provider=provider).generate(
        topic="地球上出现了一种会讲故事的动物",
        time_range_text="约 30 万年前 — 7 万年前",
        record_ids=["demo"],
        database=database,
        output_dir=tmp_path / "scripts",
    )

    assert result["revision_count"] == 1
    assert len(result["review_history"]) == 2
    assert result["review_history"][0]["passed"] is False
    assert result["script_review"]["passed"] is True
    assert provider.revision_payload["review"]["missing_content"] == ["语言和故事能力的形成过程", "虚构故事如何扩大合作"]
    assert "返修后" in result["script"]["article"]


def test_script_prompt_requires_complete_narrative_not_joke_summary():
    prompt = build_script_prompt(
        {
            "topic": "地球上出现了一种会讲故事的动物",
            "time_range": "约 30 万年前 — 7 万年前",
            "events": [
                {
                    "title": "智人在非洲逐步形成",
                    "content": "原文围绕智人在非洲出现、早期并非食物链顶端、逐步发展协作与语言能力展开。",
                    "time_label": "约 30 万年前至 7 万年前",
                    "place_label": "非洲",
                }
            ],
        }
    )

    assert "严肃史料短剧化改编" in prompt
    assert "严肃史料短剧化改编总编剧 Agent" in prompt
    assert "严肃史料 → 因果重构 → 场景扩写 → 短剧化表达 → 事实边界控制" in prompt
    assert "Step 1：先提取史实卡片" in prompt
    assert "Step 2：重建因果链" in prompt
    assert "Step 3：设计段落大纲" in prompt
    assert "Step 5：输出事实边界提醒" in prompt
    assert '"fact_cards"' in prompt
    assert '"causal_chain"' in prompt
    assert '"outline"' in prompt
    assert '"fact_boundaries"' in prompt
    assert "不是摘要" in prompt
    assert "不要划分场景" in prompt
    assert '"article"' in prompt
    assert "幽默只是表达方式，不是目的" in prompt
    assert "早在多少年以前" in prompt
    assert "早期智人在食物链的地位并不是一开始就处于顶端" in prompt
    assert "content 都是从原文章节中整理出来的“原文压缩材料”" in prompt
    assert "必须优先消化这些 content" in prompt
    assert "不要把前史铺垫写成主菜" in prompt
    assert "主题核心必须占全片 50%-60% 以上" in prompt
    assert "核心必须落在语言、象征、虚构故事、集体想象、社群合作、知识传播" in prompt
    assert "连续多个自然段" in prompt
    assert "为什么会出现这个变化？" in prompt
    assert "任何事的发生都要尽量写出前因、经过和后果" in prompt
    assert "不能把概念当答案" in prompt
    assert "4-7 个“关键节点”" in prompt
    assert "每个关键节点至少写 2 个自然段" in prompt
    assert "主题核心节点至少写 3-5 个自然段" in prompt
    assert "3000-5000 个中文字符" in prompt
    assert '"subjects"' not in prompt
    assert '"map_shots"' not in prompt
    assert "notes_for_later_agents" not in prompt
    assert "主体清单" not in prompt
    assert "地图画面清单" not in prompt


def test_script_review_prompt_rejects_simple_word_count_passing():
    prompt = build_review_prompt(
        {
            "topic": "地球上出现了一种会讲故事的动物",
            "time_range": "约 30 万年前 — 7 万年前",
            "events": [{"title": "认知革命", "content": "原文围绕语言、象征和虚构故事如何扩大合作展开。"}],
        },
        {
            "title": "短剧",
            "logline": "",
            "article": "认知革命来了。",
            "subjects": [],
            "map_shots": [],
        },
    )

    assert "不要因为字数达标就判定通过" in prompt
    assert "结构完整性" in prompt
    assert "fact_cards、causal_chain、outline、article、fact_boundaries" in prompt
    assert "事实边界" in prompt
    assert "故事完整性" in prompt
    assert "主题贴合度" in prompt
    assert "材料利用度" in prompt
    assert "关键节点丰富度" in prompt
    assert "如果任何关键节点只用一句话或一小段带过" in prompt
    assert "内容简陋、概念化、空泛" in prompt
