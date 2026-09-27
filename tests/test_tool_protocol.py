
from wax.intelligence.tool_protocol import (
    content_has_tool_protocol_leak,
    normalize_completion_payload,
    normalize_tool_calls,
    parse_arguments,
    sanitize_learner_reply,
    strip_tool_protocol_from_content,
)


def test_parse_arguments_json_string():
    assert parse_arguments('{"html": "<p>hi</p>", "title": "T"}')["title"] == "T"


def test_normalize_tool_calls_string_args():
    raw = [{
        "id": "1",
        "type": "function",
        "function": {"name": "create_surface", "arguments": '{"title": "Cells", "html": "<html></html>"}'},
    }]
    out = normalize_tool_calls(raw)
    assert out[0]["function"]["name"] == "create_surface"
    assert out[0]["function"]["arguments"]["title"] == "Cells"


def test_leak_detection_and_sanitize():
    leak = "<|tool_call_begin|>create_surface\n<html><body>x</body></html>"
    assert content_has_tool_protocol_leak(leak)
    cleaned = sanitize_learner_reply(leak)
    assert "<|tool_call" not in cleaned
    assert "<html" not in cleaned.lower() or "interactive" in cleaned.lower()


def test_normalize_payload_strips_leak_without_inventing_tools():
    norm = normalize_completion_payload(
        content="<|tool_call_begin|>create_surface foo",
        tool_calls=[],
        provider="upstage",
        model="solar",
    )
    assert norm["tool_calls"] == []
    assert not content_has_tool_protocol_leak(norm.get("content") or "")
