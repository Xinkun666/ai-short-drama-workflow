from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from drama_agents.chapter_refiner import parse_json_object
from drama_agents.script_agent import parse_llm_json_object, response_preview
from drama_agents.storage import normalize_visual_scene_payload


class VisualSceneAgent:
    def __init__(self, provider=None):
        self.provider = provider or RuleBasedVisualSceneProvider()

    @classmethod
    def from_environment(cls):
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            return cls(provider=RuleBasedVisualSceneProvider())
        return cls(provider=DeepSeekVisualSceneProvider(api_key=api_key))

    def extract(self, generation: dict[str, Any]) -> dict[str, Any]:
        payload = script_generation_payload(generation)
        extracted = self.provider.extract_scenes(payload)
        return normalize_scene_extraction_payload(extracted)


class RuleBasedVisualSceneProvider:
    def extract_scenes(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = searchable_script_text(payload)
        scenes: list[dict[str, Any]] = []

        def add_if_present(
            canonical_name: str,
            markers: list[str],
            *,
            scene_type: str,
            role_in_script: str,
            importance: int,
            description: str,
            region: str,
            terrain: str,
            weather: str,
            lighting: str,
            palette: str,
            mood: str,
            typical_elements: list[str],
            related_subjects: list[str] | None = None,
            related_props: list[str] | None = None,
            aliases: list[str] | None = None,
        ) -> None:
            if not any(marker in text for marker in markers):
                return
            scenes.append(
                scene_payload(
                    canonical_name=canonical_name,
                    aliases=aliases or [],
                    scene_type=scene_type,
                    role_in_script=role_in_script,
                    importance=importance,
                    first_appearance=first_appearance_for(text, markers),
                    description=description,
                    region=region,
                    terrain=terrain,
                    weather=weather,
                    lighting=lighting,
                    palette=palette,
                    mood=mood,
                    typical_elements=typical_elements,
                    related_subjects=related_subjects or [],
                    related_props=related_props or [],
                )
            )

        add_if_present(
            "东非稀树草原",
            ["非洲东部的稀树草原", "稀树草原", "非洲东部"],
            aliases=["非洲东部稀树草原"],
            scene_type="natural_environment",
            role_in_script="本集开场核心环境，交代智人所处的辽阔高危生态背景。",
            importance=5,
            description="开阔、干热、危机四伏的东非草原环境，需要在多次开场和迁徙镜头中保持一致。",
            region="非洲东部",
            terrain="开阔稀树草原，干燥草地，远处有低矮树木和灌木。",
            weather="干热、尘土感、强烈日光。",
            lighting="白天强烈自然光，空气略带尘土。",
            palette="黄褐色、暗绿色、土色。",
            mood="危险、辽阔、原始、生存压力强。",
            typical_elements=["稀树", "枯草", "低矮灌木", "远处动物剪影"],
            related_subjects=["智人"],
        )
        add_if_present(
            "非洲智人部落营地",
            ["部落", "营地", "整个部落一起拉扯", "非洲部落"],
            scene_type="settlement_camp",
            role_in_script="承载智人群体协作、分工和日常生存压力的主要营地空间。",
            importance=5,
            description="早期智人部落共同生活、制作工具和照看幼年的稳定环境空间。",
            region="非洲东部",
            terrain="半开放营地，周围是草地、简陋遮蔽物和活动痕迹。",
            weather="干燥微尘，白天炎热，夜间温差明显。",
            lighting="白天自然光，夜晚由火光补足。",
            palette="土色、兽皮棕、草黄、烟灰色。",
            mood="协作、紧张、原始但有群体秩序。",
            typical_elements=["简陋遮蔽物", "石器制作区", "食物堆放处", "部落活动痕迹"],
            related_subjects=["智人", "早期智人群体"],
            related_props=["石器", "兽皮"],
        )
        add_if_present(
            "篝火烹饪营地",
            ["篝火", "烹饪", "火", "烤糊的肉", "围坐"],
            scene_type="campfire_site",
            role_in_script="围火讲述和协作烹饪的核心空间，连接故事、食物和群体关系。",
            importance=5,
            description="以篝火为中心的烹饪与讲述区域，不把火本身误当成场景。",
            region="非洲智人营地",
            terrain="营地中心的压实土地，周围有围坐痕迹、灰烬和简陋石块。",
            weather="夜间微凉，烟尘轻微。",
            lighting="暖橙火光照亮人物和近处地面，外围快速变暗。",
            palette="火焰橙、烟灰、深棕、暗土色。",
            mood="亲密、紧张、故事感强，是群体共同想象的起点。",
            typical_elements=["篝火", "灰烬", "围坐痕迹", "烤肉", "石块火圈"],
            related_subjects=["智人", "早期智人群体"],
            related_props=["火", "烤肉"],
        )
        add_if_present(
            "布隆伯斯洞穴",
            ["布隆伯斯洞穴", "贝壳珠子", "赭石板"],
            scene_type="cave_site",
            role_in_script="展示符号、装饰和抽象能力的关键洞穴空间。",
            importance=5,
            description="具有岩壁、洞口光线和符号材料陈列感的洞穴遗址空间。",
            region="南部非洲",
            terrain="岩石洞穴内部，局部有自然光照入，地面粗糙。",
            weather="洞内阴凉干燥，外部海岸气候隐约可见。",
            lighting="洞口冷光与内部暖色辅助光形成对比。",
            palette="岩石灰、赭红、贝壳白、暗褐色。",
            mood="神秘、安静、符号能力初现。",
            typical_elements=["岩壁", "洞口光", "贝壳珠子", "赭石板", "粗糙石面"],
            related_subjects=["智人"],
            related_props=["贝壳珠子", "赭石板"],
        )
        add_if_present(
            "黎凡特冰河期遭遇地带",
            ["黎凡特地区", "地中海东部", "尼安德特人", "冰河期"],
            scene_type="encounter_landscape",
            role_in_script="智人与尼安德特人相遇和对照的冷暖交界地带。",
            importance=5,
            description="位于地中海东部的寒冷遭遇地带，用于承载不同古人类族群的同屏对照。",
            region="黎凡特地区",
            terrain="半干旱山地与开阔谷地交错，植被稀疏。",
            weather="冰河期偏冷、干燥、风大。",
            lighting="冷色日光，空气清透但有寒意。",
            palette="冷灰、土黄、苍绿、石色。",
            mood="陌生、警觉、历史交汇感强。",
            typical_elements=["石灰岩地貌", "稀疏灌木", "寒风", "远处山脊"],
            related_subjects=["智人", "尼安德特人"],
        )
        add_if_present(
            "多巴火山灾变",
            ["多巴火山", "火山灰", "全球气温骤降", "南亚暗无天日"],
            scene_type="disaster_event",
            role_in_script="表现灾变压力和迁徙断点的关键环境事件空间。",
            importance=4,
            description="火山灰笼罩、光线变暗、生态压力陡增的灾变环境。",
            region="南亚及印度洋周边",
            terrain="被火山灰覆盖的荒凉地表，远景低可见度。",
            weather="灰尘弥漫、低温、昏暗。",
            lighting="灰暗漫射光，太阳被火山灰削弱。",
            palette="灰黑、冷蓝灰、枯土色。",
            mood="压迫、灾难、生存危机。",
            typical_elements=["火山灰", "暗沉天空", "枯萎植被", "灰覆盖地面"],
            related_subjects=["智人"],
        )
        add_if_present(
            "红海海口迁徙渡口",
            ["索马里", "红海海口", "阿拉伯半岛", "跨过红海"],
            scene_type="migration_crossing",
            role_in_script="智人离开非洲、跨海进入阿拉伯半岛的关键渡口。",
            importance=5,
            description="狭窄海口、对岸陆地和迁徙群体形成强识别度的跨越空间。",
            region="索马里至阿拉伯半岛之间的红海海口",
            terrain="干旱海岸、浅滩、岩石海角和狭窄海面。",
            weather="干热海风，空气带盐雾。",
            lighting="强烈日光照亮海面，远岸轮廓清晰。",
            palette="沙土黄、海蓝、岩石灰、阳光白。",
            mood="冒险、未知、跨越边界。",
            typical_elements=["狭窄海口", "浅滩", "远岸", "迁徙队伍剪影"],
            related_subjects=["智人"],
            related_props=["简易木筏"],
        )
        add_if_present(
            "印度洋海岸迁徙路线",
            ["印度洋海岸线", "印度河", "恒河", "湄公河", "河口"],
            scene_type="migration_route",
            role_in_script="表现智人沿海岸线扩散和逐段开图的复用迁徙空间。",
            importance=4,
            description="由海岸、河口和湿热陆地连接成的连续迁徙路线。",
            region="印度洋沿岸",
            terrain="海岸线、河口、滩涂和低地植被连续出现。",
            weather="湿热、海风、季风感。",
            lighting="明亮潮湿的自然光，水汽较重。",
            palette="海蓝、湿土棕、热带绿、沙色。",
            mood="漫长、开放、持续迁徙。",
            typical_elements=["海岸线", "河口", "滩涂", "湿地植被"],
            related_subjects=["智人"],
        )
        add_if_present(
            "巽他大陆尽头海峡",
            ["巽他大陆", "100公里宽的汪洋", "开阔大洋", "澳大利亚", "巴布亚新几内亚"],
            scene_type="ocean_edge",
            role_in_script="表现智人面对大洋阻隔和远距离航渡的高难度边界场景。",
            importance=5,
            description="大陆架尽头的开阔海峡，强调海面宽度和未知对岸。",
            region="巽他大陆边缘至澳大利亚方向",
            terrain="大陆尽头、陡变海岸、宽阔汪洋。",
            weather="海风强、天空开阔、浪面明显。",
            lighting="高对比日光或黄昏逆光，突出海面尺度。",
            palette="深海蓝、岩石灰、沙土黄、天光白。",
            mood="壮阔、危险、未知、史诗感。",
            typical_elements=["开阔大洋", "远岸不可见", "海峡", "迁徙队伍"],
            related_subjects=["智人"],
        )
        add_if_present(
            "冰河期欧洲尼安德特人营地",
            ["欧洲的尼安德特人", "零下6°C", "严冬", "寒冷"],
            scene_type="cold_camp",
            role_in_script="用于展示尼安德特人在寒冷欧洲环境中的生存方式。",
            importance=4,
            description="冰河期欧洲寒冷营地，需要和非洲智人营地明显区分。",
            region="冰河期欧洲",
            terrain="寒冷开阔地、岩石遮蔽和低矮植被。",
            weather="严冬、低温、风雪或霜冻。",
            lighting="冷色低角度日光，阴影长。",
            palette="冷灰、雪白、深棕、暗蓝。",
            mood="坚硬、寒冷、耐受、生存压力。",
            typical_elements=["兽皮遮蔽", "寒风", "霜冻地面", "岩石避风处"],
            related_subjects=["尼安德特人"],
        )
        add_if_present(
            "洞穴壁画与葬礼仪式空间",
            ["岩壁", "壁画", "葬礼仪式", "红花", "仪式", "洞穴壁画"],
            scene_type="ritual_space",
            role_in_script="承载共同想象、死亡观念和仪式行为的终章空间。",
            importance=4,
            description="洞穴深处的壁画与葬礼仪式空间，强调符号、火光和集体记忆。",
            region="史前洞穴",
            terrain="洞穴深处岩壁、低矮空间和仪式中心区域。",
            weather="洞内阴冷安静。",
            lighting="火把和微弱自然光共同形成跳动光影。",
            palette="赭红、炭黑、岩灰、火光橙。",
            mood="庄重、神秘、共同想象、纪念感。",
            typical_elements=["岩壁", "壁画", "火把", "红花", "葬礼痕迹"],
            related_subjects=["智人"],
            related_props=["红花", "火把"],
        )

        return {"scenes": scenes, "rejected_candidates": rejected_scene_candidates_from_text(text)}


class DeepSeekVisualSceneProvider:
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
            or os.environ.get("DEEPSEEK_VISUAL_SCENE_MODEL")
            or os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
        )
        self.base_url = base_url
        self.timeout = timeout or int(os.environ.get("DEEPSEEK_TIMEOUT", "240"))

    def extract_scenes(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._complete_json(build_visual_scene_prompt(payload))

    def _complete_json(self, prompt: str) -> dict[str, Any]:
        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是短剧视觉场景解析 Agent，只提取需要跨镜头保持视觉一致性的环境空间。"
                        "只输出合法 JSON，不要 Markdown 代码围栏。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.12,
            "max_tokens": int(os.environ.get("VISUAL_SCENE_MAX_TOKENS", "5000")),
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
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = str(message.get("content") or "")
        try:
            return parse_llm_json_object(content)
        except json.JSONDecodeError as exc:
            detail = response_preview(
                content,
                finish_reason=choice.get("finish_reason"),
                model=data.get("model"),
            )
            reasoning = str(message.get("reasoning_content") or "")
            if reasoning and not content.strip():
                detail = f"{detail}；reasoning_chars={len(reasoning)}"
            raise RuntimeError(f"DeepSeek 场景解析没有返回可用 JSON：{exc}；{detail}") from exc


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


def normalize_scene_extraction_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_scenes = payload.get("scenes") if isinstance(payload, dict) else []
    raw_rejected = payload.get("rejected_candidates") if isinstance(payload, dict) else []
    scenes = []
    seen: set[str] = set()
    for item in raw_scenes or []:
        if not isinstance(item, dict):
            continue
        normalized = normalize_visual_scene_payload(item)
        identity_key = f"{normalized['canonical_name']}::{normalized['visual_phase_key']}"
        if not normalized["canonical_name"] or identity_key in seen:
            continue
        seen.add(identity_key)
        scenes.append(normalized)
    rejected_candidates = []
    for candidate in raw_rejected or []:
        if not isinstance(candidate, dict):
            continue
        name = str(candidate.get("name") or "").strip()
        reason = str(candidate.get("reason") or "").strip()
        if name and reason:
            rejected_candidates.append({"name": name, "reason": reason})
    return {"scenes": scenes, "rejected_candidates": rejected_candidates}


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


def scene_payload(
    *,
    canonical_name: str,
    aliases: list[str],
    scene_type: str,
    role_in_script: str,
    importance: int,
    first_appearance: str,
    description: str,
    region: str,
    terrain: str,
    weather: str,
    lighting: str,
    palette: str,
    mood: str,
    typical_elements: list[str],
    related_subjects: list[str],
    related_props: list[str],
) -> dict[str, Any]:
    visual_identity = {
        "era": "约7万年前至5万年前",
        "region": region,
        "terrain": terrain,
        "weather": weather,
        "lighting": lighting,
        "palette": palette,
        "mood": mood,
        "typical_elements": typical_elements,
    }
    consistency_rules = {
        "must_keep": [
            f"{canonical_name}的空间结构和环境识别度",
            "历史科普卡通短剧的半扁平环境风格",
            "不能像现代城市、现代室内或纯道具特写",
        ],
        "avoid": [
            "现代建筑",
            "现代交通工具",
            "现代城市灯光",
            "科幻界面",
            "只画单个道具",
            "把主体人物当作场景",
        ],
    }
    return {
        "canonical_name": canonical_name,
        "aliases": aliases,
        "scene_type": scene_type,
        "role_in_script": role_in_script,
        "importance": importance,
        "first_appearance": first_appearance,
        "evidence_text": first_appearance,
        "why_consistency_needed": "该环境空间会承载多个镜头或后续剧本复用，需要观众一眼识别。",
        "short_description": description,
        "visual_identity": visual_identity,
        "consistency_rules": consistency_rules,
        "negative_rules": consistency_rules["avoid"],
        "related_subjects": related_subjects,
        "related_props": related_props,
        "extraction_confidence": "high" if importance >= 5 else "medium",
    }


def rejected_scene_candidates_from_text(text: str) -> list[dict[str, str]]:
    reasons = {
        "大脑": "大脑是器官/概念，不是需要视觉一致性管理的环境空间。",
        "火": "火是道具/技术线索，不是场景；应归入篝火烹饪营地这样的空间。",
        "贝壳": "贝壳是道具或装饰物，不是场景；可作为布隆伯斯洞穴的环境元素。",
        "赭石板": "赭石板是道具或材料，不是场景；可作为布隆伯斯洞穴的环境元素。",
        "弓箭": "弓箭是道具/技术元素，不是环境空间。",
        "骨针": "骨针是道具/技术元素，不是环境空间。",
        "船": "船或木筏是交通道具，不是场景；应提取渡口或海峡空间。",
        "木筏": "木筏是交通道具，不是场景；应提取渡口或海峡空间。",
        "狮子": "狮子是普通动物或威胁元素，不是需要保持一致的场景。",
        "鬣狗": "鬣狗是普通动物或威胁元素，不是需要保持一致的场景。",
        "蛤蟆神": "蛤蟆神属于修辞或概念化对象，不是古史环境空间。",
        "月亮": "月亮是自然画面元素，不是可承载多镜头的环境空间。",
        "股票市场": "股票市场是现代比喻，不应进入古史场景池。",
        "CPU": "CPU 是现代比喻，不应进入古史场景池。",
        "汽车油箱": "汽车油箱是现代比喻，不应进入古史场景池。",
        "智人": "智人是主体池对象，不是场景池对象。",
        "尼安德特人": "尼安德特人是主体池对象，不是场景池对象。",
    }
    return [{"name": name, "reason": reason} for name, reason in reasons.items() if name in text]


def build_visual_scene_prompt(payload: dict[str, Any]) -> str:
    return f"""
请从下面的短剧正文中做“轻量场景解析”，提取“视觉场景池”候选。

你不是抽取所有地点名。你是在抽取需要视觉一致性管理的场景：短剧中需要跨镜头、跨分镜、跨剧本保持视觉一致的环境空间。观众一看到这个空间，就应该知道这是哪个历史阶段、地点或情境。

重要边界：
- 现在只做文本结构化解析，不生成场景图，不生成完整绘图提示词。
- 后续用户点击某个场景时，系统会再基于单个场景生成场景图提示词。
- 本轮输出必须短，避免长篇扩写。

应该提取：
1. 有明确空间环境。
2. 能承载多个镜头。
3. 对短剧视觉风格有决定作用。
4. 需要后续生成场景锚点图。
5. 可能被分镜、图片、视频多次调用。
6. 观众看到后能识别这是哪个历史阶段/环境。

不要提取：
1. 道具。
2. 普通动物。
3. 抽象概念。
4. 修辞比喻。
5. 单句过场元素。
6. 现代类比画面。
7. 不需要保持一致性的临时画面。
8. 主体池对象，比如智人、尼安德特人。
9. 道具池对象，比如火、骨针、弓箭、船。

阶段复用规则：
- 发现同名场景时，先判断它是不是同一个视觉阶段。如果地貌、建筑/营地形态、技术痕迹、生活方式、典型元素都一致，说明能复用已有场景阶段。
- 如果同名场景处在不同阶段，不要强行合并，要拆成多个场景阶段。例如同一片东非草原可以拆为“采集狩猎迁徙阶段”“农业定居村落阶段”“现代城市阶段”。
- visual_phase_label 必须写清楚阶段，优先用环境用途、文明阶段或生活方式命名，而不是只写“阶段1”。
- 同名场景的不同阶段可以有相同 canonical_name，但必须有不同 visual_phase_label。

输出预算：
- 最多输出 6 个核心场景；宁可少而准，不要列全过场地点。
- 每个字符串尽量控制在 30 个汉字以内；first_appearance/evidence_text 最多 80 个汉字。
- aliases、typical_elements、must_keep、avoid、negative_rules、related_subjects、related_props 每组最多 3 项。
- rejected_candidates 最多 8 个，只写最容易误抽取的主体、道具、动物、概念或现代比喻。
- visual_identity 只写后续生成场景图时必需的环境锚点关键词。

输出严格 JSON object：
{{
  "scenes": [
    {{
      "canonical_name": "东非稀树草原",
      "aliases": ["非洲东部稀树草原"],
      "scene_type": "natural_environment",
      "visual_phase_label": "采集狩猎迁徙阶段",
      "role_in_script": "本集开场核心场景...",
      "importance": 5,
      "first_appearance": "首次出现片段",
      "evidence_text": "依据片段",
      "why_consistency_needed": "为什么需要保持一致",
      "short_description": "简短描述",
      "visual_identity": {{
        "era": "时代",
        "environment_stage": "环境/文明阶段",
        "region": "地区",
        "terrain": "地形",
        "weather": "天气",
        "lighting": "光线",
        "palette": "色彩",
        "mood": "氛围",
        "typical_elements": ["典型环境元素"]
      }},
      "consistency_rules": {{
        "must_keep": ["必须保持的视觉特征"],
        "avoid": ["避免项"]
      }},
      "negative_rules": ["避免项"],
      "related_subjects": ["相关主体"],
      "related_props": ["相关道具"],
      "extraction_confidence": "high"
    }}
  ],
  "rejected_candidates": [
    {{"name": "火", "reason": "火是道具/技术元素，不是场景；应提取为篝火烹饪营地。"}}
  ]
}}

短剧标题：{payload.get("title", "")}
主题：{payload.get("topic", "")}
正文：
{payload.get("article", "")}
"""


def build_scene_anchor_prompt(scene: dict[str, Any]) -> str:
    identity = scene.get("visual_identity") if isinstance(scene.get("visual_identity"), dict) else {}
    rules = scene.get("consistency_rules") if isinstance(scene.get("consistency_rules"), dict) else {}
    typical_elements = identity.get("typical_elements") if isinstance(identity.get("typical_elements"), list) else []
    must_keep = rules.get("must_keep") if isinstance(rules.get("must_keep"), list) else []
    return f"""
请生成一张“场景锚点图”，用于 AI 历史科普短剧后续分镜保持环境空间视觉一致。

历史科普卡通短剧视觉风格：
- 画面是半扁平卡通环境设定图，不是写实电影截图，也不是幼儿动画。
- 只生成环境空间：地形、天气、光线、色彩、氛围和典型环境元素是主角。
- 可以有极小的人物剪影帮助尺度判断，但不要主体人物大特写，不要群像叙事。
- 不要把单个道具、动物、现代比喻或信息图版式当成场景。
- 构图干净，适合作为后续分镜、关键帧和视频生成的环境参考。

场景内容：
- 名称：{scene.get("canonical_name", "")}
- 类型：{scene.get("scene_type", "")}
- 简述：{scene.get("short_description", "")}
- 时代：{identity.get("era", "")}
- 地区：{identity.get("region", "")}
- 地形：{identity.get("terrain", "")}
- 天气：{identity.get("weather", "")}
- 光线：{identity.get("lighting", "")}
- 色彩：{identity.get("palette", "")}
- 氛围：{identity.get("mood", "")}
- 典型元素：{"、".join(str(item) for item in typical_elements)}
- 必须保持：{"；".join(str(item) for item in must_keep)}

生成要求：
- 只生成环境空间，不要把智人、尼安德特人或其他主体放到画面中心。
- 画面重点是稳定可复用的空间锚点：地貌结构、天气光线、色彩氛围和环境元素。
- 可用于 9:16 短视频分镜，构图要有明确前景、中景和远景层次。
- 不要生成地图、标题文字、知识卡片、UI 面板或说明性箭头。
- 不要生成单个道具特写，例如火、贝壳、弓箭、骨针、船。
""".strip()


def build_scene_anchor_negative_prompt(scene: dict[str, Any]) -> str:
    rules = scene.get("consistency_rules") if isinstance(scene.get("consistency_rules"), dict) else {}
    avoid = rules.get("avoid") if isinstance(rules.get("avoid"), list) else []
    negative_rules = scene.get("negative_rules") if isinstance(scene.get("negative_rules"), list) else []
    items = [
        "不要主体人物大特写",
        "不要人物群像占据画面中心",
        "不要单个道具特写",
        "不要只画火",
        "不要只画贝壳",
        "不要只画弓箭",
        "不要现代城市",
        "不要现代建筑",
        "不要现代交通工具",
        "不要科幻界面",
        "不要股票市场",
        "不要 CPU",
        "不要汽车油箱",
        "不要信息图文字",
        "不要标题排版",
        "不要地图 UI",
        "不要写实电影截图",
        "不要太幼稚",
    ]
    items.extend(str(item) for item in avoid)
    items.extend(str(item) for item in negative_rules)
    deduped = []
    seen = set()
    for item in items:
        clean = item.strip()
        if clean and clean not in seen:
            deduped.append(clean)
            seen.add(clean)
    return "，".join(deduped)
