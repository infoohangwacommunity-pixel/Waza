"""
Integration tests for Principal continuity across WhatsApp and Telegram.

These require the project's async DB fixtures. They are written so hosted CI
can execute them; local environments without SQLAlchemy/Postgres will skip.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("pytest_asyncio", reason="async tests")

# Attempt project fixtures — if absent, skip module
pytestmark = pytest.mark.asyncio


async def _maybe_session():
    try:
        from wax.db.session import session_scope
        return session_scope
    except Exception:
        return None


async def test_link_then_both_channels_same_principal():
    scope = await _maybe_session()
    if scope is None:
        pytest.skip("db session unavailable")

    from uuid import uuid4
    from wax.db.models import Principal, InterfaceIdentity, Memory
    from wax.domain.identity import (
        link_identity_to_principal,
        resolve_or_create_messaging_identity,
        linked_channels_state,
    )

    async with scope() as session:
        principal = Principal(id=uuid4(), display_name="Kennedy")
        session.add(principal)
        await session.flush()

        wa = await link_identity_to_principal(
            session,
            principal_id=principal.id,
            channel="whatsapp",
            external_id="255700000001",
            make_primary=True,
        )
        tg = await link_identity_to_principal(
            session,
            principal_id=principal.id,
            channel="telegram",
            external_id="10001",
            make_primary=False,
        )
        assert wa.principal_id == principal.id
        assert tg.principal_id == principal.id

        # Inbound resolution must not create a second Principal
        p2, id_wa = await resolve_or_create_messaging_identity(
            session, channel="whatsapp", external_id="255700000001"
        )
        p3, id_tg = await resolve_or_create_messaging_identity(
            session, channel="telegram", external_id="10001"
        )
        assert p2.id == principal.id
        assert p3.id == principal.id
        assert id_wa.id == wa.id
        assert id_tg.id == tg.id

        state = await linked_channels_state(
            session, principal_id=principal.id, current_channel="telegram"
        )
        assert state["whatsapp_linked"] is True
        assert state["telegram_linked"] is True
        assert set(state["linked_channels"]) >= {"whatsapp", "telegram"}

        # Learner data is Principal-scoped — store one memory, visible regardless of channel
        session.add(
            Memory(
                id=uuid4(),
                principal_id=principal.id,
                memory_type="goal",
                content="Working on quadratic equations",
                confidence=0.8,
                importance=0.7,
                source="test",
                is_active=True,
            )
        )
        await session.flush()

        # Conflict: another principal cannot take telegram 10001
        other = Principal(id=uuid4(), display_name="Other")
        session.add(other)
        await session.flush()
        from wax.domain.identity import IdentityConflictError

        with pytest.raises(IdentityConflictError):
            await link_identity_to_principal(
                session,
                principal_id=other.id,
                channel="telegram",
                external_id="10001",
                allow_reassign=False,
            )


async def test_already_linked_is_idempotent():
    scope = await _maybe_session()
    if scope is None:
        pytest.skip("db session unavailable")

    from uuid import uuid4
    from wax.db.models import Principal
    from wax.domain.identity import link_identity_to_principal, list_identities

    async with scope() as session:
        principal = Principal(id=uuid4(), display_name="Sam")
        session.add(principal)
        await session.flush()
        a = await link_identity_to_principal(
            session, principal_id=principal.id, channel="telegram", external_id="20002"
        )
        b = await link_identity_to_principal(
            session, principal_id=principal.id, channel="telegram", external_id="20002"
        )
        assert a.id == b.id
        ids = await list_identities(session, principal.id)
        tg = [i for i in ids if i.channel == "telegram" and i.external_id == "20002"]
        assert len(tg) == 1
