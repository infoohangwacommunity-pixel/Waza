"""Factual student context vs AI-owned personalization — one system, not two.

Audit rule: infrastructure may expose FACTS (display name, current channel,
time, linked identities, first contact, explicit stored settings). The AI
decides what they mean. Judgment-bearing personalization (teaching method,
ability, weaknesses, learning style, emotional state, curriculum position)
must NOT be an application-defined store or selection layer — it lives in the
AI's own notebook/memory. These tests prove that split and principal
isolation.
"""

from __future__ import annotations

import ast
import pathlib
import uuid

import pytest

from wax.db.models import Conversation, InterfaceIdentity, Message, Principal
from wax.domain.profile import factual_context


async def _seed_two(session):
    alice = Principal(id=uuid.uuid4(), display_name="Alice")
    bob = Principal(id=uuid.uuid4(), display_name="Bob")
    session.add_all([alice, bob])
    a_ident = InterfaceIdentity(
        id=uuid.uuid4(), principal_id=alice.id, channel="whatsapp",
        external_id="wa-alice", display_name="Ali", is_primary=True,
    )
    b_ident = InterfaceIdentity(
        id=uuid.uuid4(), principal_id=bob.id, channel="telegram",
        external_id="tg-bob",
    )
    a_conv = Conversation(id=uuid.uuid4(), principal_id=alice.id, channel="whatsapp")
    session.add_all([a_ident, b_ident, a_conv])
    await session.flush()
    m = Message(
        id=uuid.uuid4(), principal_id=alice.id, conversation_id=a_conv.id,
        channel="whatsapp", direction="inbound", role="user",
        content="hi", external_id=f"seed-{uuid.uuid4()}",
    )
    session.add(m)
    await session.flush()
    return alice, bob, a_conv


@pytest.mark.asyncio
async def test_factual_context_reports_only_facts(session):
    alice, _, _ = await _seed_two(session)

    ctx = await factual_context(session, alice.id)
    assert ctx["found"] is True
    assert ctx["display_name"] == "Alice"
    # Retired preferences column is NOT surfaced: notebook/memory are the
    # single personalization authority (see test_preferences_surface_retired).
    assert "preferences" not in ctx
    assert ctx["first_message_at"]  # first-contact fact from real rows
    assert ctx["created_at"]
    chans = {i["channel"]: i["external_id"] for i in ctx["linked_channel_identities"]}
    assert chans == {"whatsapp": "wa-alice"}
    assert len(ctx["conversations"]) == 1

    # No interpretation fields: nothing ranked, scored, summarized or inferred.
    banned_keys = {
        "relevance", "score", "importance", "summary", "learning_style",
        "ability", "weaknesses", "emotional_state", "curriculum_position",
        "preferred_teaching_method",
    }
    flat = str(ctx).lower()
    for k in banned_keys:
        assert k not in flat


@pytest.mark.asyncio
async def test_principal_isolation_in_factual_context(session):
    alice, bob, _ = await _seed_two(session)
    ctx_bob = await factual_context(session, bob.id)
    assert ctx_bob["display_name"] == "Bob"
    assert ctx_bob["first_message_at"] is None
    assert [i["channel"] for i in ctx_bob["linked_channel_identities"]] == ["telegram"]
    assert ctx_bob["conversations"] == []
    # Bob's context never leaks Alice's facts.
    assert "wa-alice" not in str(ctx_bob)
    assert "Alice" not in str(ctx_bob)


@pytest.mark.asyncio
async def test_missing_principal_is_reported_not_guessed(session):
    ctx = await factual_context(session, uuid.uuid4())
    assert ctx == {"found": False}


def test_profile_module_has_no_intelligence_primitives():
    """profile.py must stay a mechanical read: no ranking/selection/embedding."""
    import io
    import tokenize

    src = pathlib.Path("wax/domain/profile.py").read_text()
    # Strip comments and string literals; scan only executable identifiers.
    idents = {
        t.string
        for t in tokenize.generate_tokens(io.StringIO(src).readline)
        if t.type == tokenize.NAME
    }
    joined = " ".join(sorted(idents)).lower()
    banned = ("embed", "similar", "rank", "summariz", "extract", "classif",
              "importance", "select_relevant")
    for word in banned:
        assert word not in joined, f"profile.py contains intelligence identifier {word!r}"


def test_single_personalization_authority_in_prompt():
    """Tutor prompt must point judgment-bearing notes to notebook/memory and
    teach ONE personalization authority — no separate preferences store."""
    from wax.intelligence.tutor import TUTOR_SYSTEM

    t = TUTOR_SYSTEM
    assert "notebook/" in t and "memory" in t
    assert "no separate preferences store" in t
    assert "preference_op" not in t  # retired surface stays out of the prompt
