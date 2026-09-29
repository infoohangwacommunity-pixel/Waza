"""The tutor system prompt must teach channel FORMATS, not canned content.

Hardcoded example payloads ("preferred explanation style", "Practice sheet",
"Which path?", fixed delays...) risk becoming de-facto routing/selection
policy if the model merely copies them. The prompt must show only
placeholder-shaped examples so every real value stays AI-owned.
"""

import re

from wax.intelligence.tutor import TUTOR_SYSTEM

# Canned payload strings that once appeared as literal examples.
BANNED_LITERAL_EXAMPLES = [
    "preferred explanation style",
    "Student prefers short worked examples",
    "explanation style",
    "tags: teaching",
    "delay_seconds: 30",
    "short pause then continue",
    "title: Practice sheet",
    "lifetime_hours: 168",
    "prompt: Which path?",
    "- More examples",
    "- Try a problem",
]


def test_no_canned_example_payloads_in_system_prompt():
    for probe in BANNED_LITERAL_EXAMPLES:
        assert probe not in TUTOR_SYSTEM, f"canned example leaked into prompt: {probe!r}"


def test_state_search_example_uses_placeholders_not_suggested_queries():
    # No literal 'query:' example remains; terms are placeholder-shaped.
    assert "query:" not in TUTOR_SYSTEM
    assert 'terms: ["your own words here"]' in TUTOR_SYSTEM


def test_directive_examples_are_placeholder_shaped():
    # Every templated field is angle-bracketed or explicitly "your own".
    placeholders = [
        "<a durable fact you decided is worth keeping>",
        "<your own type>",
        "[<your own tags>]",
        "delay_seconds: <number>",
        "reason: <why this wake-up exists>",
        "title: <your title>",
        "lifetime_hours: <number>",
        "prompt: <question to the student>",
        "- <option one>",
        "- <option two>",
    ]
    for p in placeholders:
        assert p in TUTOR_SYSTEM, f"missing placeholder example: {p}"


def test_no_keyword_trigger_heuristics_in_prompt():
    # Behavioural guidance only: no imperative "if message contains X -> do Y"
    # workflow rules baked into infrastructure text.
    trigger = re.compile(
        r"\bif (?:the )?(?:student|user|they) (?:says|ask[s]? about|mentions)\b",
        re.IGNORECASE,
    )
    assert not trigger.search(TUTOR_SYSTEM), "keyword-trigger workflow found in prompt"


def test_channel_contract_still_parses_after_placeholder_edit():
    # Sanity: the directive bridge still understands the documented grammar.
    from wax.intelligence.directives import parse_agent_output

    out = parse_agent_output(
        '```state\naction: search\nterms: ["anything"]\nmode: any\n```\nreply'
    )
    assert len(out.directives) == 1
    assert out.directives[0].channel == "state"
