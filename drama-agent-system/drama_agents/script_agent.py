from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from drama_agents.chapter_refiner import parse_json_object
from drama_agents.storage import MaterialDatabase


class ScriptAgent:
    def __init__(self, provider=None):
        self.provider = provider

    @classmethod
    def from_environment(cls):
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            return cls(provider=None)
        return cls(provider=DeepSeekScriptProvider(api_key=api_key))

    def generate(
        self,
        *,
        topic: str,
        time_range_text: str,
        time_start_year: int | None = None,
        time_end_year: int | None = None,
        record_ids: list[str],
        database: MaterialDatabase,
        output_dir: Path | str,
    ) -> dict[str, Any]:
        clean_topic = topic.strip()
        clean_time_range = time_range_text.strip()
        clean_record_ids = [record_id for record_id in record_ids if str(record_id).strip()]
        if not clean_topic:
            raise ValueError("短剧主题不能为空")
        if not clean_record_ids:
            raise ValueError("请至少选择一本书的时间线")
        if not self.provider:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY，无法生成剧本。")

        if time_start_year is not None or time_end_year is not None:
            if time_start_year is None or time_end_year is None:
                raise ValueError("开始年份和结束年份必须同时填写")
            start_year, end_year = normalize_year_range(int(time_start_year), int(time_end_year))
            clean_time_range = clean_time_range or format_time_range(start_year, end_year)
        else:
            if not clean_time_range:
                raise ValueError("时间范围不能为空")
            start_year, end_year = parse_time_range(clean_time_range)
        matched_events = collect_matching_events(database, clean_record_ids, start_year, end_year)
        if not matched_events:
            raise ValueError("所选时间线里没有匹配这个时间范围的事件模块")

        provider_payload = {
            "topic": clean_topic,
            "time_range": clean_time_range,
            "time_start_year": start_year,
            "time_end_year": end_year,
            "events": matched_events,
        }
        generated = normalize_script_payload(self.provider.generate_script(provider_payload), clean_topic)
        review_history = []
        first_review = normalize_review_payload(self.provider.review_script(provider_payload, generated))
        review_history.append(first_review)
        revision_count = 0
        if review_requires_revision(first_review):
            generated = normalize_script_payload(self.provider.revise_script(provider_payload, generated, first_review), clean_topic)
            revision_count = 1
            review_history.append(normalize_review_payload(self.provider.review_script(provider_payload, generated)))
        final_review = review_history[-1]
        script_dir = unique_script_dir(Path(output_dir), clean_topic)
        script_dir.mkdir(parents=True, exist_ok=True)

        result = {
            "generation_id": script_dir.name,
            "status": "completed",
            "message": "剧本生成完成，已根据审查意见返修。" if revision_count else "剧本生成完成，审查通过。",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "topic": clean_topic,
            "time_range": clean_time_range,
            "time_start_year": start_year,
            "time_end_year": end_year,
            "selected_record_ids": clean_record_ids,
            "matched_event_count": len(matched_events),
            "matched_events": matched_events,
            "script": {
                "title": generated["title"],
                "logline": generated["logline"],
                "fact_cards": generated["fact_cards"],
                "causal_chain": generated["causal_chain"],
                "outline": generated["outline"],
                "article": generated["article"],
                "fact_boundaries": generated["fact_boundaries"],
                "scenes": generated["script"],
            },
            "subjects": [],
            "map_shots": [],
            "script_review": final_review,
            "review_history": review_history,
            "revision_count": revision_count,
        }
        json_path = script_dir / "script_result.json"
        markdown_path = script_dir / "script_result.md"
        result["json_path"] = str(json_path)
        result["markdown_path"] = str(markdown_path)
        json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(render_script_markdown(result), encoding="utf-8")
        database.save_script_generation(result)
        return result

    def assist_edit(
        self,
        *,
        generation: dict[str, Any],
        selection: str,
        instruction: str,
        contexts: list[dict[str, Any]],
        conversation: list[dict[str, Any]] | None = None,
        pending_edit: dict[str, Any] | None = None,
        intent: str = "PROPOSE_EDIT",
        memory: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_selection = selection.strip()
        clean_instruction = instruction.strip()
        if not clean_instruction:
            raise ValueError("请输入你想让剧本对话助手帮你做什么")
        if not self.provider:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY，无法调用剧本对话助手。")
        if not hasattr(self.provider, "edit_selection"):
            raise RuntimeError("当前剧本 provider 不支持对话式阅读和局部修改。")
        payload = {
            "topic": generation.get("topic", ""),
            "time_range": generation.get("time_range", ""),
            "script_title": (generation.get("script") or {}).get("title", ""),
            "script": generation.get("script") or {},
            "selection": clean_selection,
            "instruction": clean_instruction,
            "contexts": contexts,
            "conversation": conversation or [],
            "pending_edit": pending_edit or {},
            "intent": intent,
            "memory": memory or {},
        }
        return normalize_edit_payload(self.provider.edit_selection(payload))


class DeepSeekScriptProvider:
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
            or os.environ.get("DEEPSEEK_SCRIPT_MODEL")
            or os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
        )
        self.base_url = base_url
        self.timeout = timeout or int(os.environ.get("DEEPSEEK_TIMEOUT", "240"))

    def generate_script(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._complete_json(
            system=(
                "你是短剧编剧 Agent，擅长把严肃历史材料改写成完整、准确、幽默的中文讲述型短剧。"
                "你首先是讲故事的人，其次才是写段子的人。只输出合法 JSON。"
            ),
            prompt=build_script_prompt(payload),
            temperature=0.75,
            max_tokens=int(os.environ.get("SCRIPT_AGENT_MAX_TOKENS", "14000")),
        )

    def review_script(self, source_payload: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
        return self._complete_json(
            system=(
                "你是短剧审查 Agent，只负责严厉审查剧本是否完整、贴合主题、连贯、充分使用原文材料。"
                "不要因为字数够就放行。只输出合法 JSON。"
            ),
            prompt=build_review_prompt(source_payload, draft),
            temperature=0.1,
            max_tokens=int(os.environ.get("SCRIPT_REVIEW_MAX_TOKENS", "3200")),
        )

    def revise_script(self, source_payload: dict[str, Any], draft: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
        return self._complete_json(
            system=(
                "你是短剧返修 Agent。你必须基于审查意见重写或大幅修补剧本，解决完整性、主题重心、连贯性和材料利用问题。"
                "只输出合法 JSON。"
            ),
            prompt=build_revision_prompt(source_payload, draft, review),
            temperature=0.65,
            max_tokens=int(os.environ.get("SCRIPT_AGENT_MAX_TOKENS", "14000")),
        )

    def edit_selection(self, payload: dict[str, Any]) -> dict[str, Any]:
        intent = str(payload.get("intent") or "")
        return self._complete_json(
            system=(
                "你是剧本阅读、评审、修改对话助手，使用 DeepSeek V4 Pro 风格能力工作。"
                "后端已经完成意图识别，你只能执行指定任务，不能自行决定保存正文。"
                "只输出合法 JSON。"
            ),
            prompt=build_assistant_prompt(payload, intent=intent),
            temperature=0.45,
            max_tokens=int(os.environ.get("SCRIPT_EDIT_MAX_TOKENS", "3600")),
        )

    def _complete_json(self, *, system: str, prompt: str, temperature: float, max_tokens: int) -> dict[str, Any]:
        try:
            return self._complete_json_once(
                system=system,
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except json.JSONDecodeError as exc:
            repair_prompt = (
                f"{prompt}\n\n"
                "重要：上一次输出不是合法 JSON，解析失败。\n"
                f"错误信息：{exc}\n"
                "请重新输出严格合法的 JSON object，不要 Markdown 代码围栏，不要解释文字。\n"
                "所有字符串必须正确转义；完整长文必须放在 article 字符串中；"
                "如果需要段落换行，请在 JSON 字符串里使用 \\n\\n，不要输出未转义的裸换行；"
                "不要尾随逗号，不要截断字符串，不要省略闭合引号或闭合大括号。"
            )
            try:
                return self._complete_json_once(
                    system=system,
                    prompt=repair_prompt,
                    temperature=min(temperature, 0.2),
                    max_tokens=max_tokens,
                )
            except json.JSONDecodeError as repair_exc:
                detail = getattr(self, "_last_parse_error_detail", "")
                suffix = f"；返回预览：{detail}" if detail else ""
                raise RuntimeError(f"DeepSeek 返回的 JSON 仍无法解析：{repair_exc}{suffix}") from repair_exc

    def _complete_json_once(self, *, system: str, prompt: str, temperature: float, max_tokens: int) -> dict[str, Any]:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
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
        self._last_parse_error_detail = ""
        try:
            return parse_llm_json_object(content)
        except json.JSONDecodeError:
            self._last_parse_error_detail = response_preview(
                content,
                finish_reason=choice.get("finish_reason"),
                model=data.get("model"),
            )
            raise


def collect_matching_events(
    database: MaterialDatabase,
    record_ids: list[str],
    start_year: int | None,
    end_year: int | None,
) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for record_id in record_ids:
        record = database.find_record(record_id)
        if not record or record.get("timeline_status") != "completed":
            continue
        for event in database.list_timeline_events(record_id):
            if years_overlap(start_year, end_year, event.get("time_start_year"), event.get("time_end_year")):
                event = dict(event)
                event["book_name"] = record.get("book_name", record_id)
                event["timeline_url"] = record.get("timeline_url", "")
                matched.append(event)
    matched.sort(key=lambda item: (item.get("time_start_year") is None, item.get("time_start_year") or 0, item["event_id"]))
    return matched


def years_overlap(
    query_start: int | None,
    query_end: int | None,
    event_start: int | float | None,
    event_end: int | float | None,
) -> bool:
    if query_start is None or query_end is None:
        return True
    if event_start is None and event_end is None:
        return False
    event_start = int(event_start if event_start is not None else event_end)
    event_end = int(event_end if event_end is not None else event_start)
    start = min(event_start, event_end)
    end = max(event_start, event_end)
    return start <= query_end and query_start <= end


def parse_time_range(value: str) -> tuple[int | None, int | None]:
    parts = re.split(r"\s*[—–-]\s*|\s*到\s*|\s*至\s*", value.strip(), maxsplit=1)
    years = [parse_year_label(part) for part in parts if part.strip()]
    if len(years) == 1:
        years.append(years[0])
    if not years:
        return (None, None)
    start, end = years[0], years[-1]
    if start is not None and end is not None and start > end:
        start, end = end, start
    return start, end


def normalize_year_range(start_year: int, end_year: int) -> tuple[int, int]:
    if start_year > end_year:
        return end_year, start_year
    return start_year, end_year


def format_time_range(start_year: int | None, end_year: int | None) -> str:
    if start_year is None and end_year is None:
        return ""
    if end_year is None:
        end_year = start_year
    if start_year is None:
        start_year = end_year
    start_year, end_year = normalize_year_range(int(start_year), int(end_year))
    return f"{format_year_label(start_year)} — {format_year_label(end_year)}"


def format_year_label(year: int | float) -> str:
    year = int(year)
    if year < 0:
        absolute_year = abs(year)
        if absolute_year >= 10000:
            wan_value = absolute_year / 10000
            wan_label = f"{wan_value:g}"
            return f"{wan_label}万年前"
        return f"公元前 {absolute_year} 年"
    if year > 0:
        return f"公元 {year} 年"
    return "公元 0 年"


def parse_year_label(value: str) -> int | None:
    text = value.replace(",", "").replace("，", "").strip()
    if "至今" in text or text in {"现在", "当代"}:
        return datetime.now().year
    multiplier = 10000 if "万" in text else 1
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    year = int(float(match.group(1)) * multiplier)
    if "年前" in text or "公元前" in text:
        return -year
    return year


def response_preview(content: str, *, finish_reason: Any = None, model: Any = None, limit: int = 500) -> str:
    compact = re.sub(r"\s+", " ", content).strip()
    if len(compact) > limit:
        compact = f"{compact[:limit]}..."
    if not compact:
        compact = "<空内容>"
    parts = [f"finish_reason={finish_reason}", f"model={model}", f"content={compact}"]
    return "；".join(parts)


def parse_llm_json_object(content: str) -> dict[str, Any]:
    try:
        return parse_json_object(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(content[start : end + 1])


def build_script_prompt(payload: dict[str, Any]) -> str:
    events_text = json.dumps(payload.get("events", []), ensure_ascii=False, indent=2)
    return f"""
请基于下面的时间线材料，生成一个“严肃史料短剧化改编”结果。

你的身份：
你不是普通写作助手，也不是历史问答助手。你是“严肃史料短剧化改编总编剧 Agent”。
你的任务是把权威历史材料、学术著作摘录、考古资料、人物事件资料，转化为普通人愿意看的历史科普短剧文章/旁白稿。

核心判断：
- 这不是摘要，不是提纲，不是几句俏皮话。
- 观众看完后，必须清楚知道这个主题到底讲了什么。
- 不要划分场景，不要输出分镜脚本，不要把内容拆成场景 1、场景 2。请写成一整篇顺口、连贯、可直接朗读的文章式讲述稿。
- 幽默只是表达方式，不是目的；不能为了搞笑牺牲事实链条、背景铺垫和历史过程。
- 梗要服务于理解，比如把复杂历史过程讲得轻松，但不能让内容变空。
- 下面每个时间线模块里的 content 都是从原文章节中整理出来的“原文压缩材料”，不是可有可无的摘要。写剧本时必须优先消化这些 content，把里面的背景、过程、原因、影响、演化和细节尽量复述进旁白。
- 不允许只拿 title/time/place 生成几句概括；每个被采用的时间线模块，都要在剧本中留下可识别的细节痕迹。
- 先判断“主题真正要回答的问题”是什么，再安排文章结构。不要把前史铺垫写成主菜，也不要让核心主题龙头蛇尾。
- 你不是在写“历史总结”。你是在做：严肃史料 → 因果重构 → 场景扩写 → 短剧化表达 → 事实边界控制。

主题重心规则：
- 剧本不是按时间线平均分配篇幅，而是按主题重要性分配篇幅。
- 背景铺垫只能服务主题，通常不超过全片 25%-30%；主题核心必须占全片 50%-60% 以上。
- 如果主题是“地球上出现了一种会讲故事的动物”，核心不是石器、火、直立行走或古人类谱系；这些只能作为“为什么故事能力重要”的前置条件。核心必须落在语言、象征、虚构故事、集体想象、社群合作、知识传播和由此带来的扩张能力上。
- 如果主题里出现“故事、语言、认知革命、想象、信仰、合作”等关键词，必须用连续多个自然段展开其诞生过程、机制变化和后果，不能只用一段概括。
- 每个段落都要有明确功能：铺垫困境、引出问题、解释核心机制、展示过程变化、说明结果影响、回扣主题。删除那些虽然有趣但不推动主题的问题。
- 虽然不划分场景，但文章内部仍要有清晰段落推进：开场钩子、背景铺垫、核心过程、影响结果、主题回扣。

叙事结构必须包含：
1. 开场钩子：用历史纵深和反差把观众拉进来。可以使用类似表达：“早在多少年以前，非洲某个地方生活着一群人类，我们称之为智人。你们万万没想到，这群人的能力边界有一天上能抵达月球，下能到达深海，还会遍布地球各处。那他们是怎么做到的？”
2. 背景铺垫：说明当时的人类处在什么环境里、有什么限制、面对什么生存压力。比如：早期智人在食物链的地位并不是一开始就处于顶端。
3. 过程展开：按照时间线材料，逐个回答“这件事为什么会发生、发生前有什么条件、过程中有哪些关键变化、参与者如何行动、结果如何、又如何引出下一步”。
4. 情绪推进：可以有吐槽、比喻、反差、轻梗，但每一个梗都要帮助观众理解历史，不要空口搞笑。
5. 结尾回扣：回到主题，告诉观众这个节点为什么是人类文明故事里的关键一步。

固定工作流程：
Step 1：先提取史实卡片。每张卡片要包含：编号、史实、时间、地点、来源依据、置信度、可短剧化方向、不能乱写的地方。
Step 2：重建因果链。必须用 A 发生了什么 → 为什么导致 B → B 解决什么问题 → B 带来什么代价 → 代价如何推动 C 的方式组织。
Step 3：设计段落大纲。按 6-10 个段落拆分，每段都要说明段落标题、核心知识点、开场画面、人群动作、冲突/问题、选择/变化、代价/后果、与下一段衔接。
Step 4：写完整短剧旁白稿。适合 10-15 分钟视频，中文 3000-5000 字，有开场钩子、清晰主线、段落节奏、画面感、因果解释、适度幽默、结尾升华和下一集钩子。
Step 5：输出事实边界提醒。区分原始材料明确支持、合理场景化改写、需要人工核对、可能过度夸张、建议补充资料。

每个知识点必须有“戏”：
- 当时的场景是什么？
- 人群遇到了什么问题？
- 他们做出了什么选择？
- 这个选择带来了什么好处？
- 这个好处背后有什么代价？
- 这个代价又如何推动下一件事发生？
如果一个知识点无法写出场景、问题、选择、代价和后果，不要把它作为主段落，只能作为补充信息。

材料使用规则：
- 先读每条 event.content，再决定文章结构；event.content 里的信息密度决定讲述细节密度。
- event.evidence_note 用来判断哪些内容是原文明说、哪些是章节上下文归纳；原文明说的内容可以写得更确定，归纳内容要谨慎表述。
- event.drama_potential 用来决定戏剧功能，但不能替代 content。
- source_pages 可以在内部帮助你理解材料出处，不需要在台词里生硬报页码。
- 如果多个时间线模块讲的是同一历史过程，要合并成连续叙事，不要机械罗列；但必须保留每个模块提供的关键细节。
- 必须先从时间线材料中选出 4-7 个“关键节点”，例如工具、火、直立行走、语言、象征物、虚构故事、群体合作、迁徙扩张。每个关键节点都不能只写一句话。
- 每个关键节点至少写 2 个自然段；主题核心节点至少写 3-5 个自然段。每个节点都要展开：出现前的处境、为什么需要它、它具体怎样发生或表现、它改变了什么、它如何推到下一个节点。
- 如果 event.content 里出现数字、例子、对比、具体物件、具体行为或时间范围，要尽量写进文章。不要把“火降低消化成本”“婴儿长期抚养促进社会关系”“虚构故事扩大合作”这类细节压成一句概括。
- 对关键节点的写法要像“把一个小故事讲完整”：先给观众一个可想象的场面，再解释背后的机制，最后落到影响。不能只列观点。
- 遇到“语言”“认知革命”“农业”“国家”“贸易网络”“帝国”等大概念时，不能把概念当答案。必须用原文 content 解释它的发生条件、形成过程、实际变化和后果。
- 任何事的发生都要尽量写出前因、经过和后果；不是只有“失败—能力变化—成功”这种情节才需要因果链。
- 不要用一句“于是某某革命发生了”跳过过程；要让观众知道“为什么它会发生、它具体改变了什么、为什么这会让后面的事成为可能”。
- 写到核心概念时必须形成“问题链”：为什么会出现这个变化？它诞生前已有了哪些条件？它在过程中具体改变了哪些能力？它马上造成了什么结果？它如何影响后续历史？

长度要求：
- 完整剧本 article 建议 3000-5000 个中文字符；如果材料很少，也要尽量把已给材料讲完整。
- 写成自然段，每段之间要顺滑衔接。
- 每个自然段尽量只完成一个叙事任务，不要把多个关键节点压进同一段。
- 不要输出 dialogue，不要输出场景数组；完整讲述功能全部由 article 承担。
- 禁止只写“一句话摘要式旁白”，禁止只给概念列表。

硬性要求：
1. 只能使用给定时间线材料里的事实，不要补百科、不要硬编历史细节。
2. 如果材料里没有精确地点，只能写成“非洲某些地区”“相关区域”等谨慎表达，不要编具体遗址。
3. 风格要像轻量地图动画短剧：画面可以简单，但语言要幽默、风趣、有梗，能带动观众情绪。
4. 剧本必须讲清楚：谁、什么时候、在哪里、发生了什么、为什么重要。
5. 口语可以好笑，但事实不能乱说；不确定的地方用旁白规避，不要装确定。
6. 本 Agent 只负责生成剧本文稿、史实卡片、因果链、大纲和事实边界；不要生成非剧本文稿的视觉资产或镜头资产。

输出严格 JSON，字段必须是：
{{
  "title": "短剧标题",
  "logline": "一句话说明这一集讲什么",
  "fact_cards": [
    {{
      "id": "F1",
      "fact": "史实",
      "time": "时间",
      "place": "地点",
      "source_basis": "来源依据，说明来自哪条 event.content/evidence_note/source_pages",
      "confidence": "高|中|低",
      "drama_direction": "可短剧化方向",
      "do_not_overstate": "不能乱写或不能写死的地方"
    }}
  ],
  "causal_chain": [
    "A 发生了什么 → 为什么导致 B → B 解决了什么问题 → B 带来了什么代价 → 代价如何推动 C"
  ],
  "outline": [
    {{
      "title": "段落标题",
      "core_point": "这一段的核心知识点",
      "opening_image": "开场画面",
      "human_action": "人群动作",
      "conflict": "冲突/问题",
      "change": "选择/变化",
      "cost": "代价/后果",
      "transition": "与下一段的衔接"
    }}
  ],
  "article": "完整短剧旁白稿。不要划分场景。要求读起来顺口，故事前因后果清楚，必须基于 event.content 缝合时间范围内的模块和原文压缩内容，不省略关键细节。",
  "fact_boundaries": {{
    "explicitly_supported": ["原始材料明确支持的内容"],
    "dramatized_inference": ["合理场景化改写"],
    "needs_manual_check": ["需要人工核对的内容"],
    "possible_overstatement": ["可能过度夸张的表达"],
    "suggested_sources": ["建议补充权威资料的地方"]
  }}
}}

主题：{payload.get("topic", "")}
时间范围：{payload.get("time_range", "")}

可用时间线材料：
{events_text}
""".strip()


def build_review_prompt(source_payload: dict[str, Any], draft: dict[str, Any]) -> str:
    events_text = json.dumps(source_payload.get("events", []), ensure_ascii=False, indent=2)
    draft_text = json.dumps(draft, ensure_ascii=False, indent=2)
    return f"""
请审查下面这版短剧稿。你不是润色员，而是剧本审查人员。

审查目标：
1. 结构完整性：是否包含 fact_cards、causal_chain、outline、article、fact_boundaries 五部分。
2. 故事完整性：是否把主题相关事件的前因、过程、细节、结果和影响讲清楚。
3. 主题贴合度：是否真正围绕主题展开，还是把大量篇幅浪费在前史或旁支材料上。
4. 内容连贯性：段落之间是否有自然过渡，是否存在“突然就发生了”“一句话跳过核心机制”的问题。
5. 材料利用度：是否充分使用 event.content 里的原文压缩材料，而不是只看 title/time/place 写摘要。
6. 关键节点丰富度：每个关键节点是否至少展开出现前处境、发生过程、具体表现、影响和下一步连接。
7. 场景化程度：每个主段落是否有画面、问题、人群动作、选择、代价、后果。
8. 事实边界：是否清楚区分材料明确支持、合理场景化、需要人工核对、可能夸张和建议补充资料。
9. 丰富度：是否只是字数够，但内容简陋、概念化、空泛、缺少具体过程。
10. 事实谨慎：有没有引入材料之外的外部知识、编造具体地点、编造因果。

硬性判定：
- 不要因为字数达标就判定通过。
- 如果缺少史实卡片、因果链、段落大纲、完整剧本、事实边界任意一部分，必须判定不通过。
- 如果主题核心只用一小段带过，必须判定不通过。
- 如果任何关键节点只用一句话或一小段带过，且 event.content 明明有更多细节，必须判定不通过。
- 如果文章只是从一个概念跳到下一个概念，没有展开具体例子、机制和影响，必须判定不通过。
- 如果段落大纲没有写清画面、问题、动作、选择、代价和衔接，必须判定不通过。
- 如果事实边界没有提醒争议、推演或需要核对点，必须判定不通过。
- 如果出现“认知革命来了，所以智人征服世界”这类跳跃表达，必须判定不通过。
- 如果背景铺垫明显压过主题核心，必须判定不通过。
- 如果 event.content 中的关键细节没有进入旁白，必须指出缺失。

输出严格 JSON，字段必须是：
{{
  "passed": true 或 false,
  "score": 1-5,
  "verdict": "总体审查结论",
  "theme_alignment": "主题贴合度评价",
  "story_completeness": "故事完整性评价",
  "continuity": "连贯性评价",
  "material_usage": "原文材料利用评价",
  "key_node_depth": "关键节点是否展开充分",
  "simplicity_risk": "是否简陋、空泛、概念化",
  "missing_content": ["缺少哪些内容或机制"],
  "issues": [
    {{
      "severity": "critical|major|minor",
      "category": "theme|completeness|continuity|material_usage|style|factuality",
      "description": "具体问题",
      "suggestion": "返修建议"
    }}
  ],
  "revision_brief": "给返修 Agent 的明确返修指令"
}}

主题：{source_payload.get("topic", "")}
时间范围：{source_payload.get("time_range", "")}

时间线材料：
{events_text}

待审查剧本：
{draft_text}
""".strip()


def build_revision_prompt(source_payload: dict[str, Any], draft: dict[str, Any], review: dict[str, Any]) -> str:
    events_text = json.dumps(source_payload.get("events", []), ensure_ascii=False, indent=2)
    draft_text = json.dumps(draft, ensure_ascii=False, indent=2)
    review_text = json.dumps(review, ensure_ascii=False, indent=2)
    return f"""
请根据审查意见返修完整长文讲述稿。你可以保留好用的表达，但必须解决审查 Agent 指出的问题。

返修目标：
- 保持五部分输出完整：史实卡片、因果链、场景大纲、完整剧本、事实边界。
- 重新判断主题核心，把主要篇幅给主题真正要回答的问题。
- 基于 event.content 补足前因、过程、细节、结果和影响。
- 对审查指出的一笔带过节点，必须扩写成 2-5 个自然段；补足场面、机制、例子、影响和与下一个节点的连接。
- 优先补充 event.content 中被遗漏的数字、具体行为、物件、对比、时间范围和因果解释。
- 修复段落之间的跳跃，让观众知道每一步为什么发生、如何发生、造成什么后果。
- 删除或压缩不服务主题的铺垫内容。
- 保持幽默风趣，但幽默必须帮助理解。
- 不要引入时间线材料以外的新事实。

输出格式必须与初稿完全一致，严格 JSON：
{{
  "title": "短剧标题",
  "logline": "一句话说明这一集讲什么",
  "fact_cards": [
    {{
      "id": "F1",
      "fact": "史实",
      "time": "时间",
      "place": "地点",
      "source_basis": "来源依据",
      "confidence": "高|中|低",
      "drama_direction": "可短剧化方向",
      "do_not_overstate": "不能乱写或不能写死的地方"
    }}
  ],
  "causal_chain": ["因果链条"],
  "outline": [
    {{
      "title": "段落标题",
      "core_point": "核心知识点",
      "opening_image": "开场画面",
      "human_action": "人群动作",
      "conflict": "冲突/问题",
      "change": "选择/变化",
      "cost": "代价/后果",
      "transition": "与下一段衔接"
    }}
  ],
  "article": "返修后的完整长文讲述稿。不要划分场景，不要输出 dialogue，不要输出分镜脚本。",
  "fact_boundaries": {{
    "explicitly_supported": ["原始材料明确支持的内容"],
    "dramatized_inference": ["合理场景化改写"],
    "needs_manual_check": ["需要人工核对的内容"],
    "possible_overstatement": ["可能过度夸张的表达"],
    "suggested_sources": ["建议补充权威资料的地方"]
  }}
}}

主题：{source_payload.get("topic", "")}
时间范围：{source_payload.get("time_range", "")}

时间线材料：
{events_text}

初稿：
{draft_text}

审查意见：
{review_text}
""".strip()


def build_assistant_prompt(payload: dict[str, Any], *, intent: str) -> str:
    if intent == "SMALLTALK":
        return build_chat_prompt(payload)
    if intent in {"EXPLAIN_SCRIPT", "EXPLAIN_SELECTION", "ASK_SOURCE"}:
        return build_explain_prompt(payload)
    if intent in {"REVIEW_SCRIPT", "REVIEW_SELECTION"}:
        return build_review_prompt_for_assistant(payload)
    if intent == "REVISE_PENDING":
        return build_revise_pending_prompt(payload)
    return build_edit_proposal_prompt(payload)


def assistant_prompt_common(payload: dict[str, Any]) -> dict[str, str]:
    contexts_text = json.dumps(payload.get("contexts", []), ensure_ascii=False, indent=2)
    conversation_text = json.dumps(compact_conversation(payload.get("conversation", [])), ensure_ascii=False, indent=2)
    pending_edit_text = json.dumps(payload.get("pending_edit") or {}, ensure_ascii=False, indent=2)
    memory_text = json.dumps(payload.get("memory") or {}, ensure_ascii=False, indent=2)
    return {
        "topic": str(payload.get("topic") or ""),
        "time_range": str(payload.get("time_range") or ""),
        "script_title": str(payload.get("script_title") or ""),
        "script": json.dumps(payload.get("script", {}), ensure_ascii=False, indent=2),
        "selection": str(payload.get("selection") or ""),
        "instruction": str(payload.get("instruction") or ""),
        "contexts": contexts_text,
        "conversation": conversation_text,
        "pending_edit": pending_edit_text,
        "memory": memory_text,
    }


def build_chat_prompt(payload: dict[str, Any]) -> str:
    data = assistant_prompt_common(payload)
    return f"""
你是固定在剧本阅读器右侧的“剧本对话助手”。当前任务是普通聊天或功能说明。

规则：
1. 自然、简短地回答用户。
2. 不要生成 replacement。
3. 不要声称已经保存或修改正文。
4. 如用户询问功能，说明：可以解释整篇或选中段落、评审剧本、生成候选修改；候选修改必须用户点击应用后才会保存。

输出严格 JSON：
{{
  "answer": "给用户的自然语言回复",
  "replacement": "",
  "used_context_ids": []
}}

用户要求：
{data["instruction"]}

当前记忆：
{data["memory"]}
""".strip()


def build_explain_prompt(payload: dict[str, Any]) -> str:
    data = assistant_prompt_common(payload)
    return f"""
你是剧本解释助手。当前任务是解释剧本或选中段落，不做改写。

规则：
1. 只解释含义、叙事作用、事实边界。
2. 如有本地资料片段，只引用实际使用的 chunk_id。
3. replacement 必须为空字符串。
4. 不要生成候选修改，不要保存正文。

输出严格 JSON：
{{
  "answer": "解释内容",
  "replacement": "",
  "used_context_ids": ["实际使用的 chunk_id"]
}}

主题：{data["topic"]}
时间范围：{data["time_range"]}
剧本标题：{data["script_title"]}

完整剧本与推导链条：
{data["script"]}

用户选中的原文：
{data["selection"]}

用户要求：
{data["instruction"]}

本地资料片段：
{data["contexts"]}
""".strip()


def build_review_prompt_for_assistant(payload: dict[str, Any]) -> str:
    data = assistant_prompt_common(payload)
    return f"""
你是剧本评审助手。当前任务是评审整篇剧本或选中段落，不直接改写。

规则：
1. 指出结构、事实、叙事节奏、可补充处。
2. 如果用户没有明确要求“直接改写”，replacement 必须为空字符串。
3. 事实判断要依据完整剧本、本地资料片段和推导链条；资料不足就说明不足。
4. 不要保存正文。

输出严格 JSON：
{{
  "answer": "评审意见和建议",
  "replacement": "",
  "used_context_ids": ["实际使用的 chunk_id"]
}}

主题：{data["topic"]}
时间范围：{data["time_range"]}
剧本标题：{data["script_title"]}

完整剧本与推导链条：
{data["script"]}

用户选中的原文：
{data["selection"]}

用户要求：
{data["instruction"]}

本地资料片段：
{data["contexts"]}

最近对话历史：
{data["conversation"]}
""".strip()


def build_edit_proposal_prompt(payload: dict[str, Any]) -> str:
    data = assistant_prompt_common(payload)
    return f"""
你是局部改写助手。当前任务只生成候选 replacement，不保存正文。

规则：
1. 只改写“用户选中的原文”，不能重写整篇剧本。
2. replacement 必须是可直接替换选中文本的正文，不带标题、解释、Markdown 或引号。
3. answer 简短说明修改方向，并提醒这是待确认候选。
4. 优先遵守记忆里的风格偏好，例如口语、事实谨慎、不要营销号。
5. 必须依据当前剧本和本地资料片段；资料不足时不要编造成确定事实。

输出严格 JSON：
{{
  "answer": "候选修改说明",
  "replacement": "只用于替换选中内容的正文",
  "used_context_ids": ["实际使用的 chunk_id"]
}}

主题：{data["topic"]}
时间范围：{data["time_range"]}
剧本标题：{data["script_title"]}

用户选中的原文：
{data["selection"]}

用户要求：
{data["instruction"]}

当前记忆：
{data["memory"]}

本地资料片段：
{data["contexts"]}

完整剧本与推导链条：
{data["script"]}
""".strip()


def build_revise_pending_prompt(payload: dict[str, Any]) -> str:
    data = assistant_prompt_common(payload)
    return f"""
你是候选修改返修助手。当前任务是基于上一版候选修改继续调整，生成新的 replacement，不保存正文。

规则：
1. 仍然只替换同一段原文。
2. 必须参考“待确认修改”和用户新的调整意见。
3. replacement 只放新候选正文，不带解释、标题或 Markdown。
4. answer 说明这一版相对上一版怎么调整。

输出严格 JSON：
{{
  "answer": "返修说明",
  "replacement": "新的候选替换正文",
  "used_context_ids": ["实际使用的 chunk_id"]
}}

用户选中的原文：
{data["selection"]}

用户新的调整意见：
{data["instruction"]}

待确认修改：
{data["pending_edit"]}

当前记忆：
{data["memory"]}

本地资料片段：
{data["contexts"]}

最近对话历史：
{data["conversation"]}
""".strip()


def build_selection_edit_prompt(payload: dict[str, Any]) -> str:
    data = assistant_prompt_common(payload)
    return f"""
你是固定在剧本阅读器右侧的“剧本对话助手”。请像 ChatGPT 一样自然回应用户，同时能阅读当前完整剧本、推导链条和本地资料片段。

行为规则：
1. 普通聊天、问候、询问能力时，正常回答即可，replacement 必须为空字符串。
2. 用户要求阅读或评审当前剧本时，基于“完整剧本与推导链条”指出结构、事实、叙事节奏或可补充处。
3. 用户选中了原文时，先明确你正在处理这段内容，再结合本地资料片段和推导链给出解释、评审或修改建议。
4. 只有当用户明确要求改写、补充、润色、重写选中段落，且“用户选中的原文”非空时，replacement 才写可直接替换选中内容的正文。
5. replacement 只能替换选中段落，不能重写整篇剧本，不能带标题、解释、Markdown 或引号。
6. 如果用户只是咨询、不懂、要求分析，replacement 为空字符串。
7. 必须优先依据本地资料片段和当前剧本；资料不足时明确说明“不足以确定”，不要把猜测写成事实。
8. 中文格式要规范：中文标点、自然分段、口语讲述但事实谨慎，避免英文半角标点混入正文。
9. 必须读取“最近对话历史”。如果用户没有重新选中原文，但明显在继续讨论上一段、纠正上一条建议或要求开始修改，就沿用最近对话里的同一段原文和修改意图，不要说“尚未选中任何原文段落”。
10. 如果“待确认修改”存在，用户又说“开始修改、确认、可以、按这个改”等，应理解为确认保存上一条候选修改；如果用户是在纠正上一条建议，则应基于同一段原文重新生成更贴合的新 replacement。

输出严格 JSON：
{{
  "answer": "给用户的自然语言回复；若给出候选修改，要说明这是待用户确认的局部修改建议",
  "replacement": "只用于替换选中内容的正文；不需要替换或未选中文本时为空字符串",
  "used_context_ids": ["实际使用的 chunk_id"]
}}

主题：{payload.get("topic", "")}
时间范围：{payload.get("time_range", "")}
剧本标题：{payload.get("script_title", "")}

完整剧本与推导链条：
{data["script"]}

用户选中的原文：
{payload.get("selection", "")}

用户要求：
{payload.get("instruction", "")}

最近对话历史：
{data["conversation"]}

待确认修改：
{data["pending_edit"]}

本地资料片段：
{data["contexts"]}
""".strip()


def compact_conversation(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for message in messages[-10:]:
        result = message.get("result") if isinstance(message.get("result"), dict) else {}
        compacted.append(
            {
                "role": message.get("role", ""),
                "content": str(message.get("content") or "")[:1200],
                "selection": str(message.get("selection") or "")[:1200],
                "replacement": str(result.get("replacement") or "")[:1200],
                "applied": bool(result.get("applied")) if result else False,
            }
        )
    return compacted


def normalize_script_payload(payload: dict[str, Any], topic: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("剧本 Agent 返回不是 JSON object")
    scenes = normalize_scenes(payload.get("script"))
    article = str(payload.get("article") or "").strip()
    if not article and scenes:
        article = "\n\n".join(scene.get("narration", "") for scene in scenes if scene.get("narration")).strip()
    return {
        "title": str(payload.get("title") or topic),
        "logline": str(payload.get("logline") or ""),
        "fact_cards": normalize_fact_cards(payload.get("fact_cards")),
        "causal_chain": normalize_string_list(payload.get("causal_chain")),
        "outline": normalize_outline(payload.get("outline")),
        "article": article,
        "fact_boundaries": normalize_fact_boundaries(payload.get("fact_boundaries")),
        "script": scenes,
    }


def normalize_fact_cards(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    cards = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        cards.append(
            {
                "id": str(item.get("id") or f"F{index}"),
                "fact": str(item.get("fact") or ""),
                "time": str(item.get("time") or ""),
                "place": str(item.get("place") or ""),
                "source_basis": str(item.get("source_basis") or ""),
                "confidence": str(item.get("confidence") or ""),
                "drama_direction": str(item.get("drama_direction") or ""),
                "do_not_overstate": str(item.get("do_not_overstate") or ""),
            }
        )
    return [item for item in cards if item["fact"]]


def normalize_outline(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    outline = []
    for item in value:
        if not isinstance(item, dict):
            continue
        outline.append(
            {
                "title": str(item.get("title") or ""),
                "core_point": str(item.get("core_point") or ""),
                "opening_image": str(item.get("opening_image") or ""),
                "human_action": str(item.get("human_action") or ""),
                "conflict": str(item.get("conflict") or ""),
                "change": str(item.get("change") or ""),
                "cost": str(item.get("cost") or ""),
                "transition": str(item.get("transition") or ""),
            }
        )
    return [item for item in outline if item["title"] or item["core_point"]]


def normalize_fact_boundaries(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        value = {}
    return {
        "explicitly_supported": normalize_string_list(value.get("explicitly_supported")),
        "dramatized_inference": normalize_string_list(value.get("dramatized_inference")),
        "needs_manual_check": normalize_string_list(value.get("needs_manual_check")),
        "possible_overstatement": normalize_string_list(value.get("possible_overstatement")),
        "suggested_sources": normalize_string_list(value.get("suggested_sources")),
    }


def normalize_review_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    issues = []
    for item in payload.get("issues") or []:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity") or "major").lower()
        if severity not in {"critical", "major", "minor"}:
            severity = "major"
        category = str(item.get("category") or "completeness")
        issues.append(
            {
                "severity": severity,
                "category": category,
                "description": str(item.get("description") or ""),
                "suggestion": str(item.get("suggestion") or ""),
            }
        )
    score = int(payload.get("score") or 0)
    score = max(1, min(score or 1, 5))
    return {
        "passed": bool(payload.get("passed")) and score >= 4 and not any(item["severity"] in {"critical", "major"} for item in issues),
        "score": score,
        "verdict": str(payload.get("verdict") or ""),
        "theme_alignment": str(payload.get("theme_alignment") or ""),
        "story_completeness": str(payload.get("story_completeness") or ""),
        "continuity": str(payload.get("continuity") or ""),
        "material_usage": str(payload.get("material_usage") or ""),
        "key_node_depth": str(payload.get("key_node_depth") or ""),
        "simplicity_risk": str(payload.get("simplicity_risk") or ""),
        "missing_content": normalize_string_list(payload.get("missing_content")),
        "issues": issues,
        "revision_brief": str(payload.get("revision_brief") or ""),
    }


def normalize_edit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    return {
        "answer": str(payload.get("answer") or ""),
        "replacement": str(payload.get("replacement") or ""),
        "used_context_ids": normalize_string_list(payload.get("used_context_ids")),
    }


def review_requires_revision(review: dict[str, Any]) -> bool:
    if not review.get("passed"):
        return True
    if int(review.get("score") or 0) < 4:
        return True
    return any(item.get("severity") in {"critical", "major"} for item in review.get("issues", []))


def normalize_scenes(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    scenes = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        scenes.append(
            {
                "scene": int(item.get("scene") or index),
                "title": str(item.get("title") or f"场景 {index}"),
                "setting": str(item.get("setting") or ""),
                "narration": str(item.get("narration") or ""),
                "dialogue": normalize_dialogue(item.get("dialogue")),
                "visual_notes": str(item.get("visual_notes") or ""),
            }
        )
    return scenes


def normalize_dialogue(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    return [
        {"speaker": str(item.get("speaker") or "旁白"), "line": str(item.get("line") or "")}
        for item in value
        if isinstance(item, dict)
    ]


def normalize_subjects(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    subjects = []
    for item in value:
        if not isinstance(item, dict):
            continue
        subjects.append(
            {
                "name": str(item.get("name") or ""),
                "type": str(item.get("type") or "概念"),
                "intro": str(item.get("intro") or ""),
                "visual_modeling": str(item.get("visual_modeling") or ""),
                "script_usage": str(item.get("script_usage") or ""),
            }
        )
    return [item for item in subjects if item["name"]]


def normalize_map_shots(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    shots = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        region = str(item.get("region") or infer_region(item)).strip() or "world"
        shots.append(
            {
                "title": str(item.get("title") or f"地图画面 {index}"),
                "region": region,
                "places": normalize_string_list(item.get("places")),
                "route": item.get("route") if isinstance(item.get("route"), dict) else None,
                "description": str(item.get("description") or ""),
                "script_scene": int(item.get("script_scene") or 1),
                "map_render_url": f"/api/maps/render?region={region}",
            }
        )
    return shots


def infer_region(item: dict[str, Any]) -> str:
    text = " ".join([str(item.get("title") or ""), str(item.get("description") or ""), " ".join(normalize_string_list(item.get("places")))])
    mapping = [
        ("非洲", "africa"),
        ("欧洲", "europe"),
        ("地中海", "mediterranean"),
        ("西亚", "west_asia"),
        ("中亚", "central_asia"),
        ("南亚", "south_asia"),
        ("东亚", "east_asia"),
        ("中国", "china"),
        ("美洲", "americas"),
        ("澳洲", "australasia"),
        ("澳大利亚", "australasia"),
    ]
    for keyword, region in mapping:
        if keyword in text:
            return region
    return "world"


def normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def unique_script_dir(root: Path, topic: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    slug = slugify(topic) or "script"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = root / f"{stamp}_{slug}"
    if not candidate.exists():
        return candidate
    for index in range(2, 1000):
        next_candidate = root / f"{stamp}_{slug}_{index}"
        if not next_candidate.exists():
            return next_candidate
    raise RuntimeError("无法生成剧本输出目录")


def slugify(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", value).strip("_").lower()
    return slug[:80]


def render_script_markdown(result: dict[str, Any]) -> str:
    lines = [
        f"# {result['script']['title']}",
        "",
        f"- 主题: {result['topic']}",
        f"- 时间范围: {result['time_range']}",
        f"- 匹配时间线模块: {result['matched_event_count']}",
        "",
        f"## 一句话",
        "",
        result["script"].get("logline", ""),
        "",
        "## 1. 史实提取",
        "",
    ]
    for card in result["script"].get("fact_cards", []):
        lines.extend(
            [
                f"### {card.get('id', '')} {card.get('fact', '')}",
                "",
                f"- 时间: {card.get('time', '')}",
                f"- 地点: {card.get('place', '')}",
                f"- 来源依据: {card.get('source_basis', '')}",
                f"- 置信度: {card.get('confidence', '')}",
                f"- 可短剧化方向: {card.get('drama_direction', '')}",
                f"- 不能乱写: {card.get('do_not_overstate', '')}",
                "",
            ]
        )
    lines.extend(["## 2. 因果链", ""])
    lines.extend([f"- {item}" for item in result["script"].get("causal_chain", [])])
    lines.extend(["", "## 3. 场景大纲", ""])
    for item in result["script"].get("outline", []):
        lines.extend(
            [
                f"### {item.get('title', '')}",
                "",
                f"- 核心知识点: {item.get('core_point', '')}",
                f"- 开场画面: {item.get('opening_image', '')}",
                f"- 人群动作: {item.get('human_action', '')}",
                f"- 冲突/问题: {item.get('conflict', '')}",
                f"- 选择/变化: {item.get('change', '')}",
                f"- 代价/后果: {item.get('cost', '')}",
                f"- 衔接: {item.get('transition', '')}",
                "",
            ]
        )
    lines.extend(
        [
            "## 4. 完整剧本",
            "",
        ]
    )
    article = result["script"].get("article", "")
    if article:
        lines.extend([article, ""])
    else:
        for scene in result["script"].get("scenes", []):
            lines.extend([f"### 场景 {scene['scene']}：{scene['title']}", "", f"场景：{scene['setting']}", "", scene["narration"], ""])
            for line in scene.get("dialogue", []):
                lines.append(f"- {line['speaker']}：{line['line']}")
            lines.extend(["", f"画面：{scene['visual_notes']}", ""])
    boundaries = result["script"].get("fact_boundaries") or {}
    lines.extend(["## 5. 事实边界与人工核对点", ""])
    boundary_titles = [
        ("explicitly_supported", "原始材料明确支持"),
        ("dramatized_inference", "合理场景化改写"),
        ("needs_manual_check", "需要人工核对"),
        ("possible_overstatement", "可能过度夸张"),
        ("suggested_sources", "建议补充资料"),
    ]
    for key, title in boundary_titles:
        values = boundaries.get(key) or []
        lines.extend([f"### {title}", ""])
        lines.extend([f"- {item}" for item in values] or ["- 无"])
        lines.append("")
    lines.extend(["## 主体清单", ""])
    for subject in result.get("subjects", []):
        lines.extend([f"### {subject['name']}", "", f"- 类型: {subject['type']}", f"- 介绍: {subject['intro']}", f"- 建模: {subject['visual_modeling']}", f"- 用途: {subject['script_usage']}", ""])
    lines.extend(["## 地图画面", ""])
    for shot in result.get("map_shots", []):
        lines.extend([f"### {shot['title']}", "", f"- 区域: {shot['region']}", f"- 地点: {'、'.join(shot.get('places', []))}", f"- 说明: {shot['description']}", ""])
    review = result.get("script_review") or {}
    if review:
        lines.extend(
            [
                "## 剧本审查",
                "",
                f"- 是否通过: {'是' if review.get('passed') else '否'}",
                f"- 分数: {review.get('score', '')}",
                f"- 返修次数: {result.get('revision_count', 0)}",
                f"- 结论: {review.get('verdict', '')}",
                f"- 主题贴合: {review.get('theme_alignment', '')}",
                f"- 完整性: {review.get('story_completeness', '')}",
                f"- 连贯性: {review.get('continuity', '')}",
                f"- 材料利用: {review.get('material_usage', '')}",
                f"- 关键节点展开: {review.get('key_node_depth', '')}",
                f"- 简陋风险: {review.get('simplicity_risk', '')}",
                "",
            ]
        )
        missing = review.get("missing_content") or []
        if missing:
            lines.extend(["### 仍需注意的缺失", ""])
            lines.extend([f"- {item}" for item in missing])
            lines.append("")
        issues = review.get("issues") or []
        if issues:
            lines.extend(["### 审查问题", ""])
            for issue in issues:
                lines.extend(
                    [
                        f"- [{issue.get('severity', '')}] {issue.get('category', '')}: {issue.get('description', '')}",
                        f"  建议: {issue.get('suggestion', '')}",
                    ]
                )
            lines.append("")
    return "\n".join(lines)
