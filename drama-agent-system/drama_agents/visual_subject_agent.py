from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from drama_agents.chapter_refiner import parse_json_object
from drama_agents.storage import normalize_visual_subject_payload


class VisualSubjectAgent:
    def __init__(self, provider=None):
        self.provider = provider or RuleBasedVisualSubjectProvider()

    @classmethod
    def from_environment(cls):
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            return cls(provider=RuleBasedVisualSubjectProvider())
        return cls(provider=DeepSeekVisualSubjectProvider(api_key=api_key))

    def extract(self, generation: dict[str, Any]) -> dict[str, Any]:
        payload = script_generation_payload(generation)
        extracted = self.provider.extract_subjects(payload)
        return normalize_extraction_payload(extracted)


class RuleBasedVisualSubjectProvider:
    def extract_subjects(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = searchable_script_text(payload)
        subjects: list[dict[str, Any]] = []

        def add_if_present(
            canonical_name: str,
            markers: list[str],
            *,
            aliases: list[str] | None = None,
            subject_type: str,
            role_in_script: str,
            importance: int,
            description: str,
            appearance: str,
            clothing: str = "",
            group_composition: str = "",
        ) -> None:
            if not any(marker in text for marker in markers):
                return
            subjects.append(
                subject_payload(
                    canonical_name=canonical_name,
                    aliases=aliases or [],
                    subject_type=subject_type,
                    role_in_script=role_in_script,
                    importance=importance,
                    first_appearance=first_appearance_for(text, markers),
                    description=description,
                    appearance=appearance,
                    clothing=clothing,
                    group_composition=group_composition,
                )
            )

        add_if_present(
            "智人",
            ["智人", "现代人类的祖先", "现代智人的祖先", "人类祖先"],
            aliases=["早期智人", "现代人类的祖先"],
            subject_type="species",
            role_in_script="本集核心主体，承载人类认知跃迁和跨区域扩散的主线。",
            importance=5,
            description="需要在多个镜头中保持可识别的早期现代人类形象。",
            appearance="深色皮肤、粗糙黑发，身形较瘦但灵活的早期现代人类。",
            clothing="简陋兽皮、植物纤维或原始披挂。",
            group_composition="男女老少混合的小型部落群体。",
        )
        if "早期智人" in text and "群体" in text:
            subjects.append(
                subject_payload(
                    canonical_name="早期智人群体",
                    aliases=["早期智人部落", "智人迁徙群体"],
                    subject_type="group",
                    role_in_script="作为群像主角，表现围火讲述、协作迁徙和共同想象的形成。",
                    importance=5,
                    first_appearance=first_appearance_for(text, ["早期智人群体", "早期智人"]),
                    description="反复出现的智人群像，需要和单个角色、其他古人类族群区分。",
                    appearance="早期现代人类群像，年龄和性别混合，动作协作而警觉。",
                    clothing="兽皮披挂、草绳束带和粗糙工具携带。",
                    group_composition="围绕火光、狩猎或迁徙组织起来的小群体。",
                )
            )
        add_if_present(
            "尼安德特人",
            ["尼安德特人"],
            subject_type="species",
            role_in_script="与智人形成对照的古人类族群，帮助观众理解同一时代的不同人群。",
            importance=4,
            description="需要保持区别于智人的体态和面部特征。",
            appearance="体格更粗壮，眉脊明显，面部轮廓厚重的古人类。",
            clothing="寒冷地区感更强的兽皮披挂。",
            group_composition="小规模古人类群体。",
        )
        add_if_present(
            "直立人",
            ["直立人"],
            subject_type="species",
            role_in_script="作为更早的古人类参照，呈现人类演化谱系的纵深。",
            importance=3,
            description="中优先级主体，适合在谱系或对照镜头中保持外观一致。",
            appearance="更原始的古人类体态，额头较低，姿态结实。",
            clothing="极简原始覆盖物或无明显缝制衣物。",
            group_composition="小规模觅食群体。",
        )
        add_if_present(
            "丹尼索瓦人",
            ["丹尼索瓦人"],
            subject_type="species",
            role_in_script="作为同时期古人类支系出现，补足跨区域古人类世界观。",
            importance=3,
            description="中优先级主体，后续可用于支线和对照镜头复用。",
            appearance="带有高原或寒冷环境适应感的古人类形象，轮廓厚重。",
            clothing="兽皮、粗纤维束带和石器。",
            group_composition="小型古人类族群。",
        )
        add_if_present(
            "智人部落老者",
            ["老者", "讲述者", "部落讲述者"],
            aliases=["部落讲述者", "智人老者"],
            subject_type="character",
            role_in_script="如果多次出现，可承担讲述、传承和群体记忆的角色。",
            importance=3,
            description="可复用的智人叙事角色，不与整个智人族群混淆。",
            appearance="年长智人，脸部有皱纹，目光沉稳，姿态像讲述者。",
            clothing="旧兽皮披肩、简单骨饰或贝壳饰物。",
        )
        add_if_present(
            "智人猎人群体",
            ["智人猎人", "猎人", "狩猎"],
            aliases=["智人猎人"],
            subject_type="group",
            role_in_script="用于动作镜头和生存压力镜头，体现协作狩猎和风险应对。",
            importance=3,
            description="反复承担行动功能的智人小队，需要稳定工具和姿态设定。",
            appearance="年轻到壮年的智人小队，肌肉紧绷，动作敏捷。",
            clothing="便于奔跑的兽皮和植物纤维绑缚。",
            group_composition="三到六人的猎人小队，携带木矛和石器。",
        )

        rejected_candidates = rejected_from_text(text)
        return {"subjects": subjects, "rejected_candidates": rejected_candidates}


class DeepSeekVisualSubjectProvider:
    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        base_url: str = "https://api.deepseek.com/chat/completions",
        timeout: int | None = None,
    ):
        self.api_key = api_key
        self.model = (
            model
            or os.environ.get("DEEPSEEK_VISUAL_SUBJECT_MODEL")
            or os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
        )
        self.base_url = base_url
        self.timeout = timeout or int(os.environ.get("DEEPSEEK_TIMEOUT", "240"))

    def extract_subjects(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._complete_json(build_visual_subject_prompt(payload))

    def _complete_json(self, prompt: str) -> dict[str, Any]:
        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是短剧视觉主体解析 Agent，只提取需要跨镜头保持视觉一致性的主体。"
                        "只输出合法 JSON，不要 Markdown 代码围栏。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.15,
            "max_tokens": int(os.environ.get("VISUAL_SUBJECT_MAX_TOKENS", "4000")),
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


def script_generation_payload(generation: dict[str, Any]) -> dict[str, Any]:
    script = generation.get("script") if isinstance(generation.get("script"), dict) else {}
    return {
        "title": script.get("title") or generation.get("script_title") or generation.get("topic") or "",
        "topic": generation.get("topic") or "",
        "article": script.get("article") or "",
        "fact_cards": script.get("fact_cards") or [],
        "causal_chain": script.get("causal_chain") or [],
        "outline": script.get("outline") or [],
    }


def normalize_extraction_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_subjects = payload.get("subjects") if isinstance(payload, dict) else []
    raw_rejected = payload.get("rejected_candidates") if isinstance(payload, dict) else []
    subjects = []
    seen: set[str] = set()
    for item in raw_subjects or []:
        if not isinstance(item, dict):
            continue
        normalized = normalize_visual_subject_payload(item)
        identity_key = f"{normalized['canonical_name']}::{normalized['visual_phase_key']}"
        if not normalized["canonical_name"] or identity_key in seen:
            continue
        seen.add(identity_key)
        subjects.append(normalized)
    rejected_candidates = []
    for candidate in raw_rejected or []:
        if not isinstance(candidate, dict):
            continue
        name = str(candidate.get("name") or "").strip()
        reason = str(candidate.get("reason") or "").strip()
        if name and reason:
            rejected_candidates.append({"name": name, "reason": reason})
    return {"subjects": subjects, "rejected_candidates": rejected_candidates}


def searchable_script_text(payload: dict[str, Any]) -> str:
    parts = [
        payload.get("title", ""),
        payload.get("topic", ""),
        payload.get("article", ""),
        json.dumps(payload.get("fact_cards") or [], ensure_ascii=False),
        json.dumps(payload.get("causal_chain") or [], ensure_ascii=False),
        json.dumps(payload.get("outline") or [], ensure_ascii=False),
    ]
    return "\n".join(str(part) for part in parts if part)


def first_appearance_for(text: str, markers: list[str]) -> str:
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n{2,}|(?<=[。！？])", text) if paragraph.strip()]
    for paragraph in paragraphs:
        if any(marker in paragraph for marker in markers):
            return paragraph[:180]
    return ""


def subject_payload(
    *,
    canonical_name: str,
    aliases: list[str],
    subject_type: str,
    role_in_script: str,
    importance: int,
    first_appearance: str,
    description: str,
    appearance: str,
    clothing: str,
    group_composition: str = "",
) -> dict[str, Any]:
    visual_identity = {
        "era": "约20万年至5万年前",
        "region": "非洲及欧亚大陆早期人类活动区域",
        "appearance": appearance,
        "clothing": clothing,
        "props": ["石器", "木矛"] if "智人" in canonical_name else ["石器"],
        "body_language": "警觉、协作、在危险环境中求生。",
        "group_composition": group_composition,
    }
    consistency_rules = {
        "must_keep": [
            f"{canonical_name}的核心外观辨识度",
            "远古人类环境适应感",
            "不能像现代城市人",
        ],
        "avoid": [
            "现代衣服",
            "金属盔甲",
            "奇幻风格",
            "欧洲中世纪装扮",
        ],
    }
    return {
        "canonical_name": canonical_name,
        "aliases": aliases,
        "subject_type": subject_type,
        "role_in_script": role_in_script,
        "importance": importance,
        "first_appearance": first_appearance,
        "evidence_text": first_appearance,
        "why_consistency_needed": "该主体会在多个镜头或后续剧本中复用，需要观众一眼识别。",
        "short_description": description,
        "visual_identity": visual_identity,
        "consistency_rules": consistency_rules,
        "negative_rules": consistency_rules["avoid"],
        "extraction_confidence": "high" if importance >= 4 else "medium",
    }


def rejected_from_text(text: str) -> list[dict[str, str]]:
    reasons = {
        "大脑": "这是概念/器官，不是需要跨镜头保持一致的主体。",
        "狮子": "通常只是环境威胁，不是本剧本核心视觉主体。",
        "鬣狗": "通常只是环境威胁，不是本剧本核心视觉主体。",
        "火": "这是技术/道具元素，第一阶段不进入主体池。",
        "贝壳": "这是道具或装饰物，后续可进入道具池。",
        "赭石板": "这是道具或材料，后续可进入道具池。",
        "羚羊": "普通动物不进入主体池，除非成为反复出现的核心角色。",
        "水牛": "普通动物不进入主体池，除非成为反复出现的核心角色。",
        "蛤蟆神": "修辞或概念化对象不进入主体池。",
        "月亮": "这是自然场景元素，不是视觉一致性主体。",
        "船": "这是道具/交通工具，后续可进入道具池。",
        "弓箭": "这是道具/技术元素，后续可进入道具池。",
        "油灯": "这是道具，不进入主体池。",
        "骨针": "这是道具/技术元素，后续可进入道具池。",
        "股票市场": "这是抽象现代概念，不是短剧视觉主体。",
    }
    return [{"name": name, "reason": reason} for name, reason in reasons.items() if name in text]


def build_visual_subject_prompt(payload: dict[str, Any]) -> str:
    return f"""
请从下面的短剧生成结果中提取“视觉主体池”候选。

只提取需要视觉一致性的主体。主体不是所有名词，不是所有画面元素，而是短剧中需要跨镜头、跨场景、甚至跨剧本保持视觉一致，观众一看到就知道这是谁/是什么的人群、角色、族群、物种、核心视觉符号。

优先提取：
1. 反复出现的人群、族群、物种
2. 观众需要一眼认出的角色或群体
3. 后续剧本可能继续复用的视觉主体
4. 与剧情推进高度相关的实体
5. 可用于生成主体锚点图的对象

不要提取：
1. 普通动物，除非是核心角色或反复出现
2. 单次出现的道具
3. 抽象概念
4. 器官、能力、技术概念
5. 场景元素
6. 地点
7. 事件
8. 修辞里的比喻对象
9. 不需要保持一致性的临时画面元素

特别注意：
- “大脑”不是主体，它是概念或器官。
- “狮子、鬣狗”通常不是主体，只是威胁环境。
- “火”不是主体，是道具/技术元素。
- “贝壳、赭石板、骨针、弓箭”不是主体，后续可以进入道具池。
- “智人、尼安德特人、直立人、丹尼索瓦人”是主体，因为它们需要跨镜头保持辨识度。

合并规则：
- 智人、早期智人、现代智人的祖先、现代人类的祖先应合并为 canonical_name = 智人。
- 智人、尼安德特人、直立人、丹尼索瓦人必须保持独立主体。

阶段复用规则：
- 发现同名主体时，先判断它是不是同一个视觉阶段。如果外观、服饰、工具、生活方式、社会形态都一致，说明能复用已有主体阶段。
- 如果同名主体处在不同阶段，不要强行合并，要拆成多个主体阶段，并分别输出。例如智人可拆为“采集狩猎阶段”“农业定居阶段”“现代阶段”。
- visual_phase_label 必须写清楚阶段，优先用生活方式或历史阶段命名，而不是只写“阶段1”。
- 同名主体的不同阶段可以有相同 canonical_name，但必须有不同 visual_phase_label。

输出严格 JSON object：
{{
  "subjects": [
    {{
      "canonical_name": "智人",
      "aliases": ["早期智人", "现代人类的祖先"],
      "subject_type": "species",
      "visual_phase_label": "采集狩猎阶段",
      "role_in_script": "本集核心主角...",
      "importance": 5,
      "first_appearance": "首次出现片段",
      "evidence_text": "依据片段",
      "why_consistency_needed": "为什么需要保持一致",
      "short_description": "简短描述",
      "visual_identity": {{
        "era": "时代",
        "region": "地区",
        "lifestyle_stage": "生活方式阶段",
        "appearance": "外观",
        "clothing": "服饰",
        "props": ["可稳定携带的标志性道具"],
        "body_language": "身体语言",
        "group_composition": "群体构成"
      }},
      "consistency_rules": {{
        "must_keep": ["必须保持的特征"],
        "avoid": ["避免项"]
      }},
      "negative_rules": ["避免项"],
      "extraction_confidence": "high"
    }}
  ],
  "rejected_candidates": [
    {{"name": "大脑", "reason": "这是概念/器官，不是需要跨镜头保持一致的主体。"}}
  ]
}}

短剧标题：{payload.get("title", "")}
主题：{payload.get("topic", "")}
正文：
{payload.get("article", "")}

fact_cards：
{json.dumps(payload.get("fact_cards") or [], ensure_ascii=False)}

causal_chain：
{json.dumps(payload.get("causal_chain") or [], ensure_ascii=False)}

outline：
{json.dumps(payload.get("outline") or [], ensure_ascii=False)}
"""
