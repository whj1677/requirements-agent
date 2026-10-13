"""Offline bridge policy/evidence tests, no SDK process or model traffic."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from app.dsh_bridge import DshBridge, SDK_VERSION, TRANSPORT, event_metadata, sanitize_event


@pytest.fixture
def bridge(tmp_path):
    for name in ("workspace", "skills"):
        (tmp_path / name).mkdir()
    (tmp_path / "workspace/input-index.json").write_text('{"title":"合成电价需求"}', encoding="utf-8")
    return DshBridge(workspace=tmp_path / "workspace", skill_root=tmp_path / "skills",
                     dsh_home=tmp_path / "home", evidence_dir=tmp_path / "evidence")


def test_default_has_no_invented_request_budget(bridge):
    assert bridge.max_requests is None


def test_evidence_cannot_be_inside_readable_snapshot(tmp_path):
    tmp_path.joinpath("workspace").mkdir()
    with pytest.raises(ValueError, match="outside"):
        DshBridge(workspace=tmp_path / "workspace", skill_root=tmp_path / "workspace",
                  dsh_home=tmp_path / "workspace/home", evidence_dir=tmp_path / "evidence")


def test_snapshot_with_environment_file_is_refused(bridge):
    (bridge.workspace / ".env").write_text("secret", encoding="utf-8")
    with pytest.raises(ValueError, match="credential"):
        bridge.prepare()


def test_policy_disables_log_upload_and_persistence_but_retains_context(bridge):
    patch = json.loads(bridge.prepare().read_text("utf-8"))
    by_id = {row["id"]: row for row in patch if "id" in row}
    assert by_id["session-log-deepseek"]["config"]["enabled"] is False
    assert by_id["session-persistence-jsonl"]["disabled"] is True
    assert by_id["llm-deepseek"]["config"]["thinking"] == "disabled"
    assert by_id["skill-filesystem"]["config"]["includeDefaultRoots"] is False
    assert "compaction-basic" not in by_id
    for name in ("tool-pwsh", "tool-bash", "tool-web", "tool-subagent", "tool-workflow"):
        assert by_id[name]["disabled"] is True


def test_pinned_runtime_chat_transport_uses_public_root_in_both_layers(bridge, monkeypatch):
    captured = {}
    def sdk(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()
    monkeypatch.setitem(sys.modules, "deepseek_harness", SimpleNamespace(DeepSeekHarness=sdk))
    monkeypatch.setattr("app.dsh_bridge.importlib.metadata.version", lambda name: "0.1.5rc1")
    bridge._harness("synthetic-key")
    patch = json.loads((bridge.home / "pm-req.patch.yml").read_text("utf-8"))
    config = next(row["config"] for row in patch if row.get("id") == "llm-deepseek")
    assert SDK_VERSION == "0.1.5rc1" and TRANSPORT == "chat_completions"
    assert captured["base_url"] == config["baseURL"] == "https://api.deepseek.com"
    assert captured["base_url"] + "/chat/completions" == "https://api.deepseek.com/chat/completions"
    assert captured["reasoning_effort"] == config["reasoningEffort"] == "off"


def test_raw_progress_drops_hidden_reasoning_source_prose_and_secret():
    data = dict(type="assistant/message", seq=3, data=dict(message=dict(
        content=[dict(type="thinking", thinking="PRIVATE REASONING"),
                 dict(type="text", text="Long model prose")],
        usage=dict(input_tokens=5, output_tokens=2, reasoning_tokens=1)), api_key="key-123"))
    clean = event_metadata(data, ("key-123",))
    encoded = json.dumps(clean)
    assert "PRIVATE" not in encoded and "Long model" not in encoded and "key-123" not in encoded
    assert clean["data"]["usage"]["reasoning_tokens"] == 1


def test_sanitizer_covers_nested_secret_and_reasoning():
    value = dict(authorization="Bearer key", content=[dict(type="reasoning", text="private"),
        dict(type="text", text="error mentions key-123")], reasoning_content="private")
    assert sanitize_event(value, ("key-123",)) == dict(content=[dict(type="text", text="error mentions [REDACTED]")])


def test_sdk_string_arguments_keep_only_safe_tool_selectors():
    event = dict(type="tool/call", data=dict(callId="call-1", name="read",
        arguments=json.dumps(dict(file_path="materials/S1.md", offset=1, limit=30,
                                  content="private material", api_key="secret"))))
    clean = event_metadata(event)
    assert clean["data"]["arguments"] == dict(file_path="materials/S1.md", offset=1, limit=30)
    assert "private" not in json.dumps(clean) and "secret" not in json.dumps(clean)
    event["data"]["arguments"] = '{broken private material'
    assert event_metadata(event)["data"]["arguments_parse"] == "invalid_json"


def test_sdk_nested_tool_result_exposes_outcome_and_image_identity_not_pixels():
    event = dict(type="tool/result", data=dict(turn=1, step=2, message=dict(
        source=dict(kind="tool", callId="image-1"), content=[dict(type="tool-result",
            toolCallId="image-1", isError=False, content=[dict(type="text", text="private document"),
                dict(type="image", attachment=dict(attachmentId="sha256:abc", mediaType="image/png",
                    bytes=123, width=20, height=10, data="base64 pixels")),
                dict(type="reasoning", text="private reasoning")])])))
    clean = event_metadata(event)["data"]
    assert clean["callId"] == "image-1" and clean["isError"] is False
    assert clean["content_types"] == {"tool-result": 1, "text": 1, "image": 1}
    assert clean["image_attachments"] == [dict(attachmentId="sha256:abc", mediaType="image/png",
        bytes=123, width=20, height=10)]
    assert "private" not in json.dumps(clean) and "base64" not in json.dumps(clean)
    event["data"]["message"]["content"][0]["isError"] = True
    assert event_metadata(event)["data"]["isError"] is True


def test_sdk_top_level_usage_and_request_header_are_retained():
    usage = dict(inputTokens=123, outputTokens=45, cacheReadTokens=100, reasoningTokens=0)
    event = dict(type="assistant/message", data=dict(turn=1, step=2, usage=usage,
        message=dict(content=[dict(type="text", text="private answer")]),
        stream=[dict(type="reasoning-chunks", texts=["private reasoning"])]))
    assert event_metadata(event)["data"]["usage"] == usage
    header = dict(type="request/header", data=dict(header=dict(config=dict(
        model="deepseek-flash", reasoningEffort="off", api_key="secret"))))
    assert event_metadata(header)["data"]["config"] == dict(model="deepseek-flash", reasoningEffort="off")


class FakeHarness:
    def __init__(self, fail=False):
        self.closed = False
        self.fail = fail
    def run(self, prompt, *, session_id, on_notification):
        events = [dict(type="step/start", data={}),
            dict(type="tool/call", data=dict(name="read", arguments=dict(file_path="input-index.json"))),
            dict(type="tool/result", data=dict(isError=False, content=[dict(type="text", text="private source body")])),
            dict(type="assistant/message", data=dict(content=[dict(type="thinking", thinking="private reasoning"),
                dict(type="text", text='{"draft":"候选"}')], usage=dict(input_tokens=123, output_tokens=45)))]
        for event in events:
            on_notification(SimpleNamespace(method="session.event", payload={"event": event}))
        if self.fail:
            raise RuntimeError("synthetic SDK failure")
        return SimpleNamespace(final_response='  {"draft":"候选"}\n', finish_reason="complete")
    def close(self):
        self.closed = True


def test_final_keeps_exact_sdk_text_and_structured_usage(bridge, monkeypatch):
    bridge.prepare()
    fake = FakeHarness()
    monkeypatch.setattr(bridge, "_harness", lambda key: fake)
    result = bridge.run("整理合成资料", api_key="fake-key")
    assert result["final_response"] == '  {"draft":"候选"}\n'
    assert (bridge.evidence / "final.txt").read_text("utf-8") == result["final_response"]
    assert result["request_steps"] == 1 and result["tool_calls"] == 1
    assert result["usage_records"] == [dict(input_tokens=123, output_tokens=45)]
    progress = (bridge.evidence / "events.jsonl").read_text("utf-8")
    assert "private" not in progress and "fake-key" not in progress
    assert fake.closed


def test_sdk_failure_closes_process_and_retains_safe_progress(bridge, monkeypatch):
    bridge.prepare()
    fake = FakeHarness(fail=True)
    monkeypatch.setattr(bridge, "_harness", lambda key: fake)
    with pytest.raises(RuntimeError, match="synthetic SDK"):
        bridge.run("整理", api_key="fake-key")
    assert fake.closed and (bridge.evidence / "events.json").is_file()
    assert not (bridge.evidence / "final.txt").exists()


def test_document_reader_requires_both_fixed_arguments(bridge):
    with pytest.raises(ValueError, match="together"):
        DshBridge(workspace=bridge.workspace, skill_root=bridge.skill_root,
                  dsh_home=bridge.home, evidence_dir=bridge.evidence, document_python="x")


def test_prior_model_evidence_is_never_overwritten(bridge, monkeypatch):
    bridge.prepare()
    prior = bridge.evidence / "final.txt"
    prior.write_text("prior response", encoding="utf-8")
    monkeypatch.setattr(bridge, "_harness", lambda key: pytest.fail("SDK must not start"))
    with pytest.raises(ValueError, match="fresh"):
        bridge.run("another turn", api_key="fake-key")
    assert prior.read_text("utf-8") == "prior response"
