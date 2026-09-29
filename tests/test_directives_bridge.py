"""Minimal AI→infrastructure bridge — canonical channels only."""

from wax.intelligence.directives import parse_agent_output, CHANNELS, Directive


def test_canonical_channels_only():
    # Core infrastructure channels + identity linking channels
    assert CHANNELS == frozenset({
        "world", "state", "time", "publish", "interact",
        "link", "link_request",
    })


def test_parse_world_and_state():
    text = (
        "Here is help.\n\n"
        "```world\npython3 -c 'print(2)'\n```\n\n"
        "```state\naction: search\nquery: style\n```\n"
    )
    turn = parse_agent_output(text)
    assert "Here is help" in turn.reply
    assert len(turn.directives) == 2
    assert turn.directives[0].channel == "world"
    assert "print(2)" in turn.directives[0].fields.get("command", "")
    assert turn.directives[1].channel == "state"
    assert turn.directives[1].fields["action"] == "search"
    assert turn.directives[1].fields["query"] == "style"


def test_old_aliases_ignored():
    text = "```memory\naction: create\ncontent: x\n```\n```schedule\ndelay_seconds: 5\n```\n```choices\n- A\n```"
    turn = parse_agent_output(text)
    assert turn.directives == []


def test_interact_bullets():
    text = "```interact\nWhich path?\n- More examples\n- Try a problem\n```"
    turn = parse_agent_output(text)
    assert len(turn.directives) == 1
    d = turn.directives[0]
    assert d.channel == "interact"
    assert d.fields["prompt"] == "Which path?"
    assert d.fields["choices"] == ["More examples", "Try a problem"]


def test_directive_has_fields_not_parsed_or_kind():
    d = Directive(channel="world", body="echo hi", fields={"command": "echo hi"})
    assert d.channel == "world"
    assert hasattr(d, "fields")
    assert not hasattr(d, "parsed")
    assert not hasattr(d, "kind")
