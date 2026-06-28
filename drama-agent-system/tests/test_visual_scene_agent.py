import json

from drama_agents.visual_scene_agent import DeepSeekVisualSceneProvider, build_visual_scene_prompt


class FakeDeepSeekResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_deepseek_visual_scene_provider_defaults_to_flash():
    provider = DeepSeekVisualSceneProvider(api_key="test-key")

    assert provider.model == "deepseek-v4-flash"


def test_deepseek_visual_scene_provider_reports_empty_truncated_content(monkeypatch):
    def fake_urlopen(request, timeout):
        return FakeDeepSeekResponse(
            {
                "model": "deepseek-v4-pro",
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": "一直在推理但没有输出 JSON",
                        },
                    }
                ],
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = DeepSeekVisualSceneProvider(api_key="test-key", model="deepseek-v4-pro")

    try:
        provider.extract_scenes({"title": "测试", "article": "东非稀树草原出现。"})
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected RuntimeError")

    assert "DeepSeek 场景解析" in message
    assert "finish_reason=length" in message
    assert "content=<空内容>" in message


def test_visual_scene_prompt_keeps_scene_extraction_lightweight():
    prompt = build_visual_scene_prompt(
        {
            "title": "测试剧本",
            "topic": "智人迁徙",
            "article": "东非稀树草原、布隆伯斯洞穴和红海海口迁徙渡口反复出现。",
        }
    )

    assert "轻量场景解析" in prompt
    assert "不生成场景图" in prompt
    assert "最多输出" in prompt
    assert "每个字符串" in prompt
