import base64
import json

from drama_agents.visual_anchor_agent import (
    ArkSeedDreamImageProvider,
    ChainedImageProvider,
    OpenAIImageProvider,
    VisualAnchorAgent,
    build_subject_anchor_negative_prompt,
    build_subject_anchor_prompt,
)


class FallbackArkProvider(ArkSeedDreamImageProvider):
    def __init__(self):
        super().__init__(api_key="test-key", model="model-a,model-b")
        self.seen_models = []

    def _generate_image_once(self, *, model, prompt, negative_prompt):
        self.seen_models.append(model)
        if model == "model-a":
            raise RuntimeError("ARK 图片生成失败 HTTP 404: ModelNotOpen")
        return {
            "image_bytes": b"ok",
            "mime_type": "image/png",
            "model": model,
            "provider": "ark",
        }


def test_ark_provider_falls_back_when_seedream_model_is_not_open():
    provider = FallbackArkProvider()

    result = provider.generate_image(prompt="主体锚点图", negative_prompt="")

    assert provider.seen_models == ["model-a", "model-b"]
    assert result["image_bytes"] == b"ok"
    assert result["model"] == "model-b"


def test_subject_anchor_prompt_is_single_subject_reference_only():
    subject = {
        "canonical_name": "智人",
        "subject_type": "species",
        "short_description": "早期智人群体",
        "visual_identity": {
            "era": "史前时代",
            "region": "东非草原",
            "appearance": "直立行走，五官清晰",
            "clothing": "粗糙兽皮",
            "props": ["石器", "火把"],
            "body_language": "警觉而善于沟通",
            "group_composition": "成年人与少年组成的群体",
        },
        "consistency_rules": {
            "must_keep": ["清晰眉弓", "兽皮披肩"],
            "avoid": ["现代背包"],
        },
    }

    prompt = build_subject_anchor_prompt(subject)
    negative_prompt = build_subject_anchor_negative_prompt(subject)

    assert "只生成一个主体" in prompt
    assert "纯主体参考图" in prompt
    assert "最典型代表个体" in prompt
    assert "干净纯色" in prompt
    assert "3-6 个代表性人物" not in prompt
    assert "地图+人物+场景" not in prompt
    assert "不要多人群像" in negative_prompt
    assert "不要多个主体" in negative_prompt
    assert "不要地图背景" in negative_prompt
    assert "不要信息图文字" in negative_prompt


def test_ark_provider_uses_authorized_seedream_request_shape(monkeypatch):
    requests = []

    class FakeResponse:
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {"data": [{"b64_json": base64.b64encode(b"ark-image").decode("ascii")}], "model": "doubao-seedream-5-0-260128"}
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        requests.append({"request": request, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = ArkSeedDreamImageProvider(api_key="ark-key")

    result = provider.generate_image(prompt="主体锚点图", negative_prompt="不要写实")

    assert result["image_bytes"] == b"ark-image"
    assert result["provider"] == "ark"
    assert result["model"] == "doubao-seedream-5-0-260128"
    body = json.loads(requests[0]["request"].data.decode("utf-8"))
    assert body["model"] == "doubao-seedream-5-0-260128"
    assert "主体锚点图" in body["prompt"]
    assert "不要写实" in body["prompt"]
    assert body["size"] == "2K"
    assert body["response_format"] == "url"
    assert body["watermark"] is True
    assert "output_format" not in body
    assert "negative_prompt" not in body
    assert requests[0]["request"].headers["Authorization"] == "Bearer ark-key"


def test_openai_provider_uses_gpt_image_2_and_decodes_b64(monkeypatch):
    requests = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return (
                '{"data":[{"b64_json":"'
                + base64.b64encode(b"openai-image").decode("ascii")
                + '"}]}'
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        requests.append({"request": request, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = OpenAIImageProvider(api_key="openai-key")

    result = provider.generate_image(prompt="主体锚点图", negative_prompt="不要写实")

    assert result["image_bytes"] == b"openai-image"
    assert result["provider"] == "openai"
    assert result["model"] == "gpt-image-2"
    body = requests[0]["request"].data.decode("utf-8")
    assert '"model": "gpt-image-2"' in body
    assert "不要写实" in body
    assert requests[0]["request"].headers["Authorization"] == "Bearer openai-key"


def test_visual_anchor_agent_prefers_ark_and_keeps_openai_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("ARK_API_KEY", "ark-key")

    agent = VisualAnchorAgent.from_environment()

    assert isinstance(agent.provider, ChainedImageProvider)
    assert isinstance(agent.provider.providers[0], ArkSeedDreamImageProvider)
    assert isinstance(agent.provider.providers[1], OpenAIImageProvider)
