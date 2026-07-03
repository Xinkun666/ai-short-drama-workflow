from __future__ import annotations

import json
import hashlib
import os
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from drama_agents.chapter_refiner import parse_json_object
from drama_agents.storage import MaterialDatabase, current_timestamp


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
            "created_at": current_timestamp(),
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

    def plan_assist(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self.provider or not hasattr(self.provider, "plan_assistant_action"):
            return None
        return self.provider.plan_assistant_action(payload)

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
        reference_selection: dict[str, Any] | None = None,
        focus_action: str = "",
        focus_reason: str = "",
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
            "reference_selection": reference_selection or {},
            "focus_action": focus_action,
            "focus_reason": focus_reason,
        }
        return normalize_edit_payload(self.provider.edit_selection(payload))

    def adapt_for_storyboard(self, *, generation: dict[str, Any]) -> dict[str, Any]:
        if not self.provider:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY，无法改造分镜剧本。")
        supports_multistage = all(
            hasattr(self.provider, method_name)
            for method_name in ("extract_content_atoms", "plan_narrator_scenes", "write_narrator_scene_batch")
        )
        if not hasattr(self.provider, "adapt_script_for_storyboard") and not supports_multistage:
            raise RuntimeError("当前剧本 provider 不支持剧本改造。")
        script = generation.get("script") if isinstance(generation.get("script"), dict) else {}
        article = str(script.get("article") or "").strip()
        if not article:
            raise ValueError("当前剧本没有完整正文，无法改造为分镜剧本。")
        title = str(script.get("title") or generation.get("topic") or "")
        payload = {
            "topic": generation.get("topic", ""),
            "time_range": generation.get("time_range", ""),
            "script": script,
            "article": article,
            "matched_events": generation.get("matched_events") or [],
        }
        if supports_multistage:
            content_atoms_payload = normalize_content_atoms_payload(
                self.provider.extract_content_atoms({**payload, "title": title}),
                fallback_article=article,
            )
            scene_plan_payload = normalize_scene_plan_payload(
                self.provider.plan_narrator_scenes({**payload, **content_atoms_payload, "title": title})
            )
            scene_script = []
            for scene_plan_batch in chunk_items(scene_plan_payload["scene_plan"], adaptation_batch_size()):
                batch_payload = {
                    **payload,
                    **content_atoms_payload,
                    "title": title,
                    "episode_structure": scene_plan_payload["episode_structure"],
                    "scene_plan": scene_plan_batch,
                    "full_scene_plan": scene_plan_payload["scene_plan"],
                }
                batch_result = self.provider.write_narrator_scene_batch(batch_payload)
                if isinstance(batch_result, dict):
                    scene_script.extend(normalize_scene_script(batch_result.get("scene_script")))
            raw_adapted = {
                "title": f"{title or '分场脚本'} - 分镜前脚本",
                "format": "narrator_led_science_comic",
                "episode_structure": scene_plan_payload["episode_structure"],
                "scene_plan": scene_plan_payload["scene_plan"],
                "scene_script": scene_script,
                "content_atoms": content_atoms_payload["content_atoms"],
                "causal_chain": content_atoms_payload["causal_chain"],
                "retention_requirements": content_atoms_payload["retention_requirements"],
                "adaptation_notes": ["按 content_atoms → scene_plan → scene_script 多阶段生成。"],
            }
        else:
            raw_adapted = self.provider.adapt_script_for_storyboard(payload)
            content_atoms_payload = normalize_content_atoms_payload(raw_adapted, fallback_article=article)
        adapted = normalize_narrator_scene_script_payload(raw_adapted, fallback_article=article)
        if not adapted.get("content_atoms") and content_atoms_payload.get("content_atoms"):
            adapted["content_atoms"] = content_atoms_payload["content_atoms"]
            adapted["causal_chain"] = content_atoms_payload["causal_chain"]
            adapted["retention_requirements"] = content_atoms_payload["retention_requirements"]
            adapted["retention_review"] = validate_content_retention(content_atoms_payload, adapted.get("scene_script") or [])
        if (
            supports_multistage
            and hasattr(self.provider, "repair_narrator_scene_script")
            and should_repair_content_retention(adapted.get("retention_review") or {})
        ):
            repair_payload = self.provider.repair_narrator_scene_script(
                {
                    **payload,
                    "title": title,
                    "original_payload": adapted,
                    "content_atoms": adapted.get("content_atoms") or [],
                    "causal_chain": adapted.get("causal_chain") or [],
                    "scene_plan": adapted.get("scene_plan") or [],
                    "retention_review": adapted.get("retention_review") or {},
                    "missing_must_keep_atoms": (adapted.get("retention_review") or {}).get("missing_must_keep_atoms") or [],
                }
            )
            adapted = merge_repaired_scenes(adapted, repair_payload)
        adapted["created_at"] = current_timestamp()
        adapted["source_article_hash"] = text_hash(article)
        adapted["source_title"] = title
        return adapted


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
        self.adaptation_model = (
            os.environ.get("DEEPSEEK_ADAPTATION_MODEL")
            or os.environ.get("DEEPSEEK_SCRIPT_MODEL")
            or os.environ.get("DEEPSEEK_MODEL")
            or "deepseek-v4-pro"
        )
        self.base_url = base_url
        self.timeout = timeout or int(os.environ.get("DEEPSEEK_TIMEOUT", "240"))

    def get_adaptation_model(self) -> str:
        return (
            os.environ.get("DEEPSEEK_ADAPTATION_MODEL")
            or os.environ.get("DEEPSEEK_SCRIPT_MODEL")
            or os.environ.get("DEEPSEEK_MODEL")
            or self.adaptation_model
            or "deepseek-v4-pro"
        )

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

    def plan_assistant_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._complete_json(
            system=(
                "你是剧本对话助手的 Planner Agent，只负责理解用户自然语言并选择工具。"
                "你不能改写正文，不能保存正文，只输出合法 JSON。"
            ),
            prompt=build_assistant_planner_prompt(payload),
            temperature=0.05,
            max_tokens=int(os.environ.get("SCRIPT_ASSISTANT_PLANNER_MAX_TOKENS", "1200")),
        )

    def edit_selection(self, payload: dict[str, Any]) -> dict[str, Any]:
        intent = str(payload.get("intent") or "")
        return self._complete_json(
            system=(
                "你是剧本阅读、评审、修改对话助手，使用 DeepSeek V4 Pro 风格能力工作。"
                "Planner Agent 已经完成意图理解并选择任务，你只能执行指定任务，不能自行决定保存正文。"
                "只输出合法 JSON。"
            ),
            prompt=build_assistant_prompt(payload, intent=intent),
            temperature=0.45,
            max_tokens=int(os.environ.get("SCRIPT_EDIT_MAX_TOKENS", "3600")),
        )

    def extract_content_atoms(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._complete_json(
            system=(
                "你是内容保真分析 Agent，只负责从原文中提取不可丢失的内容原子和推理链。"
                "你不是摘要器，不要生成短稿。只输出合法 json object。"
            ),
            prompt=build_content_atoms_extraction_prompt(payload),
            temperature=0.1,
            max_tokens=int(os.environ.get("SCRIPT_ATOMS_MAX_TOKENS", "20000")),
            model=self.get_adaptation_model(),
        )

    def plan_narrator_scenes(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._complete_json(
            system=(
                "你是讲述人驱动科普漫剧的分场规划 Agent。"
                "只规划 scene_plan，不写最终旁白。只输出合法 json object。"
            ),
            prompt=build_narrator_scene_plan_prompt(payload),
            temperature=0.2,
            max_tokens=int(os.environ.get("SCRIPT_SCENE_PLAN_MAX_TOKENS", "16000")),
            model=self.get_adaptation_model(),
        )

    def write_narrator_scene_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._complete_json(
            system=(
                "你是有 30 年纪录片、动画科普和短剧导演经验的分场脚本改造 Agent。"
                "你负责按 scene_plan 分批写讲述人驱动的 AI 科普漫剧分场。只输出合法 json object。"
            ),
            prompt=build_narrator_scene_batch_prompt(payload),
            temperature=0.35,
            max_tokens=int(os.environ.get("SCRIPT_SCENE_BATCH_MAX_TOKENS", "24000")),
            model=self.get_adaptation_model(),
        )

    def repair_narrator_scene_script(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._complete_json(
            system=(
                "你是分场脚本内容保真修复 Agent。"
                "你只补漏，不重写全片。只输出合法 json object。"
            ),
            prompt=build_repair_missing_atoms_prompt(payload),
            temperature=0.2,
            max_tokens=int(os.environ.get("SCRIPT_ADAPTATION_REPAIR_MAX_TOKENS", "16000")),
            model=self.get_adaptation_model(),
        )

    def adapt_script_for_storyboard(self, payload: dict[str, Any]) -> dict[str, Any]:
        script = payload.get("script") if isinstance(payload.get("script"), dict) else {}
        article = str(payload.get("article") or script.get("article") or "").strip()
        title = str(script.get("title") or payload.get("topic") or "")
        content_atoms_payload = normalize_content_atoms_payload(
            self.extract_content_atoms({**payload, "article": article, "title": title}),
            fallback_article=article,
        )
        scene_plan_payload = normalize_scene_plan_payload(
            self.plan_narrator_scenes({**payload, **content_atoms_payload, "article": article, "title": title})
        )
        scene_script = []
        for batch in chunk_items(scene_plan_payload["scene_plan"], adaptation_batch_size()):
            batch_payload = {
                **payload,
                **content_atoms_payload,
                "article": article,
                "title": title,
                "episode_structure": scene_plan_payload["episode_structure"],
                "scene_plan": batch,
            }
            scene_script.extend(normalize_scene_script(self.write_narrator_scene_batch(batch_payload).get("scene_script")))
        return {
            "title": f"{title or '分场脚本'} - 分镜前脚本",
            "format": "narrator_led_science_comic",
            "episode_structure": scene_plan_payload["episode_structure"],
            "scene_plan": scene_plan_payload["scene_plan"],
            "scene_script": scene_script,
            "content_atoms": content_atoms_payload["content_atoms"],
            "causal_chain": content_atoms_payload["causal_chain"],
            "retention_requirements": content_atoms_payload["retention_requirements"],
        }

    def _complete_json(
        self,
        *,
        system: str,
        prompt: str,
        temperature: float,
        max_tokens: int,
        model: str | None = None,
    ) -> dict[str, Any]:
        try:
            call_args = {
                "system": system,
                "prompt": prompt,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if model is not None:
                call_args["model"] = model
            return self._complete_json_once(**call_args)
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
                repair_call_args = {
                    "system": system,
                    "prompt": repair_prompt,
                    "temperature": min(temperature, 0.2),
                    "max_tokens": max_tokens,
                }
                if model is not None:
                    repair_call_args["model"] = model
                return self._complete_json_once(**repair_call_args)
            except json.JSONDecodeError as repair_exc:
                detail = getattr(self, "_last_parse_error_detail", "")
                suffix = f"；返回预览：{detail}" if detail else ""
                raise RuntimeError(f"DeepSeek 返回的 JSON 仍无法解析：{repair_exc}{suffix}") from repair_exc

    def _complete_json_once(
        self,
        *,
        system: str,
        prompt: str,
        temperature: float,
        max_tokens: int,
        model: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "model": model or self.model,
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


def adaptation_batch_size() -> int:
    return max(1, normalize_int(os.environ.get("SCRIPT_SCENE_BATCH_SIZE"), default=6))


def chunk_items(items: list[Any], size: int) -> list[list[Any]]:
    if size <= 0:
        size = 1
    return [items[index : index + size] for index in range(0, len(items), size)]


def should_repair_content_retention(review: dict[str, Any]) -> bool:
    if not isinstance(review, dict):
        return False
    if review.get("missing_must_keep_atoms"):
        return True
    coverage_ratio = normalize_ratio(review.get("coverage_ratio"), default=1.0)
    return coverage_ratio < 0.92


def text_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


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


def build_storyboard_script_adaptation_prompt(payload: dict[str, Any]) -> str:
    script = payload.get("script") if isinstance(payload.get("script"), dict) else {}
    source = {
        "topic": payload.get("topic", ""),
        "time_range": payload.get("time_range", ""),
        "title": script.get("title", ""),
        "logline": script.get("logline", ""),
        "article": payload.get("article", ""),
        "outline": script.get("outline") or [],
        "causal_chain": script.get("causal_chain") or [],
        "fact_cards": script.get("fact_cards") or [],
        "fact_boundaries": script.get("fact_boundaries") or {},
    }
    return (
        "你是拥有 30 年纪录片、动画科普、短剧导演和总编剧经验的剧本改造 Agent。\n"
        "你的任务不是再写一篇更华丽的文章，而是把“内容正确的文章式旁白稿”改造成"
        "“讲述人驱动的 AI 科普漫剧分场脚本”。这不是传统人物漫剧。\n\n"
        "核心定位：\n"
        "- 这是讲述人驱动的 AI 科普漫剧，不是传统人物漫剧。\n"
        "- 固定讲述人可以是虚拟科普老师、旁白声音、字幕讲述人或虚拟主持人。\n"
        "- 讲述人负责提出问题、解释知识、做类比、吐槽、总结因果、转场和回扣主题。\n"
        "- 历史人物、早期人类、动物、部落成员、尼安德特人等只负责画面演绎。\n"
        "- 所有解释性、知识性、因果性、总结性、吐槽性内容必须放在 narrator_lines。\n"
        "- historical_character_dialogue 只能少量出现，只能是生活化短句，历史人物不能承担知识解释。\n\n"
        "硬性边界：\n"
        "1. 不新增未经 fact_cards、causal_chain、outline 或原 article 支持的史实。\n"
        "2. 不输出主体锚点、场景锚点、图片 prompt、分镜镜头表或 keyframe prompt。\n"
        "3. 可以重排、合并、拆分和润色旁白，让它更适合视听表达，但必须保留事实链条。\n"
        "4. 不要按原文自然段机械切场。切场依据是视听表达任务，不是原文段落。\n"
        "5. 时间变化、地点变化、知识任务变化、视觉形式变化、情绪功能变化时必须考虑切场。\n"
        "6. 每个场景只完成一个知识任务或情绪任务，不要把多个机制塞进同一场。\n"
        "7. 每个场景必须包含 scene_type、narrator_lines、visual_layer、screen_text、fact_boundary。\n"
        "8. 历史人物不能承担知识解释，不能让古人说出现代知识、学术判断、宏观总结。\n"
        "9. 适合历史科普卡通短剧：普通场景优先地图、地貌、群体剪影、图解、字幕、箭头、轻运动；关键场景才需要强漫画构图。\n\n"
        "场景类型只能从以下类型中选择，可用 + 组合：\n"
        "- HOST_OPENING\n"
        "- HOST_EXPLANATION\n"
        "- MAP_ANIMATION\n"
        "- TIMELINE_CARD\n"
        "- HISTORICAL_REENACTMENT\n"
        "- INFOGRAPHIC\n"
        "- SYMBOLIC_MONTAGE\n"
        "- COMPARISON_SPLIT_SCREEN\n"
        "- TRANSITION_CARD\n"
        "- THEME_CALLBACK\n\n"
        "输出要求：只输出严格 JSON object，格式如下：\n"
        "{\n"
        '  "title": "分场脚本标题",\n'
        '  "format": "narrator_led_science_comic",\n'
        '  "narrator_profile": {\n'
        '    "role": "虚拟科普老师 / 旁白 / 字幕讲述人",\n'
        '    "tone": "幽默、清楚、有纪录片感",\n'
        '    "visual_presence": "avatar | voice_only | subtitle_only | mixed"\n'
        "  },\n"
        '  "episode_structure": {\n'
        '    "main_question": "这集要回答的核心问题",\n'
        '    "core_thesis": "观众看完后要记住的核心观点",\n'
        '    "target_duration_min": 8,\n'
        '    "estimated_scene_count": 16\n'
        "  },\n"
        '  "scene_script": [\n'
        "    {\n"
        '      "scene_id": "S01",\n'
        '      "scene_title": "场景标题",\n'
        '      "scene_type": "HOST_OPENING + MAP_ANIMATION",\n'
        '      "duration_sec": 20,\n'
        '      "beat_function": "开场钩子 / 问题提出 / 困境建立 / 机制解释 / 历史转折 / 结果展示 / 代价揭示 / 主题回扣",\n'
        '      "knowledge_point": "这一场只讲一个核心知识点",\n'
        '      "narrator_lines": ["讲述人台词 1", "讲述人台词 2"],\n'
        '      "visual_layer": {\n'
        '        "main_visual": "画面主内容",\n'
        '        "character_action": "历史人物或群体在画面中做什么；没有就写无",\n'
        '        "environment": "时间、地点、环境氛围",\n'
        '        "camera": "镜头组织方式",\n'
        '        "animation_logic": "地图、箭头、字幕、图解、符号如何动起来",\n'
        '        "transition": "如何进入下一场"\n'
        "      },\n"
        '      "screen_text": ["屏幕关键词 1", "屏幕关键词 2"],\n'
        '      "historical_character_dialogue": [\n'
        "        {\n"
        '          "speaker": "角色名",\n'
        '          "line": "极短生活化台词",\n'
        '          "purpose": "气氛/幽默/情绪",\n'
        '          "evidence_level": "合理场景化"\n'
        "        }\n"
        "      ],\n"
        '      "audio_hint": "旁白、环境音、音乐或音效建议",\n'
        '      "fact_boundary": "史实明确支持 | 合理场景化 | 需人工核对",\n'
        '      "source_trace": ["来自原文第几段、outline 或 fact_card"],\n'
        '      "next_scene_hook": "一句话说明如何衔接下一场"\n'
        "    }\n"
        "  ],\n"
        '  "adapted_article": "兼容旧 UI，可省略；系统会从 narrator_lines 自动生成",\n'
        '  "adapted_segments": [],\n'
        '  "adaptation_notes": ["你做了哪些生产化改造"],\n'
        '  "review_notes": ["仍需人工注意的问题"],\n'
        '  "scene_review": {}\n'
        "}\n\n"
        "分场要求：\n"
        "- 不要按原文自然段机械切场；scene_script 是视听表达单元，不是文章段落列表。\n"
        "- 每个 scene 的 narrator_lines 通常 1-4 句；超过 4 句通常说明没有真正分场。\n"
        "- historical_character_dialogue 是点缀，不是讲课；总量必须远少于讲述人台词。\n"
        "- screen_text 只放屏幕关键词、概念词、时间地点或转场卡，不要塞成长句。\n"
        "- source_trace 要帮助人工知道这一场来自原文、outline、causal_chain 或 fact_card 的哪部分。\n\n"
        f"输入剧本：\n{json.dumps(source, ensure_ascii=False, indent=2)}"
    ).strip()


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


def build_assistant_planner_prompt(payload: dict[str, Any]) -> str:
    selection = payload.get("selection") or {}
    current_selection = payload.get("current_selection") or {}
    active_patch = payload.get("active_patch") or {}
    available_tools = payload.get("available_tools") or []
    conversation_text = json.dumps(compact_conversation(payload.get("conversation", [])), ensure_ascii=False, indent=2)
    return f"""
你是剧本对话助手的 Planner Agent。你的唯一任务是理解用户这一次自然语言请求，并选择后端工具。

重要边界：
1. 你只输出计划 JSON，不回答用户，不改写正文，不保存正文。
2. 用户只是让你理解、解释、评价、问“怎么样/如何/够不够抓人”时，选择 chat_with_selection，不默认检索 RAG。
3. 用户明确要求改写、润色、调整、补充、变得更抓人时，选择 propose_edit，并需要 RAG；后端只会生成候选修改，不会自动保存。
4. 用户问史实、出处、依据、资料是否支持时，选择 search_sources，并需要 RAG。
5. 用户明确应用或放弃候选修改时，选择 apply_patch 或 reject_patch；保存必须由本地工具完成。
6. 如果用户只是问候或问功能，选择 plain_chat。

可用工具：
{json.dumps(available_tools, ensure_ascii=False, indent=2)}

输出严格 JSON object：
{{
  "intent": "smalltalk | explain_selection | review_selection | ask_source | propose_edit | revise_pending | apply_patch | reject_patch",
  "tool": "plain_chat | chat_with_selection | search_sources | propose_edit | apply_patch | reject_patch",
  "needs_rag": false,
  "should_create_patch": false,
  "selection_policy": "use_current_selection | keep_current | no_selection",
  "reason": "一句话说明判断依据"
}}

判断例子：
- 有选区，用户说“这段写得怎么样 / 这段如何 / 你觉得这个开头够抓人吗”：intent=review_selection, tool=chat_with_selection, needs_rag=false, should_create_patch=false。
- 有选区，用户说“解释这段 / 这段什么意思”：intent=explain_selection, tool=chat_with_selection, needs_rag=false。
- 有选区，用户说“帮我润色 / 改得更抓人 / 重新写一下”：intent=propose_edit, tool=propose_edit, needs_rag=true, should_create_patch=true。
- 用户说“这句有依据吗 / 史实可靠吗 / 来源是什么”：intent=ask_source, tool=search_sources, needs_rag=true。

用户消息：
{payload.get("message", "")}

本次选区：
{json.dumps(selection, ensure_ascii=False, indent=2)}

当前对话已有焦点选区：
{json.dumps(current_selection, ensure_ascii=False, indent=2)}

待确认候选修改：
{json.dumps(active_patch, ensure_ascii=False, indent=2)}

显式按钮/前端 hint：
{payload.get("intent_hint", "")}

最近对话：
{conversation_text}
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
    reference_selection_text = json.dumps(payload.get("reference_selection") or {}, ensure_ascii=False, indent=2)
    focus_context_text = json.dumps(
        {
            "focus_action": payload.get("focus_action") or "",
            "focus_reason": payload.get("focus_reason") or "",
            "primary_selection": payload.get("selection") or "",
            "reference_selection": payload.get("reference_selection") or {},
            "instruction": (
                "primary_selection 是当前主要讨论或要修改的段落；"
                "reference_selection 只是参考材料或对比对象，除非 focus_action 是 SWITCH_TO_NEW，"
                "不要把参考段误当成要替换的主段落。"
            ),
        },
        ensure_ascii=False,
        indent=2,
    )
    return {
        "topic": str(payload.get("topic") or ""),
        "time_range": str(payload.get("time_range") or ""),
        "script_title": str(payload.get("script_title") or ""),
        "script": json.dumps(payload.get("script", {}), ensure_ascii=False, indent=2),
        "selection": str(payload.get("selection") or ""),
        "reference_selection": reference_selection_text,
        "focus_context": focus_context_text,
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

当前讨论焦点：
{data["focus_context"]}
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

本次新选区/参考选区：
{data["reference_selection"]}

当前讨论焦点：
{data["focus_context"]}

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

本次新选区/参考选区：
{data["reference_selection"]}

当前讨论焦点：
{data["focus_context"]}

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

本次新选区/参考选区：
{data["reference_selection"]}

当前讨论焦点：
{data["focus_context"]}

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

本次新选区/参考选区：
{data["reference_selection"]}

当前讨论焦点：
{data["focus_context"]}

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


ATOM_TYPES = {
    "fact",
    "number",
    "comparison",
    "cause_effect",
    "mechanism",
    "analogy",
    "example",
    "transition",
    "claim",
    "consequence",
    "visual_hook",
}

REASONING_ROLES = {
    "premise",
    "evidence",
    "mechanism",
    "consequence",
    "contrast",
    "turning_point",
    "audience_hook",
    "conclusion",
}

DETAIL_LOSS_ATOM_TYPES = {"comparison", "number", "cause_effect", "mechanism", "analogy", "example", "transition", "consequence"}

DEFAULT_FORBIDDEN_LOSS_TYPES = [
    "数字对比",
    "因果链",
    "机制解释",
    "关键例子",
    "关键类比",
    "转折点",
    "结论代价",
]

ALLOWED_NARRATOR_SCENE_TYPES = {
    "HOST_OPENING",
    "HOST_EXPLANATION",
    "MAP_ANIMATION",
    "TIMELINE_CARD",
    "HISTORICAL_REENACTMENT",
    "INFOGRAPHIC",
    "SYMBOLIC_MONTAGE",
    "COMPARISON_SPLIT_SCREEN",
    "TRANSITION_CARD",
    "THEME_CALLBACK",
}

VISUAL_LAYER_KEYS = [
    "main_visual",
    "character_action",
    "environment",
    "camera",
    "animation_logic",
    "transition",
]

MODERN_EXPLANATION_TERMS = [
    "认知革命",
    "大脑耗能",
    "DNA",
    "物种",
    "社会分工",
    "语言学家",
    "考古学家",
    "史实",
    "证据显示",
    "大约",
    "大约7万年前",
    "百分比",
    "能量消耗",
    "自然选择",
    "演化",
    "机制",
    "因果",
    "尼安德特人DNA",
    "复杂语言",
    "象征思维",
]


def build_content_atoms_extraction_prompt(payload: Any, title: str | None = None) -> str:
    if isinstance(payload, dict):
        source = {
            "title": payload.get("title") or title or "",
            "topic": payload.get("topic") or "",
            "time_range": payload.get("time_range") or "",
            "article": payload.get("article") or "",
        }
    else:
        source = {
            "title": title or "",
            "article": payload or "",
        }
    return f"""
你是“内容原子提取 Agent”。

你不是摘要器。
你不是提纲生成器。
你的任务是提取原文里的“内容支撑原子”，不是把原文讲短。

必须遵守：
1. 不要总结原文。
2. 不要压缩成大纲。
3. 不要为了短而删除细节。
4. 要尽可能保留原文中的推理支撑。
5. 每个数字、对比、因果、机制、例子、类比、历史转折，都应成为独立 content_atom。
6. 如果一个句子同时包含事实、因果和类比，要拆成多个 atom。
7. 标记 must_keep。
8. 标记 compression_allowed。
9. 输出 causal_chain，说明这些 atom 如何构成推理链。
10. 不要新增原文没有的史实。
11. 不要把幽默类比误判为可以删除的废话。很多类比是观众理解机制的关键支撑。
12. 数字对比、机制解释、关键例子、关键类比、历史转折默认 must_keep=true。
13. 如果无法判断某个点是否重要，默认保留。

输出严格 JSON object，结构必须是：
{{
  "content_atoms": [
    {{
      "atom_id": "P01-A01",
      "source_paragraph": 1,
      "atom_type": "fact | number | comparison | cause_effect | mechanism | analogy | example | transition | claim | consequence | visual_hook",
      "text": "从原文中抽取出的不可丢失内容",
      "reasoning_role": "premise | evidence | mechanism | consequence | contrast | turning_point | audience_hook | conclusion",
      "must_keep": true,
      "compression_allowed": false,
      "visual_potential": "这个内容适合如何视觉化",
      "narrator_hint": "这个内容适合讲述人怎么讲"
    }}
  ],
  "causal_chain": [
    {{
      "step_id": "C01",
      "from_atoms": ["P01-A01"],
      "to_atoms": ["P01-A02"],
      "logic": "这些内容原子之间的因果或论证关系"
    }}
  ],
  "retention_requirements": {{
    "must_keep_atom_ids": [],
    "minimum_retention_ratio": 0.92,
    "forbidden_loss_types": ["数字对比", "因果链", "机制解释", "关键例子", "关键类比", "转折点", "结论代价"]
  }}
}}

原文：
{json.dumps(source, ensure_ascii=False, indent=2)}

输出严格 json object。
""".strip()


def build_narrator_scene_adaptation_prompt(
    article: str,
    content_atoms_payload: dict[str, Any],
    title: str | None = None,
) -> str:
    normalized_atoms = normalize_content_atoms_payload(content_atoms_payload, fallback_article=article)
    source = {
        "title": title or "",
        "article": article or "",
        "content_atoms": normalized_atoms["content_atoms"],
        "causal_chain": normalized_atoms["causal_chain"],
        "retention_requirements": normalized_atoms["retention_requirements"],
    }
    return f"""
你不是摘要器。
你不是传统短剧编剧。
你正在改造的是“讲述人驱动的 AI 科普漫剧分场脚本”。

必须遵守：
1. 不要把原文改短。
2. 不要把原文改成提纲。
3. 不要只保留历史主线。
4. 不要为了短而删掉推理链。
5. 原文中的数字、对比、因果、机制、例子、类比、关键转折，必须进入 scene_script。
6. 每个场景不只是讲发生了什么，还必须讲为什么重要。
7. 场景不是原文自然段，而是一个“知识推理单元 + 视觉表达单元”。
8. 每个场景只完成一个主要知识任务，但这个知识任务必须包含完整支撑链。
9. 一个场景可以包含多个 content_atoms，只要它们共同服务同一个推理点。
10. 所有解释性、知识性、因果性、总结性、吐槽性内容由讲述人说。
11. 历史人物只能做画面演绎，不能承担现代知识解释。
12. 每个 scene 必须引用 source_atoms。
13. must_keep=true 的 atom 必须被某个 scene 引用。
14. 如果内容太多，优先增加场景数量，不要删除 must_keep atoms。
15. 不要按原文自然段机械切场。
16. 时间变化、地点变化、知识任务变化、视觉形式变化、情绪功能变化时，必须考虑切场。
17. 每个 scene 必须包含 scene_type、source_atoms、knowledge_payload、narrator_lines、visual_layer、screen_text、fact_boundary。
18. 不要新增原文没有的史实。
19. 如果必须进行合理场景化，必须在 fact_boundary 里说明。
20. historical_character_dialogue 只能少量出现，只能是生活化短句，不能承担知识解释。
21. 如果一句台词听起来像现代科普解释，必须放入 narrator_lines，而不是 historical_character_dialogue。

失败判定：
- 每场没有 reasoning_chain，判定失败或低分。
- 每场没有 must_keep_details，判定失败或低分。
- 大量数字、对比、例子、类比被删除，判定失败或低分。
- 场景只回答“发生了什么”，没有回答“为什么重要”，判定失败或低分。
- content_atoms 覆盖率低于 90%，判定失败或低分。
- 历史人物开始讲现代知识，判定失败或低分。

scene_type 只能从以下类型中选择，可用 “ + ” 组合：
- HOST_OPENING
- HOST_EXPLANATION
- MAP_ANIMATION
- TIMELINE_CARD
- HISTORICAL_REENACTMENT
- INFOGRAPHIC
- SYMBOLIC_MONTAGE
- COMPARISON_SPLIT_SCREEN
- TRANSITION_CARD
- THEME_CALLBACK

输出严格 JSON object，结构必须是：
{{
  "title": "分场脚本标题",
  "format": "narrator_led_science_comic",
  "narrator_profile": {{
    "role": "虚拟科普老师 / 旁白 / 字幕讲述人",
    "tone": "幽默、清楚、有纪录片感",
    "visual_presence": "avatar | voice_only | subtitle_only | mixed"
  }},
  "episode_structure": {{
    "main_question": "这集要回答的核心问题",
    "core_thesis": "观众看完后要记住的核心观点",
    "target_duration_min": 8,
    "estimated_scene_count": 16
  }},
  "scene_script": [
    {{
      "scene_id": "S01",
      "scene_title": "场景标题",
      "scene_type": "HOST_EXPLANATION + INFOGRAPHIC",
      "duration_sec": 25,
      "beat_function": "问题提出 / 困境建立 / 机制解释 / 历史转折 / 结果展示 / 代价揭示 / 主题回扣",
      "source_atoms": ["P02-A01", "P02-A02"],
      "knowledge_payload": {{
        "core_question": "这一场回答什么问题",
        "reasoning_chain": "这一场内部的因果链",
        "must_keep_details": ["不能丢的数字、例子、对比、机制"],
        "audience_takeaway": "观众看完这一场应该明白什么"
      }},
      "narrator_lines": ["讲述人台词"],
      "visual_layer": {{
        "main_visual": "画面主内容",
        "character_action": "历史人物或群体做什么；没有就写无",
        "environment": "时间、地点、环境氛围",
        "camera": "镜头组织方式",
        "animation_logic": "地图、箭头、字幕、图解、符号如何动起来",
        "transition": "如何进入下一场"
      }},
      "screen_text": ["关键词"],
      "historical_character_dialogue": [
        {{
          "speaker": "角色名",
          "line": "极短生活化台词",
          "purpose": "气氛/幽默/情绪",
          "evidence_level": "合理场景化"
        }}
      ],
      "audio_hint": "旁白、环境音、音乐或音效建议",
      "fact_boundary": "史实明确支持 | 合理场景化 | 需人工核对",
      "next_scene_hook": "下一场钩子"
    }}
  ],
  "adaptation_notes": [],
  "review_notes": [],
  "content_atoms": [],
  "causal_chain": [],
  "retention_review": {{}},
  "scene_review": {{}},
  "adapted_article": "",
  "adapted_segments": []
}}

输入：
{json.dumps(source, ensure_ascii=False, indent=2)}
""".strip()


def build_narrator_scene_plan_prompt(payload: dict[str, Any]) -> str:
    content_atoms_payload = normalize_content_atoms_payload(payload, fallback_article=str(payload.get("article") or ""))
    source = {
        "title": payload.get("title") or "",
        "topic": payload.get("topic") or "",
        "time_range": payload.get("time_range") or "",
        "article": payload.get("article") or "",
        "content_atoms": content_atoms_payload["content_atoms"],
        "causal_chain": content_atoms_payload["causal_chain"],
        "retention_requirements": content_atoms_payload["retention_requirements"],
    }
    return f"""
你是讲述人驱动科普漫剧的分场规划 Agent。

任务：
- 不要写最终旁白。
- 只规划场景。
- 每个 scene 必须引用 source_atoms。
- must_keep=true 的 atom 必须全部进入某个 scene。
- 如果内容太多，优先增加场景数量，不要删除 must_keep atom。
- 不要按原文自然段机械分场。
- 场景是“知识推理单元 + 视觉表达单元”。
- 每个场景都要回答“发生了什么”和“为什么重要”。
- 不要新增原文没有的史实。

输出严格 json object，结构必须是：
{{
  "scene_plan": [
    {{
      "scene_id": "S01",
      "scene_title": "场景标题",
      "scene_type": "HOST_OPENING + MAP_ANIMATION",
      "beat_function": "开场钩子 / 困境建立 / 机制解释 / 历史转折 / 结果展示 / 代价揭示 / 主题回扣",
      "source_atoms": ["P01-A01", "P01-A02"],
      "knowledge_task": "这一场要完成的知识任务",
      "reasoning_goal": "这一场要讲清的为什么",
      "must_keep_details": ["本场必须保留的数字、因果、类比或例子"],
      "visual_strategy": "地图 / 图解 / 历史重现 / 蒙太奇 / 对比画面",
      "estimated_duration_sec": 25
    }}
  ],
  "episode_structure": {{
    "main_question": "",
    "core_thesis": "",
    "estimated_scene_count": 0,
    "target_duration_min": 10
  }}
}}

输入：
{json.dumps(source, ensure_ascii=False, indent=2)}
""".strip()


def build_narrator_scene_batch_prompt(payload: dict[str, Any]) -> str:
    source = {
        "title": payload.get("title") or "",
        "article": payload.get("article") or "",
        "content_atoms": normalize_content_atoms(payload.get("content_atoms")),
        "causal_chain": normalize_causal_chain(payload.get("causal_chain")),
        "episode_structure": payload.get("episode_structure") if isinstance(payload.get("episode_structure"), dict) else {},
        "scene_plan": normalize_scene_plan(payload.get("scene_plan")),
    }
    return f"""
你不是摘要器。
你不是传统短剧编剧。
你正在根据 scene_plan 写“讲述人驱动的 AI 科普漫剧分场脚本”的一批场景。

硬规则：
- 不要为了短而删掉推理链。
- 不要只保留历史主线。
- 每个场景不只是讲“发生了什么”，还必须讲“为什么重要”。
- 每个场景必须引用 source_atoms。
- must_keep_details 必须进入 narrator_lines、visual_layer 或 screen_text。
- 所有解释性、知识性、因果性、总结性、吐槽性内容由讲述人说。
- 历史人物只能做画面演绎，不能承担现代知识解释。
- historical_character_dialogue 只能少量出现，只能是生活化短句。
- 如果一句台词听起来像现代科普解释，必须放入 narrator_lines。
- 不要新增原文没有的史实。
- 输出严格 json object。

输出结构：
{{
  "scene_script": []
}}

输入：
{json.dumps(source, ensure_ascii=False, indent=2)}
""".strip()


def build_repair_missing_atoms_prompt(payload: dict[str, Any]) -> str:
    source = {
        "original_payload": payload.get("original_payload") or {},
        "retention_review": payload.get("retention_review") or {},
        "missing_must_keep_atoms": normalize_string_list(payload.get("missing_must_keep_atoms")),
        "content_atoms": normalize_content_atoms(payload.get("content_atoms")),
        "scene_plan": normalize_scene_plan(payload.get("scene_plan")),
    }
    return f"""
你是分场脚本内容保真修复 Agent。

修复规则：
- 不要重写全片。
- 只补 missing_must_keep_atoms。
- 可以新增 scene，也可以补充已有 scene 的 narrator_lines、knowledge_payload、screen_text 和 visual_layer。
- 不要删除已有合格场景。
- 每个新增或修复场景必须引用 source_atoms。
- 不要新增原文没有的史实。
- 输出严格 json object。

输出结构：
{{
  "scene_script": []
}}

输入：
{json.dumps(source, ensure_ascii=False, indent=2)}
""".strip()


def normalize_content_atoms_payload(payload: Any, fallback_article: str = "") -> dict[str, Any]:
    payload = normalize_json_object(payload)
    content_atoms = normalize_content_atoms(payload.get("content_atoms"))
    causal_chain = normalize_causal_chain(payload.get("causal_chain"))
    requirements = payload.get("retention_requirements") if isinstance(payload.get("retention_requirements"), dict) else {}
    must_keep_atom_ids = normalize_string_list(requirements.get("must_keep_atom_ids"))
    if not must_keep_atom_ids:
        must_keep_atom_ids = [atom["atom_id"] for atom in content_atoms if atom.get("must_keep")]
    return {
        "content_atoms": content_atoms,
        "causal_chain": causal_chain,
        "retention_requirements": {
            "must_keep_atom_ids": must_keep_atom_ids,
            "minimum_retention_ratio": normalize_ratio(requirements.get("minimum_retention_ratio"), default=0.92),
            "forbidden_loss_types": normalize_string_list(requirements.get("forbidden_loss_types"))
            or list(DEFAULT_FORBIDDEN_LOSS_TYPES),
        },
        "source_article_excerpt": str(payload.get("source_article_excerpt") or fallback_article or "")[:1200],
    }


def normalize_content_atoms(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    atoms: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            continue
        source_paragraph = normalize_int(item.get("source_paragraph"), default=0)
        atom_id = str(item.get("atom_id") or "").strip()
        if not atom_id:
            atom_id = f"P{source_paragraph:02d}-A{index:02d}" if source_paragraph else f"A{index:03d}"
        atom_type = str(item.get("atom_type") or "").strip()
        if atom_type not in ATOM_TYPES:
            atom_type = "claim"
        reasoning_role = str(item.get("reasoning_role") or "").strip()
        if reasoning_role not in REASONING_ROLES:
            reasoning_role = "premise"
        atoms.append(
            {
                "atom_id": atom_id,
                "source_paragraph": source_paragraph,
                "atom_type": atom_type,
                "text": str(item.get("text") or "").strip(),
                "reasoning_role": reasoning_role,
                "must_keep": normalize_bool(item.get("must_keep"), default=True),
                "compression_allowed": normalize_bool(item.get("compression_allowed"), default=False),
                "visual_potential": str(item.get("visual_potential") or ""),
                "narrator_hint": str(item.get("narrator_hint") or ""),
            }
        )
    return atoms


def normalize_causal_chain(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    chain: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if isinstance(item, str):
            item = {"logic": item}
        if not isinstance(item, dict):
            continue
        chain.append(
            {
                "step_id": str(item.get("step_id") or f"C{index:02d}"),
                "from_atoms": normalize_string_list(item.get("from_atoms")),
                "to_atoms": normalize_string_list(item.get("to_atoms")),
                "logic": str(item.get("logic") or ""),
            }
        )
    return chain


def normalize_scene_plan_payload(payload: Any) -> dict[str, Any]:
    payload = normalize_json_object(payload)
    scene_plan = normalize_scene_plan(payload.get("scene_plan"))
    episode_structure = payload.get("episode_structure") if isinstance(payload.get("episode_structure"), dict) else {}
    return {
        "scene_plan": scene_plan,
        "episode_structure": {
            "main_question": str(episode_structure.get("main_question") or ""),
            "core_thesis": str(episode_structure.get("core_thesis") or ""),
            "estimated_scene_count": normalize_int(
                episode_structure.get("estimated_scene_count"),
                default=len(scene_plan),
            ),
            "target_duration_min": normalize_int(episode_structure.get("target_duration_min"), default=10),
        },
    }


def normalize_scene_plan(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    plan: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        plan.append(
            {
                "scene_id": str(item.get("scene_id") or f"S{index:02d}"),
                "scene_title": str(item.get("scene_title") or item.get("title") or "未命名场景"),
                "scene_type": normalize_scene_type(item.get("scene_type")),
                "beat_function": str(item.get("beat_function") or ""),
                "source_atoms": normalize_string_list(item.get("source_atoms")),
                "knowledge_task": str(item.get("knowledge_task") or ""),
                "reasoning_goal": str(item.get("reasoning_goal") or ""),
                "must_keep_details": normalize_string_list(item.get("must_keep_details")),
                "visual_strategy": str(item.get("visual_strategy") or ""),
                "estimated_duration_sec": normalize_int(item.get("estimated_duration_sec"), default=25),
            }
        )
    return plan


def normalize_narrator_scene_script_payload(payload: dict[str, Any], *, fallback_article: str) -> dict[str, Any]:
    payload = normalize_json_object(payload)

    scene_script = normalize_scene_script(payload.get("scene_script"))
    if not scene_script:
        scene_script = legacy_adapted_segments_to_scene_script(payload.get("adapted_segments"))

    scene_texts = []
    for scene in scene_script:
        lines = [str(line).strip() for line in scene.get("narrator_lines", []) if str(line).strip()]
        if lines:
            scene_texts.append("\n".join(lines))
    adapted_article = "\n\n".join(scene_texts).strip()
    if not adapted_article:
        adapted_article = str(payload.get("adapted_article") or "").strip() or fallback_article

    adapted_segments = scene_script_to_legacy_adapted_segments(scene_script)
    if not adapted_segments:
        adapted_segments = normalize_adapted_segments(payload.get("adapted_segments"))

    content_atoms_payload = normalize_content_atoms_payload(payload, fallback_article=fallback_article)
    scene_plan_payload = normalize_scene_plan_payload(payload)
    narrator_profile = payload.get("narrator_profile") if isinstance(payload.get("narrator_profile"), dict) else {}
    episode_structure = payload.get("episode_structure") if isinstance(payload.get("episode_structure"), dict) else {}
    normalized = {
        "title": str(payload.get("title") or "分场脚本"),
        "format": "narrator_led_science_comic",
        "narrator_profile": {
            "role": str(narrator_profile.get("role") or "虚拟科普老师 / 旁白 / 字幕讲述人"),
            "tone": str(narrator_profile.get("tone") or "幽默、清楚、有纪录片感"),
            "visual_presence": normalize_visual_presence(narrator_profile.get("visual_presence")),
        },
        "episode_structure": {
            "main_question": str(episode_structure.get("main_question") or ""),
            "core_thesis": str(episode_structure.get("core_thesis") or ""),
            "target_duration_min": normalize_int(episode_structure.get("target_duration_min"), default=8),
            "estimated_scene_count": normalize_int(
                episode_structure.get("estimated_scene_count"),
                default=len(scene_script) or 16,
            ),
        },
        "scene_script": scene_script,
        "scene_plan": scene_plan_payload["scene_plan"],
        "content_atoms": content_atoms_payload["content_atoms"],
        "causal_chain": content_atoms_payload["causal_chain"],
        "retention_requirements": content_atoms_payload["retention_requirements"],
        "retention_review": {},
        "scene_review": {},
        "adapted_article": adapted_article,
        "adapted_segments": adapted_segments,
        "adaptation_notes": normalize_string_list(payload.get("adaptation_notes")),
        "review_notes": normalize_string_list(payload.get("review_notes")),
        "raw": payload,
    }
    normalized["retention_review"] = validate_content_retention(content_atoms_payload, scene_script)
    normalized["scene_review"] = validate_narrator_scene_script(normalized)
    return normalized


def normalize_storyboard_script_payload(payload: dict[str, Any], *, fallback_article: str) -> dict[str, Any]:
    return normalize_narrator_scene_script_payload(payload, fallback_article=fallback_article)


MANUAL_STORYBOARD_SECTION_TITLES = {
    "讲述人旁白": "narrator",
    "画面演绎": "visual",
    "屏幕文字": "screen_text",
    "历史人物对白": "dialogue",
    "保留支撑点": "support",
}


def parse_manual_storyboard_script(raw_text: str, title: str = "") -> dict[str, Any]:
    text = normalize_line_endings(raw_text).strip()
    if not text:
        raise ValueError("请先粘贴分镜剧本内容")
    scenes = parse_manual_storyboard_scenes(text)
    if not scenes:
        raise ValueError("没有识别到 S01｜... 这样的分镜场景标题")
    readable_text = scene_script_to_readable_storyboard_text(scenes)
    return {
        "title": title or "手动分镜剧本",
        "format": "manual_storyboard_script",
        "narrator_profile": {
            "role": "虚拟科普老师 / 旁白 / 字幕讲述人",
            "tone": "幽默、清楚、有纪录片感",
            "visual_presence": "mixed",
        },
        "episode_structure": {
            "main_question": "",
            "core_thesis": "",
            "target_duration_min": 10,
            "estimated_scene_count": len(scenes),
        },
        "scene_plan": [],
        "scene_script": scenes,
        "content_atoms": [],
        "causal_chain": [],
        "retention_review": {},
        "scene_review": validate_narrator_scene_script({"scene_script": scenes}),
        "adaptation_notes": ["用户粘贴分镜剧本，本地解析并格式化展示。"],
        "review_notes": [],
        "adapted_article": readable_text,
        "adapted_segments": scene_script_to_legacy_adapted_segments(scenes),
        "manual_raw_text": text,
        "created_at": current_timestamp(),
    }


def parse_manual_storyboard_scenes(text: str) -> list[dict[str, Any]]:
    matches = list(re.finditer(r"(?m)^(S\d{2,})\s*[｜|]\s*(.+?)\s*$", text))
    scenes = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        scenes.append(parse_manual_storyboard_scene(match.group(1), match.group(2), text[start:end], index + 1))
    return scenes


def parse_manual_storyboard_scene(scene_id: str, scene_title: str, body: str, index: int) -> dict[str, Any]:
    metadata: dict[str, str] = {}
    sections: dict[str, list[str]] = {value: [] for value in MANUAL_STORYBOARD_SECTION_TITLES.values()}
    current_section = ""
    for raw_line in normalize_line_endings(body).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line in MANUAL_STORYBOARD_SECTION_TITLES:
            current_section = MANUAL_STORYBOARD_SECTION_TITLES[line]
            continue
        if current_section:
            sections[current_section].append(line)
            continue
        key, value = split_manual_metadata_line(line)
        if key:
            metadata[key] = value

    support_lines = sections["support"]
    scene_type = metadata.get("场景类型", "") or "HOST_EXPLANATION"
    beat_function = metadata.get("功能", "")
    narrator_lines = sections["narrator"]
    visual_text = "\n".join(sections["visual"]).strip()
    screen_text = [line for line in sections["screen_text"] if line]
    return {
        "scene_id": scene_id or f"S{index:02d}",
        "scene_title": scene_title.strip() or "未命名场景",
        "scene_type": scene_type,
        "duration_sec": parse_duration_seconds(metadata.get("时长")),
        "beat_function": beat_function,
        "source_atoms": parse_source_refs(metadata.get("源稿", "")),
        "knowledge_payload": {
            "core_question": scene_title.strip(),
            "reasoning_chain": beat_function,
            "must_keep_details": support_lines,
            "audience_takeaway": support_lines[-1] if support_lines else "",
        },
        "narrator_lines": narrator_lines,
        "visual_layer": normalize_visual_layer({"main_visual": visual_text}),
        "screen_text": screen_text,
        "historical_character_dialogue": parse_manual_dialogue(sections["dialogue"]),
        "audio_hint": "",
        "fact_boundary": "用户提供分镜稿，需人工核对",
        "source_trace": normalize_string_list(metadata.get("源稿", "")),
        "next_scene_hook": "",
    }


def split_manual_metadata_line(line: str) -> tuple[str, str]:
    match = re.match(r"^([^：:]+)\s*[：:]\s*(.*)$", line)
    if not match:
        return "", ""
    return match.group(1).strip(), match.group(2).strip()


def parse_duration_seconds(value: Any) -> int:
    match = re.search(r"\d+", str(value or ""))
    if not match:
        return 25
    return int(match.group(0))


def parse_source_refs(value: str) -> list[str]:
    refs = re.findall(r"\[([^\]]+)\]", str(value or ""))
    if refs:
        return [ref.strip() for ref in refs if ref.strip()]
    return normalize_string_list(value)


def parse_manual_dialogue(lines: list[str]) -> list[dict[str, str]]:
    compact = "".join(lines).strip()
    if not compact or compact in {"无", "无。", "无对白", "无对白。"}:
        return []
    dialogue = []
    pending_speaker = ""
    for line in lines:
        if line in {"无", "无。"}:
            continue
        speaker_match = re.match(r"^(.+?)[：:]\s*(.*)$", line)
        if speaker_match:
            pending_speaker = speaker_match.group(1).strip()
            spoken = speaker_match.group(2).strip().strip("“”")
            if spoken:
                dialogue.append(
                    {
                        "speaker": pending_speaker or "角色",
                        "line": spoken,
                        "purpose": "气氛/幽默/情绪",
                        "evidence_level": "合理场景化",
                    }
                )
            continue
        dialogue.append(
            {
                "speaker": pending_speaker or "角色",
                "line": line.strip("“”"),
                "purpose": "气氛/幽默/情绪",
                "evidence_level": "合理场景化",
            }
        )
    return [item for item in dialogue if item["line"]]


def scene_script_to_readable_storyboard_text(scene_script: Any) -> str:
    scenes = normalize_scene_script(scene_script)
    chunks = []
    for scene in scenes:
        chunks.append(format_readable_storyboard_scene(scene))
    return "\n\n".join(chunks).strip()


def format_readable_storyboard_scene(scene: dict[str, Any]) -> str:
    visual_layer = scene.get("visual_layer") if isinstance(scene.get("visual_layer"), dict) else {}
    knowledge_payload = normalize_knowledge_payload(scene.get("knowledge_payload"))
    source_refs = " ".join(f"[{source_atom}]" for source_atom in normalize_string_list(scene.get("source_atoms"))) or "无"
    parts = [
        f"{scene.get('scene_id') or ''}｜{scene.get('scene_title') or '未命名场景'}".strip(),
        "",
        f"场景类型： {scene.get('scene_type') or 'HOST_EXPLANATION'}",
        f"功能： {scene.get('beat_function') or ''}",
        f"时长： {normalize_int(scene.get('duration_sec'), default=25)} 秒",
        f"源稿： {source_refs}",
        "",
        "讲述人旁白",
        "",
        "\n\n".join(normalize_string_list(scene.get("narrator_lines"))) or "无。",
        "",
        "画面演绎",
        "",
        readable_visual_layer_text(visual_layer) or "无。",
        "",
        "屏幕文字",
        "\n".join(normalize_string_list(scene.get("screen_text"))) or "无。",
        "",
        "历史人物对白",
        "",
        readable_dialogue_text(scene.get("historical_character_dialogue")) or "无。",
        "",
        "保留支撑点",
        "\n".join(knowledge_payload["must_keep_details"]) or "无。",
    ]
    return "\n".join(parts).strip()


def readable_visual_layer_text(visual_layer: dict[str, Any]) -> str:
    values = []
    for key in VISUAL_LAYER_KEYS:
        value = str(visual_layer.get(key) or "").strip()
        if value and value not in values:
            values.append(value)
    return "\n\n".join(values)


def readable_dialogue_text(value: Any) -> str:
    dialogue = normalize_historical_character_dialogue(value)
    if not dialogue:
        return ""
    lines = []
    for item in dialogue:
        speaker = item.get("speaker") or "角色"
        line = item.get("line") or ""
        lines.append(f"{speaker}：{line}")
    return "\n".join(lines)


def normalize_line_endings(value: str) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n")


def normalize_scene_script(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    scenes = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        narrator_lines = normalize_string_list(item.get("narrator_lines") or item.get("voiceover") or item.get("narration"))
        visual_layer = normalize_visual_layer(item.get("visual_layer") or item.get("visual_goal"))
        knowledge_payload = normalize_knowledge_payload(item.get("knowledge_payload"))
        scenes.append(
            {
                "scene_id": str(item.get("scene_id") or f"S{index:02d}"),
                "scene_title": str(item.get("scene_title") or item.get("title") or "未命名场景"),
                "scene_type": normalize_scene_type(item.get("scene_type") or item.get("scene_intent")),
                "duration_sec": normalize_int(item.get("duration_sec"), default=25),
                "beat_function": str(item.get("beat_function") or item.get("dramatic_function") or ""),
                "source_atoms": normalize_string_list(item.get("source_atoms")),
                "knowledge_payload": knowledge_payload,
                "knowledge_point": str(item.get("knowledge_point") or knowledge_payload.get("core_question") or ""),
                "narrator_lines": narrator_lines,
                "visual_layer": visual_layer,
                "screen_text": normalize_string_list(item.get("screen_text")),
                "historical_character_dialogue": normalize_historical_character_dialogue(
                    item.get("historical_character_dialogue") or item.get("dialogue")
                ),
                "audio_hint": str(item.get("audio_hint") or ""),
                "fact_boundary": str(item.get("fact_boundary") or "需人工核对"),
                "source_trace": normalize_string_list(item.get("source_trace")),
                "next_scene_hook": str(item.get("next_scene_hook") or item.get("continuity_hint") or ""),
            }
        )
    return scenes


def normalize_knowledge_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    return {
        "core_question": str(value.get("core_question") or ""),
        "reasoning_chain": str(value.get("reasoning_chain") or ""),
        "must_keep_details": normalize_string_list(value.get("must_keep_details")),
        "audience_takeaway": str(value.get("audience_takeaway") or ""),
    }


def normalize_visual_layer(value: Any) -> dict[str, str]:
    if isinstance(value, str):
        value = {"main_visual": value}
    if not isinstance(value, dict):
        value = {}
    visual_layer = {key: str(value.get(key) or "") for key in VISUAL_LAYER_KEYS}
    if not visual_layer["main_visual"]:
        visual_layer["main_visual"] = str(value.get("visual_goal") or value.get("description") or "").strip()
    if not visual_layer["animation_logic"]:
        visual_layer["animation_logic"] = str(value.get("visual_progression") or "").strip()
    return visual_layer


def normalize_historical_character_dialogue(value: Any) -> list[dict[str, str]]:
    if isinstance(value, str):
        value = [{"line": value}]
    if not isinstance(value, list):
        return []
    dialogue = []
    for item in value:
        if isinstance(item, str):
            item = {"line": item}
        if not isinstance(item, dict):
            continue
        line = str(item.get("line") or "").strip()
        speaker = str(item.get("speaker") or "角色").strip()
        if not line and not speaker:
            continue
        dialogue.append(
            {
                "speaker": speaker or "角色",
                "line": line,
                "purpose": str(item.get("purpose") or ""),
                "evidence_level": str(item.get("evidence_level") or ""),
            }
        )
    return dialogue


def scene_script_to_legacy_adapted_segments(scene_script: Any) -> list[dict[str, str]]:
    scenes = normalize_scene_script(scene_script)
    segments = []
    for index, scene in enumerate(scenes, start=1):
        voiceover = "\n".join(scene.get("narrator_lines") or []).strip()
        if not voiceover:
            continue
        visual_layer = scene.get("visual_layer") if isinstance(scene.get("visual_layer"), dict) else {}
        segments.append(
            {
                "segment_id": f"seg-{index:03d}",
                "voiceover": voiceover,
                "dramatic_function": str(scene.get("beat_function") or ""),
                "visual_goal": str(visual_layer.get("main_visual") or ""),
                "visual_progression": str(visual_layer.get("animation_logic") or visual_layer.get("transition") or ""),
                "scene_intent": " + ".join(
                    part
                    for part in [
                        str(scene.get("scene_type") or ""),
                        str((scene.get("knowledge_payload") or {}).get("core_question") or ""),
                    ]
                    if part
                ),
                "continuity_hint": str(scene.get("next_scene_hook") or visual_layer.get("transition") or ""),
                "fact_boundary": str(scene.get("fact_boundary") or ""),
            }
        )
    return segments


def legacy_adapted_segments_to_scene_script(value: Any) -> list[dict[str, Any]]:
    segments = normalize_adapted_segments(value)
    scenes = []
    for index, segment in enumerate(segments, start=1):
        scenes.append(
            {
                "scene_id": f"S{index:02d}",
                "scene_title": f"场景 {index}",
                "scene_type": normalize_scene_type(segment.get("scene_intent")),
                "duration_sec": 25,
                "beat_function": segment.get("dramatic_function", ""),
                "source_atoms": [],
                "knowledge_payload": {
                    "core_question": "",
                    "reasoning_chain": "",
                    "must_keep_details": [],
                    "audience_takeaway": "",
                },
                "knowledge_point": "",
                "narrator_lines": normalize_string_list(segment.get("voiceover")),
                "visual_layer": normalize_visual_layer(
                    {
                        "main_visual": segment.get("visual_goal", ""),
                        "animation_logic": segment.get("visual_progression", ""),
                        "transition": segment.get("continuity_hint", ""),
                    }
                ),
                "screen_text": [],
                "historical_character_dialogue": [],
                "audio_hint": "",
                "fact_boundary": segment.get("fact_boundary", "") or "需人工核对",
                "source_trace": [],
                "next_scene_hook": segment.get("continuity_hint", ""),
            }
        )
    return scenes


def merge_repaired_scenes(original_payload: dict[str, Any], repair_payload: dict[str, Any]) -> dict[str, Any]:
    original = normalize_narrator_scene_script_payload(
        original_payload,
        fallback_article=str(original_payload.get("adapted_article") or ""),
    )
    repaired_scenes = normalize_scene_script((repair_payload or {}).get("scene_script"))
    if not repaired_scenes:
        return original

    by_id = {scene["scene_id"]: scene for scene in original.get("scene_script", [])}
    order = [scene["scene_id"] for scene in original.get("scene_script", [])]
    for scene in repaired_scenes:
        scene_id = scene["scene_id"]
        by_id[scene_id] = scene
        if scene_id not in order:
            order.append(scene_id)
    merged = {
        **original,
        "scene_script": [by_id[scene_id] for scene_id in order],
        "repair_notes": normalize_string_list((repair_payload or {}).get("repair_notes")),
    }
    return normalize_narrator_scene_script_payload(merged, fallback_article=original.get("adapted_article") or "")


def validate_content_retention(
    content_atoms_payload: dict[str, Any],
    scene_script: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized_atoms = normalize_content_atoms_payload(content_atoms_payload)
    atoms = normalized_atoms["content_atoms"]
    requirements = normalized_atoms["retention_requirements"]
    scenes = normalize_scene_script(scene_script)
    issues: list[dict[str, str]] = []

    if not atoms:
        issues.append(
            {
                "severity": "major",
                "category": "coverage",
                "description": "content_atoms 为空，无法确认原文推理细节是否被保留。",
                "suggestion": "先从原文提取内容原子，再生成讲述人分场脚本。",
            }
        )
    if not scenes:
        issues.append(
            {
                "severity": "critical",
                "category": "structure",
                "description": "scene_script 为空，无法检查内容保真。",
                "suggestion": "生成至少一个引用 source_atoms 的分场。",
            }
        )

    covered_atoms: set[str] = set()
    reasoning_missing_count = 0
    combined_scene_text_parts: list[str] = []
    scene_with_source_count = 0
    for scene in scenes:
        source_atoms = set(normalize_string_list(scene.get("source_atoms")))
        if source_atoms:
            scene_with_source_count += 1
        covered_atoms.update(source_atoms)
        knowledge_payload = normalize_knowledge_payload(scene.get("knowledge_payload"))
        if not knowledge_payload["reasoning_chain"]:
            reasoning_missing_count += 1
        combined_scene_text_parts.extend(normalize_string_list(scene.get("narrator_lines")))
        combined_scene_text_parts.extend(normalize_string_list(scene.get("screen_text")))

    if scenes and scene_with_source_count == 0:
        issues.append(
            {
                "severity": "critical",
                "category": "source_atoms",
                "description": "scene_script 中所有 scene 的 source_atoms 都为空。",
                "suggestion": "每个 scene 必须引用它覆盖的 content_atoms。",
            }
        )

    atom_ids = {atom["atom_id"] for atom in atoms}
    covered_known_atoms = covered_atoms & atom_ids
    coverage_ratio = round(len(covered_known_atoms) / len(atom_ids), 4) if atom_ids else 0.0
    minimum_retention_ratio = normalize_ratio(requirements.get("minimum_retention_ratio"), default=0.92)

    must_keep_ids = set(requirements.get("must_keep_atom_ids") or [])
    must_keep_ids.update(atom["atom_id"] for atom in atoms if atom.get("must_keep"))
    missing_must_keep_atoms = sorted(must_keep_ids - covered_known_atoms)
    if missing_must_keep_atoms:
        issues.append(
            {
                "severity": "major",
                "category": "coverage",
                "description": f"有 {len(missing_must_keep_atoms)} 个 must_keep content_atoms 未被 scene_script 覆盖。",
                "suggestion": "为缺失 atom 增加场景，或把它们加入相关 scene 的 source_atoms 和台词支撑。",
            }
        )

    if atoms and coverage_ratio < minimum_retention_ratio:
        issues.append(
            {
                "severity": "major",
                "category": "coverage",
                "description": f"content_atoms 覆盖率 {coverage_ratio:.2f} 低于要求 {minimum_retention_ratio:.2f}。",
                "suggestion": "优先增加场景数量，不要删除 must_keep atoms。",
            }
        )

    missing_loss_types = sorted(
        {
            atom["atom_type"]
            for atom in atoms
            if atom.get("atom_type") in DETAIL_LOSS_ATOM_TYPES and atom.get("atom_id") not in covered_known_atoms
        }
    )
    for atom_type in missing_loss_types:
        issues.append(
            {
                "severity": "major",
                "category": "detail_loss",
                "description": f"{atom_type} 类型 content_atoms 未被覆盖，可能丢失推理支撑。",
                "suggestion": "把数字对比、机制解释、因果、例子或类比写入对应 scene。",
            }
        )

    if scenes and reasoning_missing_count > len(scenes) / 2:
        issues.append(
            {
                "severity": "major",
                "category": "reasoning",
                "description": "多数 scene 缺少 knowledge_payload.reasoning_chain。",
                "suggestion": "每场都要说明这个知识单元内部的因果链或论证链。",
            }
        )

    combined_scene_text = "\n".join(combined_scene_text_parts)
    for atom in atoms:
        if atom["atom_id"] not in covered_known_atoms:
            continue
        numbers = re.findall(r"\d+(?:\.\d+)?%?|\d+(?:\.\d+)?", atom.get("text") or "")
        if numbers and not any(number in combined_scene_text for number in numbers):
            issues.append(
                {
                    "severity": "major",
                    "category": "detail_loss",
                    "description": f"{atom['atom_id']} 含有数字信息，但 narrator_lines 和 screen_text 未体现这些数字。",
                    "suggestion": "把关键数字或比例放进讲述人台词或屏幕文字。",
                }
            )
            break

    analogy_example_atoms = [atom for atom in atoms if atom.get("atom_type") in {"analogy", "example"}]
    if analogy_example_atoms:
        covered_analogy_examples = [
            atom for atom in analogy_example_atoms if atom.get("atom_id") in covered_known_atoms
        ]
        if len(covered_analogy_examples) / len(analogy_example_atoms) < 0.5:
            issues.append(
                {
                    "severity": "major",
                    "category": "detail_loss",
                    "description": "analogy/example 类型 content_atoms 大量未覆盖，观众理解支撑可能被删掉。",
                    "suggestion": "保留关键类比和例子，尤其是帮助解释机制的幽默类比。",
                }
            )

    severity_counts = count_issue_severities(issues)
    if severity_counts["critical"]:
        score = 1
    elif atoms and (coverage_ratio < 0.8 or len(missing_must_keep_atoms) >= 3):
        score = 2
    elif severity_counts["major"]:
        score = 3
    elif coverage_ratio >= 0.9 and severity_counts["minor"]:
        score = 4
    elif coverage_ratio >= 0.95 and not severity_counts["major"]:
        score = 5
    else:
        score = 4
    passed = (
        not severity_counts["critical"]
        and not severity_counts["major"]
        and bool(atoms)
        and bool(scenes)
        and coverage_ratio >= minimum_retention_ratio
        and not missing_must_keep_atoms
    )
    return {
        "passed": passed,
        "score": score,
        "coverage_ratio": coverage_ratio,
        "missing_must_keep_atoms": missing_must_keep_atoms,
        "missing_loss_types": missing_loss_types,
        "issues": issues,
    }


def validate_narrator_scene_script(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    scenes = payload.get("scene_script") if isinstance(payload.get("scene_script"), list) else []
    issues: list[dict[str, str]] = []
    if not scenes:
        issues.append(
            {
                "severity": "critical",
                "category": "structure",
                "description": "scene_script 为空，无法作为分场脚本进入后续生产。",
                "suggestion": "至少生成一个讲述人驱动的视听表达单元。",
            }
        )

    narrator_chars = 0
    dialogue_chars = 0
    scene_types = []
    screen_text_empty_count = 0
    animation_logic_empty_count = 0
    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            continue
        scene_label = str(scene.get("scene_id") or f"S{index:02d}")
        narrator_lines = normalize_string_list(scene.get("narrator_lines"))
        narrator_chars += count_text_chars("".join(narrator_lines))
        if not narrator_lines:
            issues.append(
                {
                    "severity": "major",
                    "category": "narrator",
                    "description": f"{scene_label} 缺少 narrator_lines。",
                    "suggestion": "把解释、因果、吐槽和转场信息写入讲述人台词。",
                }
            )
        if len(narrator_lines) > 4:
            issues.append(
                {
                    "severity": "minor",
                    "category": "narrator",
                    "description": f"{scene_label} 的 narrator_lines 超过 4 句，可能没有真正分场。",
                    "suggestion": "把不同知识任务或情绪任务拆成多个场景。",
                }
            )

        visual_layer = scene.get("visual_layer") if isinstance(scene.get("visual_layer"), dict) else {}
        if not str(visual_layer.get("main_visual") or "").strip():
            issues.append(
                {
                    "severity": "major",
                    "category": "visual",
                    "description": f"{scene_label} 缺少 visual_layer.main_visual。",
                    "suggestion": "补充这一场最主要的画面内容。",
                }
            )
        if not normalize_string_list(scene.get("screen_text")):
            screen_text_empty_count += 1
            issues.append(
                {
                    "severity": "minor",
                    "category": "visual",
                    "description": f"{scene_label} 缺少 screen_text。",
                    "suggestion": "补充屏幕关键词、概念词、时间地点或转场卡。",
                }
            )
        if not str(scene.get("fact_boundary") or "").strip():
            issues.append(
                {
                    "severity": "minor",
                    "category": "fact_boundary",
                    "description": f"{scene_label} 缺少 fact_boundary。",
                    "suggestion": "标注史实明确支持、合理场景化或需人工核对。",
                }
            )
        if not normalize_string_list(scene.get("source_atoms")):
            issues.append(
                {
                    "severity": "major",
                    "category": "structure",
                    "description": f"{scene_label} 缺少 source_atoms。",
                    "suggestion": "每个 scene 必须引用它覆盖的 content_atoms。",
                }
            )
        knowledge_payload = normalize_knowledge_payload(scene.get("knowledge_payload"))
        if not knowledge_payload["reasoning_chain"]:
            issues.append(
                {
                    "severity": "major",
                    "category": "structure",
                    "description": f"{scene_label} 缺少 knowledge_payload.reasoning_chain。",
                    "suggestion": "写清这一场内部的因果链或论证链。",
                }
            )
        if not str(visual_layer.get("animation_logic") or "").strip():
            animation_logic_empty_count += 1

        dialogue = normalize_historical_character_dialogue(scene.get("historical_character_dialogue"))
        for line in dialogue:
            dialogue_text = line.get("line", "")
            dialogue_chars += count_text_chars(dialogue_text)
            matched_term = find_modern_explanation_term(dialogue_text)
            if matched_term:
                issues.append(
                    {
                        "severity": "major",
                        "category": "dialogue",
                        "description": f"{scene_label} 的历史人物台词出现现代科普解释词“{matched_term}”。",
                        "suggestion": "把现代知识解释移到 narrator_lines，历史人物只说生活化短句。",
                    }
                )
        scene_type = str(scene.get("scene_type") or "").strip()
        scene_types.append(scene_type)
        invalid_scene_types = [
            part.strip()
            for part in re.split(r"\s*\+\s*|\s*,\s*|，|、", scene_type)
            if part.strip() and part.strip() not in ALLOWED_NARRATOR_SCENE_TYPES
        ]
        if invalid_scene_types:
            issues.append(
                {
                    "severity": "minor",
                    "category": "scene_type",
                    "description": f"{scene_label} 包含不在允许列表中的 scene_type：{', '.join(invalid_scene_types)}。",
                    "suggestion": "使用 HOST_OPENING、HOST_EXPLANATION、MAP_ANIMATION、INFOGRAPHIC 等允许类型组合。",
                }
            )

    if dialogue_chars and dialogue_chars > narrator_chars * 0.25:
        issues.append(
            {
                "severity": "major",
                "category": "dialogue",
                "description": "historical_character_dialogue 总字数超过 narrator_lines 总字数的 25%。",
                "suggestion": "压缩历史人物台词，把解释性内容交还给讲述人。",
            }
        )

    for index in range(2, len(scene_types)):
        if scene_types[index] and scene_types[index] == scene_types[index - 1] == scene_types[index - 2]:
            issues.append(
                {
                    "severity": "minor",
                    "category": "continuity",
                    "description": f"{scene_types[index]} 连续出现 3 个场景，视觉节奏可能重复。",
                    "suggestion": "考虑加入地图、时间线、图解、蒙太奇或转场卡打破重复。",
                }
            )
            break

    if scenes and screen_text_empty_count == len(scenes):
        issues.append(
            {
                "severity": "major",
                "category": "visual",
                "description": "所有 scene 的 screen_text 都为空，输出更像文章而不是可生产分场。",
                "suggestion": "为每场补充屏幕关键词、数字、概念词或转场卡。",
            }
        )
    if scenes and animation_logic_empty_count == len(scenes):
        issues.append(
            {
                "severity": "major",
                "category": "visual",
                "description": "所有 scene 的 visual_layer.animation_logic 都为空。",
                "suggestion": "补充地图、箭头、图解、符号、字幕或转场如何运动。",
            }
        )
    if len(scenes) == 1 and len(normalize_string_list(scenes[0].get("narrator_lines"))) > 4:
        issues.append(
            {
                "severity": "major",
                "category": "structure",
                "description": "输出只有一个长场景，更像文章段落而不是结构化分场。",
                "suggestion": "按知识任务、视觉形式、情绪功能拆成多个 scene。",
            }
        )

    severity_counts = count_issue_severities(issues)
    if severity_counts["critical"]:
        score = 1
    else:
        score = 5 - severity_counts["major"] - (1 if severity_counts["minor"] >= 2 else 0)
        score = max(1, min(score, 5))
    return {
        "passed": not severity_counts["critical"] and not severity_counts["major"] and score >= 4,
        "score": score,
        "issues": issues,
    }


def normalize_visual_presence(value: Any) -> str:
    text = str(value or "").strip()
    if text in {"avatar", "voice_only", "subtitle_only", "mixed"}:
        return text
    return "mixed"


def normalize_scene_type(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "HOST_EXPLANATION"
    parts = [part.strip() for part in re.split(r"\s*\+\s*|\s*,\s*|，|、", text) if part.strip()]
    return " + ".join(parts) if parts else "HOST_EXPLANATION"


def normalize_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_ratio(value: Any, *, default: float) -> float:
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return default
    if ratio > 1:
        ratio = ratio / 100 if ratio <= 100 else 1
    if ratio <= 0:
        return default
    return min(ratio, 1.0)


def normalize_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "是", "对"}:
        return True
    if text in {"false", "0", "no", "n", "否", "不"}:
        return False
    return default


def normalize_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = parse_llm_json_object(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def count_issue_severities(issues: list[dict[str, str]]) -> dict[str, int]:
    return {
        "critical": sum(1 for item in issues if item.get("severity") == "critical"),
        "major": sum(1 for item in issues if item.get("severity") == "major"),
        "minor": sum(1 for item in issues if item.get("severity") == "minor"),
    }


def count_text_chars(value: str) -> int:
    return len(re.sub(r"\s+", "", str(value or "")))


def find_modern_explanation_term(value: str) -> str:
    compact = re.sub(r"\s+", "", str(value or ""))
    for term in MODERN_EXPLANATION_TERMS:
        if term in compact:
            return term
    return ""


def normalize_adapted_segments(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    segments = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        voiceover = str(item.get("voiceover") or item.get("narration") or "").strip()
        if not voiceover:
            continue
        segments.append(
            {
                "segment_id": str(item.get("segment_id") or f"seg-{index:03d}"),
                "voiceover": voiceover,
                "dramatic_function": str(item.get("dramatic_function") or ""),
                "visual_goal": str(item.get("visual_goal") or ""),
                "visual_progression": str(item.get("visual_progression") or ""),
                "scene_intent": str(item.get("scene_intent") or ""),
                "continuity_hint": str(item.get("continuity_hint") or ""),
                "fact_boundary": str(item.get("fact_boundary") or ""),
            }
        )
    return segments


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
