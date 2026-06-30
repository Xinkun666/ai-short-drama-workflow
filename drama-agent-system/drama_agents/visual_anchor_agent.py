from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from drama_agents.visual_scene_agent import build_scene_anchor_negative_prompt, build_scene_anchor_prompt


HISTORY_CARTOON_STYLE_POLICY = """
历史科普卡通短剧视觉风格：
- 默认不是写实电影，也不是过度幼稚的扁平小人。
- 普通镜头优先使用地图、地貌、简化建筑、半扁平卡通人物/群体剪影和少量道具。
- 人物应是半扁平卡通人物，有明确历史身份和服饰特征，不要太幼稚，不要极简豆豆眼。
- 分镜应优先旁白驱动、字幕、箭头、缩放和图层移动，避免每一镜都生成复杂电影场景。
- 关键镜头可以更复杂，使用漫画插画感、强构图、明确光影和更强情绪冲击。
- 成本优先级：70%-80% 普通低成本镜头，20%-30% 关键漫画镜头。
- 主体锚点图阶段只生成单一主体纯参考图；地图、地貌、建筑、群体剪影和图层移动属于后续场景/分镜阶段。
""".strip()


class VisualAnchorAgent:
    def __init__(self, provider=None):
        self.provider = provider

    @classmethod
    def from_environment(cls):
        preferred_provider = os.environ.get("SUBJECT_ANCHOR_PROVIDER", "ark").strip().lower()
        openai_key = os.environ.get("OPENAI_API_KEY")
        ark_key = os.environ.get("ARK_API_KEY")
        if preferred_provider == "ark":
            providers = []
            if ark_key:
                providers.append(ArkSeedDreamImageProvider(api_key=ark_key))
            if openai_key:
                providers.append(OpenAIImageProvider(api_key=openai_key))
            if providers:
                return cls(provider=providers[0] if len(providers) == 1 else ChainedImageProvider(providers))
        if preferred_provider == "openai" and openai_key:
            return cls(provider=OpenAIImageProvider(api_key=openai_key))
        if ark_key:
            return cls(provider=ArkSeedDreamImageProvider(api_key=ark_key))
        if openai_key:
            return cls(provider=OpenAIImageProvider(api_key=openai_key))
        return cls(provider=None)

    def generate_subject_anchor(
        self,
        *,
        subject: dict[str, Any],
        output_dir: Path | str,
        prompt: str | None = None,
        negative_prompt: str | None = None,
    ) -> dict[str, Any]:
        if not self.provider:
            raise RuntimeError("未配置 OPENAI_API_KEY 或 ARK_API_KEY，无法生成主体锚点图。")
        final_prompt = str(prompt or "").strip() or build_subject_anchor_prompt(subject)
        final_negative_prompt = (
            str(negative_prompt).strip()
            if negative_prompt is not None
            else build_subject_anchor_negative_prompt(subject)
        )
        generated = self.provider.generate_image(prompt=final_prompt, negative_prompt=final_negative_prompt)
        image_bytes = generated.get("image_bytes")
        if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
            raise RuntimeError("图片接口没有返回可保存的图片内容。")

        mime_type = str(generated.get("mime_type") or "image/png")
        extension = image_extension(mime_type)
        subject_id = str(subject.get("subject_id") or subject.get("canonical_name") or "subject").strip() or "subject"
        asset_dir = Path(output_dir) / safe_asset_name(subject_id)
        asset_dir.mkdir(parents=True, exist_ok=True)
        asset_path = asset_dir / f"anchor.{extension}"
        asset_path.write_bytes(bytes(image_bytes))
        return {
            "asset_path": asset_path,
            "mime_type": mime_type,
            "model": str(generated.get("model") or ""),
            "provider": str(generated.get("provider") or "ark"),
            "prompt": final_prompt,
            "negative_prompt": final_negative_prompt,
            "workflow_name": str(generated.get("workflow_name") or workflow_name_for_provider(generated)),
        }

    def generate_scene_anchor(
        self,
        *,
        scene: dict[str, Any],
        output_dir: Path | str,
        prompt: str | None = None,
        negative_prompt: str | None = None,
    ) -> dict[str, Any]:
        if not self.provider:
            raise RuntimeError("未配置 OPENAI_API_KEY 或 ARK_API_KEY，无法生成场景图。")
        final_prompt = str(prompt or "").strip() or build_scene_anchor_prompt(scene)
        final_negative_prompt = (
            str(negative_prompt).strip()
            if negative_prompt is not None
            else build_scene_anchor_negative_prompt(scene)
        )
        generated = self.provider.generate_image(prompt=final_prompt, negative_prompt=final_negative_prompt)
        image_bytes = generated.get("image_bytes")
        if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
            raise RuntimeError("图片接口没有返回可保存的图片内容。")

        mime_type = str(generated.get("mime_type") or "image/png")
        extension = image_extension(mime_type)
        scene_id = str(scene.get("scene_id") or scene.get("canonical_name") or "scene").strip() or "scene"
        asset_dir = Path(output_dir) / safe_asset_name(scene_id)
        asset_dir.mkdir(parents=True, exist_ok=True)
        asset_path = asset_dir / f"anchor.{extension}"
        asset_path.write_bytes(bytes(image_bytes))
        return {
            "asset_path": asset_path,
            "mime_type": mime_type,
            "model": str(generated.get("model") or ""),
            "provider": str(generated.get("provider") or "ark"),
            "prompt": final_prompt,
            "negative_prompt": final_negative_prompt,
            "workflow_name": str(
                generated.get("workflow_name")
                or workflow_name_for_provider(generated, asset_type="scene")
            ),
        }

    def generate_storyboard_keyframe(
        self,
        *,
        shot: dict[str, Any],
        output_dir: Path | str,
        prompt: str | None = None,
        negative_prompt: str | None = None,
        reference_images: list[str] | None = None,
    ) -> dict[str, Any]:
        if not self.provider:
            raise RuntimeError("未配置 OPENAI_API_KEY 或 ARK_API_KEY，无法生成分镜关键帧。")
        final_prompt = str(prompt or shot.get("keyframe_prompt") or shot.get("visual_goal") or shot.get("narration") or "").strip()
        if not final_prompt:
            raise RuntimeError("当前镜头缺少 keyframe_prompt，无法生成关键帧。")
        references = [str(item).strip() for item in reference_images or [] if str(item).strip()]
        if references and "上一镜头关键帧参考图" not in final_prompt:
            final_prompt = (
                f"{final_prompt}\n\n"
                "上一镜头关键帧参考图：已随请求提供。请参考上一帧的色彩、笔触、主体外观、空间轴线和叙事情绪；"
                "当前画面仍以本镜头内容为准，不要机械复制上一帧构图。"
            )
        final_negative_prompt = (
            str(negative_prompt).strip()
            if negative_prompt is not None
            else str(shot.get("negative_prompt") or "").strip()
        )
        generated = generate_image_with_optional_references(
            self.provider,
            prompt=final_prompt,
            negative_prompt=final_negative_prompt,
            reference_images=references,
        )
        image_bytes = generated.get("image_bytes")
        if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
            raise RuntimeError("图片接口没有返回可保存的图片内容。")

        mime_type = str(generated.get("mime_type") or "image/png")
        extension = image_extension(mime_type)
        storyboard_id = safe_asset_name(str(shot.get("storyboard_id") or "storyboard"))
        shot_id = safe_asset_name(str(shot.get("shot_id") or "shot"))
        asset_dir = Path(output_dir) / storyboard_id / shot_id
        asset_dir.mkdir(parents=True, exist_ok=True)
        asset_path = asset_dir / f"keyframe.{extension}"
        asset_path.write_bytes(bytes(image_bytes))
        return {
            "asset_path": asset_path,
            "mime_type": mime_type,
            "model": str(generated.get("model") or ""),
            "provider": str(generated.get("provider") or "ark"),
            "prompt": final_prompt,
            "negative_prompt": final_negative_prompt,
            "workflow_name": str(generated.get("workflow_name") or workflow_name_for_provider(generated, asset_type="keyframe")),
        }


class ChainedImageProvider:
    def __init__(self, providers: list[Any]):
        self.providers = providers

    def generate_image(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        reference_images: list[str] | None = None,
    ) -> dict[str, Any]:
        errors = []
        for provider in self.providers:
            try:
                return generate_image_with_optional_references(
                    provider,
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    reference_images=reference_images or [],
                )
            except RuntimeError as exc:
                errors.append(str(exc))
        raise RuntimeError("所有图片生成通道均失败：" + " | ".join(errors))


class OpenAIImageProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str | None = None,
        base_url: str | None = None,
        timeout: int | None = None,
        size: str | None = None,
        quality: str | None = None,
    ):
        self.api_key = api_key
        self.model = model or os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-2")
        self.base_url = base_url or os.environ.get("OPENAI_IMAGE_BASE_URL", "https://api.openai.com/v1/images/generations")
        self.timeout = timeout or int(os.environ.get("OPENAI_IMAGE_TIMEOUT", "240"))
        self.size = size or os.environ.get("OPENAI_IMAGE_SIZE", "1024x1024")
        self.quality = quality or os.environ.get("OPENAI_IMAGE_QUALITY", "medium")

    def generate_image(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        reference_images: list[str] | None = None,
    ) -> dict[str, Any]:
        full_prompt = prompt
        if negative_prompt:
            full_prompt = f"{prompt}\n\n避免：{negative_prompt}"
        body = {
            "model": self.model,
            "prompt": full_prompt,
            "size": self.size,
            "quality": self.quality,
        }
        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"OpenAI 图片生成失败 HTTP {exc.code}: {detail[:500]}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"OpenAI 图片生成连接失败：{exc}") from exc
        image_bytes, mime_type = image_from_openai_payload(payload)
        return {
            "image_bytes": image_bytes,
            "mime_type": mime_type,
            "model": payload.get("model") or self.model,
            "provider": "openai",
            "workflow_name": "openai_gpt_image_2_subject_anchor_v1"
            if self.model == "gpt-image-2"
            else "openai_image_subject_anchor_v1",
        }


class ArkSeedDreamImageProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str | None = None,
        base_url: str | None = None,
        timeout: int | None = None,
        size: str | None = None,
        response_format: str | None = None,
        watermark: bool | None = None,
    ):
        self.api_key = api_key
        model_value = (
            model
            or os.environ.get("ARK_IMAGE_MODEL")
            or os.environ.get("ARK_IMAGE_MODELS")
            or os.environ.get("ARK_SEEDDREAM_MODEL")
            or "doubao-seedream-5-0-260128"
        )
        fallback_models = os.environ.get(
            "ARK_SEEDDREAM_FALLBACK_MODELS",
            "doubao-seedream-5-0-260128,doubao-seedream-4-5-251128,doubao-seedream-4-0-250828,doubao-seedream-3-0-t2i-250415",
        )
        self.models = parse_model_candidates(model_value, fallback_models)
        self.model = self.models[0]
        self.base_url = base_url or os.environ.get(
            "ARK_IMAGE_BASE_URL",
            "https://ark.cn-beijing.volces.com/api/v3/images/generations",
        )
        self.timeout = timeout or int(os.environ.get("ARK_IMAGE_TIMEOUT", "180"))
        self.size = size or os.environ.get("ARK_IMAGE_SIZE", "2K")
        self.response_format = response_format or os.environ.get("ARK_IMAGE_RESPONSE_FORMAT", "url")
        if watermark is None:
            watermark_value = os.environ.get("ARK_IMAGE_WATERMARK", "true").strip().lower()
            self.watermark = watermark_value in {"1", "true", "yes", "on"}
        else:
            self.watermark = bool(watermark)

    def generate_image(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        reference_images: list[str] | None = None,
    ) -> dict[str, Any]:
        errors = []
        references = [str(item).strip() for item in reference_images or [] if str(item).strip()]
        for model in self.models:
            try:
                if references:
                    try:
                        return self._generate_image_once(
                            model=model,
                            prompt=prompt,
                            negative_prompt=negative_prompt,
                            reference_images=references,
                        )
                    except TypeError as exc:
                        if "reference_images" not in str(exc):
                            raise
                return self._generate_image_once(model=model, prompt=prompt, negative_prompt=negative_prompt)
            except RuntimeError as exc:
                message = str(exc)
                errors.append(f"{model}: {message}")
                if not should_try_next_ark_model(message):
                    break
        raise RuntimeError("ARK SeedDream 候选模型均未生成成功：" + " | ".join(errors))

    def _generate_image_once(
        self,
        *,
        model: str,
        prompt: str,
        negative_prompt: str,
        reference_images: list[str] | None = None,
    ) -> dict[str, Any]:
        full_prompt = prompt
        if negative_prompt:
            full_prompt = f"{prompt}\n\n避免：{negative_prompt}"
        body = {
            "model": model,
            "prompt": full_prompt,
            "size": self.size,
            "response_format": self.response_format,
            "watermark": self.watermark,
        }
        references = [str(item).strip() for item in reference_images or [] if str(item).strip()]
        if references:
            body["image"] = references
        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"ARK 图片生成失败 HTTP {exc.code}: {detail[:500]}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"ARK 图片生成连接失败：{exc}") from exc
        image_bytes, mime_type = image_from_ark_payload(payload, timeout=self.timeout)
        return {
            "image_bytes": image_bytes,
            "mime_type": mime_type,
            "model": payload.get("model") or model,
            "provider": "ark",
            "workflow_name": "ark_seeddream_subject_anchor_v1",
        }


def generate_image_with_optional_references(
    provider: Any,
    *,
    prompt: str,
    negative_prompt: str,
    reference_images: list[str] | None = None,
) -> dict[str, Any]:
    references = [str(item).strip() for item in reference_images or [] if str(item).strip()]
    if not references:
        return provider.generate_image(prompt=prompt, negative_prompt=negative_prompt)
    try:
        return provider.generate_image(
            prompt=prompt,
            negative_prompt=negative_prompt,
            reference_images=references,
        )
    except TypeError as exc:
        if "reference_images" not in str(exc):
            raise
        return provider.generate_image(prompt=prompt, negative_prompt=negative_prompt)


def build_subject_anchor_prompt(subject: dict[str, Any]) -> str:
    identity = subject.get("visual_identity") if isinstance(subject.get("visual_identity"), dict) else {}
    rules = subject.get("consistency_rules") if isinstance(subject.get("consistency_rules"), dict) else {}
    props = identity.get("props") if isinstance(identity.get("props"), list) else []
    must_keep = rules.get("must_keep") if isinstance(rules.get("must_keep"), list) else []
    return f"""
请生成一张“主体锚点图”，用于 AI 历史科普短剧后续分镜保持视觉一致。

{HISTORY_CARTOON_STYLE_POLICY}

主体内容：
- 名称：{subject.get("canonical_name", "")}
- 类型：{subject.get("subject_type", "")}
- 简述：{subject.get("short_description", "")}
- 时代：{identity.get("era", "")}
- 地区：{identity.get("region", "")}
- 外观：{identity.get("appearance", "")}
- 服饰：{identity.get("clothing", "")}
- 道具：{"、".join(str(item) for item in props)}
- 身体语言：{identity.get("body_language", "")}
- 群体构成：{identity.get("group_composition", "")}
- 必须保持：{"；".join(str(item) for item in must_keep)}

生成要求：
- 只生成一个主体：如果主体是族群、物种或人群，也只选择一个最典型代表个体作为锚点图。
- 画面必须是纯主体参考图，不要做完整电影场景，不要生成群像、合照或一堆人物。
- 单一主体居中展示，完整身体或半身清晰，轮廓、服饰、关键外观和姿态要清楚。
- 使用干净纯色、浅色纸张或极简渐变背景；不要地图、地貌、建筑、说明文字或信息图版式。
- 道具最多保留 1 件与主体身份强相关的手持道具，不要堆叠道具或生成道具陈列。
- 观感保持历史科普卡通短剧风格：半扁平卡通人物，但不能太幼稚，也不要写实电影感。
- 构图干净，竖屏短视频资产友好，主体占画面主要位置，背景不能抢戏。
""".strip()


def build_subject_anchor_negative_prompt(subject: dict[str, Any]) -> str:
    rules = subject.get("consistency_rules") if isinstance(subject.get("consistency_rules"), dict) else {}
    avoid = rules.get("avoid") if isinstance(rules.get("avoid"), list) else []
    negative_rules = subject.get("negative_rules") if isinstance(subject.get("negative_rules"), list) else []
    items = [
        "不要太幼稚",
        "不要幼儿动画式极简豆豆眼",
        "不要电影级写实大场景",
        "不要过度 3D",
        "不要真实皮肤毛孔质感",
        "不要现代城市服装",
        "不要无关装饰堆满画面",
        "不要奇幻盔甲",
        "不要科幻装备",
        "不要多人群像",
        "不要多个主体",
        "不要群体合照",
        "不要一堆人物",
        "不要信息图文字",
        "不要标题排版",
        "不要地图背景",
        "不要地貌场景",
        "不要复杂建筑",
        "不要道具陈列",
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


def image_from_ark_payload(payload: dict[str, Any], *, timeout: int) -> tuple[bytes, str]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not data:
        raise RuntimeError("ARK 图片接口返回数据中缺少 data[0]。")
    first = data[0] if isinstance(data[0], dict) else {}
    b64_json = first.get("b64_json") or first.get("base64")
    if b64_json:
        return base64.b64decode(str(b64_json)), str(first.get("mime_type") or "image/png")
    image_url = first.get("url") or first.get("image_url")
    if image_url:
        try:
            with urllib.request.urlopen(str(image_url), timeout=timeout) as response:
                mime_type = response.headers.get("Content-Type") or "image/png"
                return response.read(), mime_type.split(";")[0]
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"ARK 图片 URL 下载失败：{exc}") from exc
    raise RuntimeError("ARK 图片接口没有返回 url 或 b64_json。")


def image_from_openai_payload(payload: dict[str, Any]) -> tuple[bytes, str]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not data:
        raise RuntimeError("OpenAI 图片接口返回数据中缺少 data[0]。")
    first = data[0] if isinstance(data[0], dict) else {}
    b64_json = first.get("b64_json")
    if not b64_json:
        raise RuntimeError("OpenAI 图片接口没有返回 b64_json。")
    return base64.b64decode(str(b64_json)), "image/png"


def image_extension(mime_type: str) -> str:
    value = mime_type.lower()
    if "jpeg" in value or "jpg" in value:
        return "jpg"
    if "webp" in value:
        return "webp"
    return "png"


def parse_model_candidates(primary: str, fallback: str) -> list[str]:
    candidates = []
    for value in (primary, fallback):
        for item in str(value or "").split(","):
            model = item.strip()
            if model and model not in candidates:
                candidates.append(model)
    return candidates or ["doubao-seedream-5-0-260128"]


def should_try_next_ark_model(message: str) -> bool:
    retry_markers = (
        "ModelNotOpen",
        "has not activated the model",
        "模型未开通",
        "model_not_found",
        "The model",
    )
    return any(marker in message for marker in retry_markers)


def workflow_name_for_provider(generated: dict[str, Any], *, asset_type: str = "subject") -> str:
    provider = str(generated.get("provider") or "")
    model = str(generated.get("model") or "")
    prefix = "scene" if asset_type == "scene" else "subject"
    if provider == "openai" and model == "gpt-image-2":
        return f"openai_gpt_image_2_{prefix}_anchor_v1"
    if provider == "openai":
        return f"openai_image_{prefix}_anchor_v1"
    if provider == "ark":
        return f"ark_seeddream_{prefix}_anchor_v1"
    return f"{prefix}_anchor_v1"


def safe_asset_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value).strip("-") or "subject"
