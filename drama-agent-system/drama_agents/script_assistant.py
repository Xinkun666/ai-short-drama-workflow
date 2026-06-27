from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from drama_agents.storage import MaterialDatabase
from drama_agents.vector_store import LocalVectorStore, build_material_chunks


SMALLTALK = "SMALLTALK"
EXPLAIN_SCRIPT = "EXPLAIN_SCRIPT"
EXPLAIN_SELECTION = "EXPLAIN_SELECTION"
REVIEW_SCRIPT = "REVIEW_SCRIPT"
REVIEW_SELECTION = "REVIEW_SELECTION"
PROPOSE_EDIT = "PROPOSE_EDIT"
REVISE_PENDING = "REVISE_PENDING"
APPLY_PATCH = "APPLY_PATCH"
REJECT_PATCH = "REJECT_PATCH"
ASK_SOURCE = "ASK_SOURCE"

KEEP_CURRENT = "KEEP_CURRENT"
SWITCH_TO_NEW = "SWITCH_TO_NEW"
USE_NEW_AS_REFERENCE = "USE_NEW_AS_REFERENCE"
COMPARE_CURRENT_AND_NEW = "COMPARE_CURRENT_AND_NEW"
ASK_CLARIFICATION = "ASK_CLARIFICATION"


ASSISTANT_TOOL_MANIFEST = (
    {
        "name": "plain_chat",
        "description": "普通问候、功能说明或不依赖选区的轻量对话；不检索 RAG，不生成候选修改。",
        "creates_patch": False,
        "can_persist": False,
    },
    {
        "name": "chat_with_selection",
        "description": "围绕当前选区做理解、解释、评价、节奏建议；默认不检索 RAG，不生成候选修改。",
        "creates_patch": False,
        "can_persist": False,
    },
    {
        "name": "search_sources",
        "description": "用户询问史实、来源、依据或资料支持时检索本地 RAG 并回答。",
        "creates_patch": False,
        "can_persist": False,
    },
    {
        "name": "propose_edit",
        "description": "用户明确要求改写、润色、调整或补充选区时检索本地 RAG 并生成待确认候选修改。",
        "creates_patch": True,
        "can_persist": False,
    },
    {
        "name": "apply_patch",
        "description": "用户明确确认某条候选修改时，由本地工具保存正文。",
        "creates_patch": False,
        "can_persist": True,
    },
    {
        "name": "reject_patch",
        "description": "用户放弃候选修改时，由本地工具关闭 pending patch。",
        "creates_patch": False,
        "can_persist": True,
    },
)


@dataclass(frozen=True)
class AssistantSelection:
    text: str = ""
    paragraph_id: str = ""
    start_offset: int | None = None
    end_offset: int | None = None

    @property
    def selection_id(self) -> str:
        return self.paragraph_id or text_hash(self.text)[:16]


@dataclass(frozen=True)
class AssistantRequest:
    message: str
    selection: AssistantSelection
    conversation_id: str = ""
    intent_hint: str = ""
    patch_id: int | None = None


@dataclass(frozen=True)
class AssistantPlan:
    intent: str
    tool: str = ""
    needs_rag: bool | None = None
    selection_policy: str = ""
    reason: str = ""
    source: str = "fallback"


@dataclass(frozen=True)
class FocusResolution:
    focus_action: str
    primary_selection: AssistantSelection
    reference_selection: AssistantSelection = AssistantSelection()
    active_selection: AssistantSelection = AssistantSelection()
    focus_reason: str = ""
    clarification_answer: str = ""


class ScriptAssistantIntentRouter:
    def classify(
        self,
        *,
        message: str,
        selection: AssistantSelection,
        intent_hint: str = "",
        patch_id: int | None = None,
        active_patch: dict[str, Any] | None = None,
    ) -> str:
        compact = compact_text(message)
        hint = intent_hint.strip().lower()
        has_selection = bool(selection.text.strip())
        if hint in {"apply", "apply_patch"} or (patch_id and has_apply_marker(compact)):
            return APPLY_PATCH
        if hint in {"reject", "reject_patch"}:
            return REJECT_PATCH
        if hint in {"edit", "rewrite"}:
            return PROPOSE_EDIT
        if hint == "review":
            return REVIEW_SELECTION if has_selection else REVIEW_SCRIPT
        if hint == "explain":
            return EXPLAIN_SELECTION if has_selection else EXPLAIN_SCRIPT
        if hint == "chat":
            return SMALLTALK

        if is_smalltalk(compact):
            return SMALLTALK
        if has_reject_marker(compact):
            return REJECT_PATCH
        if has_apply_marker(compact):
            return APPLY_PATCH
        if active_patch and has_revision_marker(compact):
            return REVISE_PENDING
        if has_source_marker(compact):
            return ASK_SOURCE
        if has_compare_marker(compact):
            return EXPLAIN_SELECTION if has_selection else EXPLAIN_SCRIPT
        if has_edit_marker(compact):
            return PROPOSE_EDIT if has_selection or active_patch else SMALLTALK
        if has_review_marker(compact):
            return REVIEW_SELECTION if has_selection else REVIEW_SCRIPT
        if has_explain_marker(compact):
            return EXPLAIN_SELECTION if has_selection else EXPLAIN_SCRIPT
        return SMALLTALK


class ScriptAssistantFocusResolver:
    def resolve(
        self,
        *,
        current_selection: AssistantSelection,
        new_selection: AssistantSelection,
        message: str,
        intent: str,
        recent_messages: list[dict[str, Any]],
        active_patch: dict[str, Any] | None = None,
    ) -> FocusResolution:
        compact = compact_text(message)
        current = current_selection if current_selection.text else selection_from_patch(active_patch)
        has_current = bool(current.text)
        has_new = bool(new_selection.text)

        if intent in {APPLY_PATCH, REJECT_PATCH}:
            return FocusResolution(
                focus_action=KEEP_CURRENT,
                primary_selection=current,
                active_selection=current,
                focus_reason="patch action keeps current discussion target",
            )

        if intent == SMALLTALK and is_smalltalk(compact):
            return FocusResolution(
                focus_action=KEEP_CURRENT,
                primary_selection=AssistantSelection(),
                active_selection=current,
                focus_reason="ordinary chat does not force a selection",
            )

        if has_new and has_current and has_compare_marker(compact):
            return FocusResolution(
                focus_action=COMPARE_CURRENT_AND_NEW,
                primary_selection=current,
                reference_selection=new_selection,
                active_selection=current,
                focus_reason="user asked to compare current and newly selected passages",
            )

        if has_new and has_current and has_reference_marker(compact):
            return FocusResolution(
                focus_action=USE_NEW_AS_REFERENCE,
                primary_selection=current,
                reference_selection=new_selection,
                active_selection=current,
                focus_reason="new selection is reference material for the current passage",
            )

        if has_new and has_current and active_patch and has_ambiguous_focus_marker(compact):
            return FocusResolution(
                focus_action=ASK_CLARIFICATION,
                primary_selection=current,
                reference_selection=new_selection,
                active_selection=current,
                focus_reason="ambiguous new selection while a pending patch exists",
                clarification_answer="你是想继续改刚才那段，还是切换到新选中的这一段？",
            )

        if has_current and (intent == REVISE_PENDING or has_continue_focus_marker(compact)):
            return FocusResolution(
                focus_action=KEEP_CURRENT,
                primary_selection=current,
                active_selection=current,
                focus_reason="user is continuing the current discussion",
            )

        if has_new and (not has_current or has_explicit_new_focus_marker(compact) or intent_uses_selection(intent)):
            return FocusResolution(
                focus_action=SWITCH_TO_NEW,
                primary_selection=new_selection,
                active_selection=new_selection,
                focus_reason="new selection becomes the main discussion target",
            )

        if has_current:
            return FocusResolution(
                focus_action=KEEP_CURRENT,
                primary_selection=current,
                active_selection=current,
                focus_reason="no new target; keep current discussion target",
            )

        return FocusResolution(
            focus_action=KEEP_CURRENT,
            primary_selection=AssistantSelection(),
            active_selection=AssistantSelection(),
            focus_reason="no active discussion target",
        )


class ScriptAssistantMemory:
    def __init__(self, database: MaterialDatabase, generation_id: str, conversation: dict[str, Any]):
        self.database = database
        self.generation_id = generation_id
        self.conversation_id = str(conversation.get("conversation_id") or "")
        self.state = conversation

    def active_patch(self) -> dict[str, Any] | None:
        patch_id = self.state.get("active_patch_id")
        if patch_id:
            patch = self.database.find_script_edit_patch(int(patch_id))
            if (
                patch
                and patch.get("generation_id") == self.generation_id
                and patch.get("conversation_id") == self.conversation_id
                and patch.get("status") == "pending"
            ):
                return patch
        patch = self.database.latest_pending_script_edit_patch(
            self.generation_id,
            conversation_id=self.conversation_id,
        )
        if patch and patch.get("status") == "pending":
            return patch
        return None

    def update(
        self,
        *,
        intent: str,
        selection: AssistantSelection | None = None,
        patch_id: int | None = None,
        article_version_hash: str = "",
        user_message: str = "",
        accepted_or_rejected: str = "",
    ) -> dict[str, Any]:
        preferences = list(self.state.get("style_preferences") or [])
        for preference in extract_style_preferences(user_message):
            if preference not in preferences:
                preferences.append(preference)
        summary_parts = []
        if intent:
            summary_parts.append(f"最近意图：{intent}")
        if accepted_or_rejected:
            summary_parts.append(accepted_or_rejected)
        if preferences:
            summary_parts.append("偏好：" + "、".join(preferences[-6:]))
        self.state = self.database.update_script_assistant_conversation_state(
            self.generation_id,
            self.conversation_id,
            active_selection_id=(selection.selection_id if selection and selection.text else self.state.get("active_selection_id", "")),
            active_selection_text=(selection.text if selection and selection.text else self.state.get("active_selection_text", "")),
            active_selection_hash=(
                text_hash(selection.text)
                if selection and selection.text
                else self.state.get("active_selection_hash", "")
            ),
            active_paragraph_id=(selection.paragraph_id if selection and selection.text else self.state.get("active_paragraph_id", "")),
            active_start_offset=(
                selection.start_offset if selection and selection.text else self.state.get("active_start_offset")
            ),
            active_end_offset=(
                selection.end_offset if selection and selection.text else self.state.get("active_end_offset")
            ),
            active_focus_reason=(
                f"{intent}: {selection.selection_id}" if selection and selection.text else self.state.get("active_focus_reason", "")
            ),
            active_patch_id=patch_id,
            session_summary="；".join(summary_parts),
            style_preferences=preferences[-12:],
        )
        return self.state


class ScriptAssistantController:
    def __init__(
        self,
        *,
        database: MaterialDatabase,
        script_agent,
        rag_database_path: Path,
        outputs_path: Path,
    ):
        self.database = database
        self.script_agent = script_agent
        self.rag_database_path = Path(rag_database_path)
        self.outputs_path = Path(outputs_path)
        self.router = ScriptAssistantIntentRouter()
        self.focus_resolver = ScriptAssistantFocusResolver()

    def handle(self, generation_id: str, raw_payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        request = normalize_assist_request(raw_payload)
        if not request.message:
            return {"error": "请输入你想让剧本对话助手做什么"}, 400
        generation = self.database.find_script_generation(generation_id)
        if not generation:
            raise KeyError(generation_id)
        if request.conversation_id:
            conversation = self.database.find_script_assistant_conversation(generation_id, request.conversation_id)
            if not conversation:
                return {"error": "对话不存在或已删除"}, 404
        else:
            conversation = self.database.create_script_assistant_conversation(generation_id)

        article = script_article(generation)
        article_hash = article_version_hash(article)
        memory = ScriptAssistantMemory(self.database, generation_id, conversation)
        active_patch = memory.active_patch()
        fallback_intent = self.router.classify(
            message=request.message,
            selection=request.selection,
            intent_hint=request.intent_hint,
            patch_id=request.patch_id,
            active_patch=active_patch,
        )
        recent_messages = self.database.list_script_assistant_messages(
            generation_id,
            conversation_id=conversation["conversation_id"],
            limit=12,
        )
        plan = self._plan_request(
            request=request,
            generation=generation,
            conversation=conversation,
            recent_messages=recent_messages,
            active_patch=active_patch,
            fallback_intent=fallback_intent,
        )
        intent = plan.intent
        focus = self.focus_resolver.resolve(
            current_selection=selection_from_conversation(conversation),
            new_selection=request.selection,
            message=request.message,
            intent=intent,
            recent_messages=recent_messages,
            active_patch=active_patch,
        )
        effective_selection = selection_for_intent(intent, focus.primary_selection, active_patch)
        self.database.add_script_assistant_message(
            generation_id=generation_id,
            conversation_id=conversation["conversation_id"],
            role="user",
            content=request.message,
            selection=effective_selection.text,
            reference_selection=focus.reference_selection.text,
            intent=intent,
            focus_action=focus.focus_action,
            patch_id=request.patch_id,
            selection_hash=text_hash(effective_selection.text) if effective_selection.text else "",
            reference_selection_hash=(
                text_hash(focus.reference_selection.text) if focus.reference_selection.text else ""
            ),
            paragraph_id=effective_selection.paragraph_id,
            start_offset=effective_selection.start_offset,
            end_offset=effective_selection.end_offset,
        )

        if focus.focus_action == ASK_CLARIFICATION:
            result = with_focus(
                assistant_result(
                    intent=intent,
                    answer=focus.clarification_answer,
                ),
                focus,
            )
            memory.update(
                intent=intent,
                selection=focus.active_selection if focus.active_selection.text else None,
                article_version_hash=article_hash,
                user_message=request.message,
            )
            memory.state = self._save_assistant_message(
                generation_id,
                conversation["conversation_id"],
                result,
                effective_selection,
                [],
                focus,
            )
            return self._payload(result, [], memory.state), 200

        if intent == SMALLTALK:
            result = with_focus(
                assistant_result(
                    intent=intent,
                    answer=smalltalk_answer(request.message, has_selection=bool(effective_selection.text)),
                ),
                focus,
            )
            memory.update(intent=intent, article_version_hash=article_hash, user_message=request.message)
            memory.state = self._save_assistant_message(
                generation_id,
                conversation["conversation_id"],
                result,
                effective_selection,
                [],
                focus,
            )
            return self._payload(result, [], memory.state), 200

        if intent == APPLY_PATCH:
            result, status, updated_generation = self._apply_patch(
                generation_id=generation_id,
                patch_id=request.patch_id,
                conversation_id=conversation["conversation_id"],
                current_generation=generation,
                article_hash=article_hash,
            )
            result = with_focus(result, focus)
            memory.update(
                intent=intent,
                article_version_hash=article_version_hash(script_article(updated_generation or generation)),
                user_message=request.message,
                accepted_or_rejected="已应用候选修改" if result.get("applied") else "",
            )
            memory.state = self._save_assistant_message(
                generation_id,
                conversation["conversation_id"],
                result,
                effective_selection,
                [],
                focus,
            )
            payload = self._payload(result, [], memory.state)
            if updated_generation:
                payload["generation"] = updated_generation
            return payload, status

        if intent == REJECT_PATCH:
            result = with_focus(
                self._reject_patch(generation_id, conversation["conversation_id"], request.patch_id, active_patch),
                focus,
            )
            memory.update(
                intent=intent,
                patch_id=None,
                article_version_hash=article_hash,
                user_message=request.message,
                accepted_or_rejected="已放弃候选修改" if result.get("rejected") else "",
            )
            memory.state = self._save_assistant_message(
                generation_id,
                conversation["conversation_id"],
                result,
                effective_selection,
                [],
                focus,
            )
            return self._payload(result, [], memory.state), 200

        if intent in {PROPOSE_EDIT, REVISE_PENDING} and not effective_selection.text:
            result = with_focus(
                assistant_result(
                    intent=intent,
                    answer="请先选中要修改的剧本文字，或点击某条候选修改继续调整。",
                ),
                focus,
            )
            memory.update(intent=intent, article_version_hash=article_hash, user_message=request.message)
            memory.state = self._save_assistant_message(
                generation_id,
                conversation["conversation_id"],
                result,
                effective_selection,
                [],
                focus,
            )
            return self._payload(result, [], memory.state), 200

        contexts = self._contexts_for_intent(
            intent,
            request.message,
            effective_selection,
            generation,
            needs_rag=plan.needs_rag,
        )
        try:
            result = self.script_agent.assist_edit(
                generation=generation,
                selection=effective_selection.text,
                instruction=request.message,
                contexts=contexts,
                conversation=recent_messages,
                pending_edit=active_patch if intent == REVISE_PENDING else None,
                intent=intent,
                memory=memory.state,
                reference_selection=selection_to_dict(focus.reference_selection),
                focus_action=focus.focus_action,
                focus_reason=focus.focus_reason,
            )
        except ValueError as exc:
            return {"error": str(exc)}, 400
        except RuntimeError as exc:
            return {"error": str(exc)}, 500

        result = with_focus(normalize_controller_result(result, intent), focus)
        patch_id = None
        if intent in {PROPOSE_EDIT, REVISE_PENDING} and result.get("replacement"):
            if intent == REVISE_PENDING and active_patch:
                self.database.mark_script_edit_patch_status(int(active_patch["patch_id"]), "stale")
            patch_id = self.database.create_script_edit_patch(
                generation_id=generation_id,
                conversation_id=conversation["conversation_id"],
                selection=effective_selection.text,
                replacement=str(result["replacement"]),
                answer=str(result.get("answer") or ""),
                selection_hash=text_hash(effective_selection.text),
                paragraph_id=effective_selection.paragraph_id,
                start_offset=effective_selection.start_offset,
                end_offset=effective_selection.end_offset,
                article_version_hash=article_hash,
            )
            result["patch_id"] = patch_id
            result["pending_edit_id"] = patch_id
            result["needs_confirmation"] = True

        memory.update(
            intent=intent,
            selection=focus.active_selection if focus.active_selection.text else effective_selection,
            patch_id=patch_id,
            article_version_hash=article_hash,
            user_message=request.message,
        )
        memory.state = self._save_assistant_message(
            generation_id,
            conversation["conversation_id"],
            result,
            effective_selection,
            contexts,
            focus,
        )
        return self._payload(result, contexts, memory.state), 200

    def _contexts_for_intent(
        self,
        intent: str,
        message: str,
        selection: AssistantSelection,
        generation: dict[str, Any],
        *,
        needs_rag: bool | None = None,
    ) -> list[dict[str, Any]]:
        if needs_rag is False:
            return []
        if needs_rag is None and not intent_needs_rag(intent, message):
            return []
        record_ids = [str(record_id) for record_id in generation.get("selected_record_ids") or []]
        query_text = f"{selection.text}\n{message}".strip()
        store = LocalVectorStore(self.rag_database_path)
        contexts = store.search(query_text, record_ids=record_ids, limit=6)
        if not contexts:
            chunks = build_material_chunks(self.database, self.outputs_path, record_ids)
            store.replace_record_chunks(record_ids, chunks)
            contexts = store.search(query_text, record_ids=record_ids, limit=6)
        return contexts

    def _plan_request(
        self,
        *,
        request: AssistantRequest,
        generation: dict[str, Any],
        conversation: dict[str, Any],
        recent_messages: list[dict[str, Any]],
        active_patch: dict[str, Any] | None,
        fallback_intent: str,
    ) -> AssistantPlan:
        payload = {
            "message": request.message,
            "selection": selection_to_dict(request.selection),
            "current_selection": selection_to_dict(selection_from_conversation(conversation)),
            "intent_hint": request.intent_hint,
            "patch_id": request.patch_id,
            "has_active_patch": bool(active_patch),
            "active_patch": summarize_active_patch(active_patch),
            "available_tools": assistant_tool_manifest(),
            "conversation": recent_messages,
            "topic": generation.get("topic", ""),
            "time_range": generation.get("time_range", ""),
            "script_title": (generation.get("script") or {}).get("title", ""),
        }
        try:
            raw_plan = self.script_agent.plan_assist(payload)
        except (RuntimeError, ValueError, TypeError, KeyError):
            raw_plan = None
        if raw_plan:
            return normalize_assistant_plan(
                raw_plan,
                fallback_intent=fallback_intent,
                has_selection=bool(request.selection.text.strip()),
                has_active_patch=bool(active_patch),
            )
        return AssistantPlan(intent=fallback_intent, source="fallback", reason="keyword fallback")

    def _apply_patch(
        self,
        *,
        generation_id: str,
        patch_id: int | None,
        conversation_id: str,
        current_generation: dict[str, Any],
        article_hash: str,
    ) -> tuple[dict[str, Any], int, dict[str, Any] | None]:
        if patch_id is None:
            return (
                assistant_result(
                    intent=APPLY_PATCH,
                    answer="请点击要应用的候选修改，或明确指定 patch_id 后再应用。",
                    applied=False,
                ),
                200,
                None,
            )
        patch = self.database.find_script_edit_patch(int(patch_id))
        if not patch or patch.get("generation_id") != generation_id or patch.get("conversation_id") != conversation_id:
            return (
                assistant_result(intent=APPLY_PATCH, answer=f"找不到 patch_id={patch_id} 的候选修改。", patch_id=patch_id),
                404,
                None,
            )
        if patch.get("status") != "pending":
            return (
                assistant_result(
                    intent=APPLY_PATCH,
                    answer=f"patch_id={patch_id} 当前状态是 {patch.get('status')}，不能再应用。",
                    patch_id=patch_id,
                ),
                409,
                None,
            )
        article = script_article(current_generation)
        selection = str(patch.get("selection") or "")
        replacement = str(patch.get("replacement") or "")
        updated_article = apply_patch_to_article(article, patch, current_hash=article_hash)
        if updated_article is None:
            return (
                assistant_result(
                    intent=APPLY_PATCH,
                    answer="无法唯一定位这条候选修改对应的原文，正文可能已变化或相同文本出现多次。请重新选中段落后再生成修改。",
                    patch_id=patch_id,
                    applied=False,
                ),
                409,
                None,
            )
        if not selection or not replacement:
            return (
                assistant_result(intent=APPLY_PATCH, answer="这条候选修改缺少原文或替换内容，不能应用。", patch_id=patch_id),
                409,
                None,
            )
        updated_generation = self.database.update_script_article(generation_id, updated_article)
        self.database.mark_script_edit_patch_applied(int(patch_id))
        return (
            assistant_result(
                intent=APPLY_PATCH,
                answer=f"已应用 patch_id={patch_id}，并保存到剧本正文。",
                patch_id=patch_id,
                applied=True,
            ),
            200,
            updated_generation,
        )

    def _reject_patch(
        self,
        generation_id: str,
        conversation_id: str,
        patch_id: int | None,
        active_patch: dict[str, Any] | None,
    ) -> dict[str, Any]:
        target_patch_id = patch_id or (int(active_patch["patch_id"]) if active_patch else None)
        if target_patch_id is None:
            return assistant_result(intent=REJECT_PATCH, answer="当前没有可放弃的候选修改。", rejected=False)
        patch = self.database.find_script_edit_patch(int(target_patch_id))
        if not patch or patch.get("generation_id") != generation_id or patch.get("conversation_id") != conversation_id:
            return assistant_result(
                intent=REJECT_PATCH,
                answer=f"找不到 patch_id={target_patch_id} 的候选修改。",
                patch_id=target_patch_id,
                rejected=False,
            )
        self.database.mark_script_edit_patch_status(int(target_patch_id), "rejected")
        return assistant_result(
            intent=REJECT_PATCH,
            answer=f"已放弃 patch_id={target_patch_id} 的候选修改。",
            patch_id=target_patch_id,
            rejected=True,
        )

    def _save_assistant_message(
        self,
        generation_id: str,
        conversation_id: str,
        result: dict[str, Any],
        selection: AssistantSelection,
        contexts: list[dict[str, Any]],
        focus: FocusResolution,
    ) -> dict[str, Any]:
        self.database.add_script_assistant_message(
            generation_id=generation_id,
            conversation_id=conversation_id,
            role="assistant",
            content=str(result.get("answer") or ""),
            selection=selection.text,
            reference_selection=focus.reference_selection.text,
            intent=str(result.get("intent") or ""),
            focus_action=focus.focus_action,
            patch_id=parse_optional_int(result.get("patch_id")),
            selection_hash=text_hash(selection.text) if selection.text else "",
            reference_selection_hash=(
                text_hash(focus.reference_selection.text) if focus.reference_selection.text else ""
            ),
            paragraph_id=selection.paragraph_id,
            start_offset=selection.start_offset,
            end_offset=selection.end_offset,
            result=result,
            contexts=contexts,
        )
        conversation = self.database.find_script_assistant_conversation(generation_id, conversation_id)
        if not conversation:
            raise KeyError(conversation_id)
        return conversation

    def _payload(self, result: dict[str, Any], contexts: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
        return {
            "result": result,
            "contexts": contexts,
            "conversation": state,
            "state": {
                "active_patch_id": state.get("active_patch_id"),
                "active_selection_id": state.get("active_selection_id"),
            },
        }


def normalize_assistant_plan(
    raw_plan: dict[str, Any],
    *,
    fallback_intent: str,
    has_selection: bool,
    has_active_patch: bool,
) -> AssistantPlan:
    if not isinstance(raw_plan, dict):
        return AssistantPlan(intent=fallback_intent, source="fallback", reason="invalid planner payload")
    raw_intent = str(raw_plan.get("intent") or raw_plan.get("action") or raw_plan.get("tool") or "")
    raw_tool = str(raw_plan.get("tool") or raw_plan.get("action") or "")
    should_create_patch = parse_optional_bool(raw_plan.get("should_create_patch"))
    intent = plan_intent_to_controller_intent(raw_intent or raw_tool, fallback_intent, has_selection)
    if should_create_patch is True and intent not in {APPLY_PATCH, REJECT_PATCH, REVISE_PENDING}:
        intent = PROPOSE_EDIT
    if intent == REVISE_PENDING and not has_active_patch:
        intent = PROPOSE_EDIT if has_selection else fallback_intent
    return AssistantPlan(
        intent=intent,
        tool=normalize_plan_token(raw_tool),
        needs_rag=parse_optional_bool(raw_plan.get("needs_rag")),
        selection_policy=normalize_plan_token(str(raw_plan.get("selection_policy") or "")),
        reason=str(raw_plan.get("reason") or "").strip(),
        source="planner",
    )


def plan_intent_to_controller_intent(raw_intent: str, fallback_intent: str, has_selection: bool) -> str:
    token = normalize_plan_token(raw_intent)
    if token in {"smalltalk", "plain_chat", "chat", "general_chat"}:
        return SMALLTALK
    if token in {"review", "review_script", "review_selection", "chat_with_selection", "quality_review"}:
        return REVIEW_SELECTION if has_selection else REVIEW_SCRIPT
    if token in {"explain", "explain_script", "explain_selection", "understand_selection"}:
        return EXPLAIN_SELECTION if has_selection else EXPLAIN_SCRIPT
    if token in {"edit", "rewrite", "modify", "polish", "propose_edit", "edit_selection"}:
        return PROPOSE_EDIT
    if token in {"revise", "revise_pending", "revise_patch"}:
        return REVISE_PENDING
    if token in {"source", "ask_source", "search_sources", "verify_source", "check_source"}:
        return ASK_SOURCE
    if token in {"apply", "apply_patch"}:
        return APPLY_PATCH
    if token in {"reject", "reject_patch"}:
        return REJECT_PATCH
    return fallback_intent


def normalize_plan_token(value: str) -> str:
    return re.sub(r"[^a-z0-9_\u4e00-\u9fff]+", "_", str(value or "").strip().lower()).strip("_")


def parse_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        compact = value.strip().lower()
        if compact in {"true", "1", "yes", "y", "需要", "是"}:
            return True
        if compact in {"false", "0", "no", "n", "不需要", "否"}:
            return False
    return None


def summarize_active_patch(active_patch: dict[str, Any] | None) -> dict[str, Any]:
    if not active_patch:
        return {}
    return {
        "patch_id": active_patch.get("patch_id"),
        "selection": str(active_patch.get("selection") or "")[:500],
        "replacement": str(active_patch.get("replacement") or "")[:500],
        "status": active_patch.get("status"),
    }


def assistant_tool_manifest() -> list[dict[str, Any]]:
    return [dict(tool) for tool in ASSISTANT_TOOL_MANIFEST]


def normalize_assist_request(payload: dict[str, Any]) -> AssistantRequest:
    message = str(payload.get("message") or payload.get("instruction") or "").strip()
    raw_selection = payload.get("selection")
    selection = normalize_selection(raw_selection)
    return AssistantRequest(
        message=message,
        selection=selection,
        conversation_id=str(payload.get("conversation_id") or "").strip(),
        intent_hint=str(payload.get("intent_hint") or "").strip(),
        patch_id=parse_optional_int(payload.get("patch_id")),
    )


def normalize_selection(raw_selection: Any) -> AssistantSelection:
    if isinstance(raw_selection, dict):
        return AssistantSelection(
            text=str(raw_selection.get("text") or "").strip(),
            paragraph_id=str(raw_selection.get("paragraph_id") or "").strip(),
            start_offset=parse_optional_int(raw_selection.get("start_offset")),
            end_offset=parse_optional_int(raw_selection.get("end_offset")),
        )
    return AssistantSelection(text=str(raw_selection or "").strip())


def selection_from_conversation(conversation: dict[str, Any]) -> AssistantSelection:
    return AssistantSelection(
        text=str(conversation.get("active_selection_text") or "").strip(),
        paragraph_id=str(conversation.get("active_paragraph_id") or "").strip(),
        start_offset=parse_optional_int(conversation.get("active_start_offset")),
        end_offset=parse_optional_int(conversation.get("active_end_offset")),
    )


def selection_from_patch(patch: dict[str, Any] | None) -> AssistantSelection:
    if not patch:
        return AssistantSelection()
    return AssistantSelection(
        text=str(patch.get("selection") or "").strip(),
        paragraph_id=str(patch.get("paragraph_id") or "").strip(),
        start_offset=parse_optional_int(patch.get("start_offset")),
        end_offset=parse_optional_int(patch.get("end_offset")),
    )


def selection_to_dict(selection: AssistantSelection) -> dict[str, Any]:
    if not selection.text:
        return {}
    return {
        "text": selection.text,
        "paragraph_id": selection.paragraph_id,
        "start_offset": selection.start_offset,
        "end_offset": selection.end_offset,
        "selection_hash": text_hash(selection.text),
    }


def parse_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def selection_for_intent(
    intent: str,
    selection: AssistantSelection,
    active_patch: dict[str, Any] | None,
) -> AssistantSelection:
    if selection.text:
        return selection
    if intent == REVISE_PENDING and active_patch:
        return AssistantSelection(
            text=str(active_patch.get("selection") or ""),
            paragraph_id=str(active_patch.get("paragraph_id") or ""),
            start_offset=active_patch.get("start_offset"),
            end_offset=active_patch.get("end_offset"),
        )
    return AssistantSelection()


def with_focus(result: dict[str, Any], focus: FocusResolution) -> dict[str, Any]:
    focused = dict(result)
    focused["focus_action"] = focus.focus_action
    focused["focus_reason"] = focus.focus_reason
    focused["reference_selection"] = selection_to_dict(focus.reference_selection)
    focused["active_selection"] = selection_to_dict(focus.active_selection)
    return focused


def normalize_controller_result(result: dict[str, Any], intent: str) -> dict[str, Any]:
    normalized = dict(result or {})
    normalized["intent"] = intent
    normalized.setdefault("answer", "")
    normalized.setdefault("used_context_ids", [])
    normalized.setdefault("patch_id", None)
    normalized.setdefault("needs_confirmation", False)
    normalized.setdefault("applied", False)
    if intent not in {PROPOSE_EDIT, REVISE_PENDING}:
        normalized["replacement"] = ""
        normalized["needs_confirmation"] = False
    else:
        normalized["replacement"] = str(normalized.get("replacement") or "")
    return normalized


def assistant_result(
    *,
    intent: str,
    answer: str,
    replacement: str = "",
    patch_id: int | None = None,
    applied: bool = False,
    rejected: bool = False,
    used_context_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "intent": intent,
        "answer": answer,
        "replacement": replacement,
        "patch_id": patch_id,
        "pending_edit_id": patch_id,
        "needs_confirmation": bool(replacement and patch_id),
        "applied": applied,
        "rejected": rejected,
        "used_context_ids": used_context_ids or [],
    }


def apply_patch_to_article(article: str, patch: dict[str, Any], *, current_hash: str) -> str | None:
    selection = str(patch.get("selection") or "")
    replacement = str(patch.get("replacement") or "")
    if not selection or not replacement:
        return None
    patch_hash = str(patch.get("article_version_hash") or "")
    start = patch.get("start_offset")
    end = patch.get("end_offset")
    if patch_hash and patch_hash == current_hash and isinstance(start, int) and isinstance(end, int):
        if 0 <= start < end <= len(article) and article[start:end] == selection:
            return f"{article[:start]}{replacement}{article[end:]}"
    if article.count(selection) == 1:
        return article.replace(selection, replacement, 1)
    return None


def intent_needs_rag(intent: str, message: str) -> bool:
    if intent in {ASK_SOURCE, REVIEW_SCRIPT, REVIEW_SELECTION, PROPOSE_EDIT, REVISE_PENDING}:
        return True
    if intent == EXPLAIN_SELECTION and has_source_marker(compact_text(message)):
        return True
    return False


def intent_uses_selection(intent: str) -> bool:
    return intent in {EXPLAIN_SELECTION, REVIEW_SELECTION, PROPOSE_EDIT, REVISE_PENDING, APPLY_PATCH, REJECT_PATCH}


def script_article(generation: dict[str, Any]) -> str:
    return str((generation.get("script") or {}).get("article") or "")


def article_version_hash(article: str) -> str:
    return text_hash(article)


def text_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def compact_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def is_smalltalk(compact: str) -> bool:
    smalltalk_exact = {
        "你好",
        "您好",
        "谢谢",
        "多谢",
        "谢了",
        "你是谁",
        "你能做什么",
        "这个功能怎么用",
        "怎么用",
        "可以聊聊吗",
    }
    return compact in smalltalk_exact


def has_apply_marker(compact: str) -> bool:
    if any(marker in compact for marker in ("不可以", "先不要", "不要改", "别改")):
        return False
    return any(
        marker in compact
        for marker in (
            "可以",
            "同意",
            "按这个改",
            "应用这个修改",
            "应用修改",
            "确认修改",
            "保存这个修改",
            "开始修改",
            "开始为我修改",
        )
    )


def has_reject_marker(compact: str) -> bool:
    return any(marker in compact for marker in ("不要这个", "取消", "放弃", "先不要", "别改"))


def has_revision_marker(compact: str) -> bool:
    return any(
        marker in compact
        for marker in (
            "再",
            "不，",
            "不对",
            "应该",
            "更口语",
            "口语一点",
            "不要这么",
            "保留事实",
            "更短",
            "太夸张",
            "不够",
            "上一版",
        )
    )


def has_continue_focus_marker(compact: str) -> bool:
    return any(
        marker in compact
        for marker in (
            "继续",
            "再短",
            "改短",
            "再改",
            "上一版",
            "刚才",
            "保留刚才",
            "保留原意",
            "不要这么",
            "不，",
            "不对",
        )
    )


def has_source_marker(compact: str) -> bool:
    return any(marker in compact for marker in ("史实", "依据", "出处", "来源", "可靠", "真实吗", "材料支持"))


def has_compare_marker(compact: str) -> bool:
    return any(marker in compact for marker in ("对比", "比较", "这两段", "两个段落", "有什么区别", "区别", "前后衔接"))


def has_reference_marker(compact: str) -> bool:
    return any(marker in compact for marker in ("参考这段", "结合这段", "作为补充", "补充材料", "用这段补", "补一下前面"))


def has_explicit_new_focus_marker(compact: str) -> bool:
    return any(
        marker in compact
        for marker in (
            "改这段",
            "解释这段",
            "评审这段",
            "这段是什么意思",
            "新选的这段",
            "新选这段",
            "当前这段",
        )
    )


def has_ambiguous_focus_marker(compact: str) -> bool:
    return compact in {"这样行吗", "你看看", "怎么改"} or any(
        marker in compact for marker in ("这样可以吗", "这样好吗", "行吗", "可以吗")
    )


def has_edit_marker(compact: str) -> bool:
    return any(
        marker in compact
        for marker in (
            "润色",
            "改写",
            "重写",
            "补充",
            "补一下",
            "调整",
            "更口语",
            "帮我改",
            "修改这段",
            "加几个",
            "疑问句",
            "放在开头",
        )
    )


def has_review_marker(compact: str) -> bool:
    return any(
        marker in compact
        for marker in (
            "评审",
            "审查",
            "哪里不好",
            "有什么问题",
            "问题在哪",
            "帮我看看",
            "看一下",
            "看下",
            "你看看",
            "先看",
            "看看这段",
            "看这一段",
            "这段怎么样",
            "这段如何",
            "这段好不好",
            "这段行不行",
        )
    )


def has_explain_marker(compact: str) -> bool:
    return any(marker in compact for marker in ("什么意思", "解释", "说明一下", "讲讲", "看不懂", "总结"))


def smalltalk_answer(message: str, *, has_selection: bool = False) -> str:
    compact = compact_text(message)
    if compact in {"你是谁", "你能做什么", "这个功能怎么用", "怎么用"}:
        return "我是剧本对话助手。你可以直接问我剧本内容，也可以选中一段后点“解释这段”“评审这段”或“改写这段”。候选修改必须点“应用这个修改”才会保存。"
    suffix = "我也可以帮你解释、评审或改写刚选中的段落。" if has_selection else "你可以问我剧本内容，或选中一段让我解释、评审、改写。"
    return f"你好，我在。{suffix}"


def extract_style_preferences(message: str) -> list[str]:
    compact = compact_text(message)
    preferences: list[str] = []
    markers = {
        "不要太营销号": "不要太营销号",
        "事实谨慎": "事实谨慎",
        "口语": "口语化",
        "普通人视角": "普通人视角",
        "更短": "更短",
        "不要这么夸张": "不要夸张",
    }
    for marker, preference in markers.items():
        if marker in compact:
            preferences.append(preference)
    return preferences
