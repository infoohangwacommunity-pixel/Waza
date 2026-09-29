"""Preferences are schema-free mechanical storage; quiet-hours policy is gone.

The old DEFAULT_PREFERENCES template (language/message_length/tone/emoji/
quiet_hours) and is_in_quiet_hours helper encoded application-defined
personalization policy — teaching style and engagement decisions belong to
the AI, not infrastructure. These tests lock that removal in.
"""

import pathlib
import re


def test_no_preference_schema_template_remains():
    src = pathlib.Path("wax/domain/preferences.py").read_text()
    assert "DEFAULT_PREFERENCES" not in src
    for banned in ("message_length", "tone", "emoji", "quiet_hours"):
        # must not appear as a defined key anywhere in the module code
        assert f'"{banned}"' not in src


def test_no_quiet_hours_gate_exists():
    root = pathlib.Path("wax")
    offenders = []
    for p in root.rglob("*.py"):
        text = p.read_text()
        if "is_in_quiet_hours" in text or re.search(r"\bquiet_hours\b", text):
            offenders.append(str(p))
    assert offenders == []


def test_get_preferences_returns_only_stored_values():
    import asyncio
    from wax.db.models import Principal
    from wax.domain.preferences import get_preferences

    async def run():
        async with _make_session() as s:
            p = Principal(display_name="Ada")
            s.add(p)
            await s.flush()
            # Nothing stored -> empty dict, no injected defaults.
            assert await get_preferences(s, p.id) == {}
            p.preferences = {"timezone": "UTC+1"}
            await s.flush()
            assert await get_preferences(s, p.id) == {"timezone": "UTC+1"}

    asyncio.run(run())


def _make_session():
    from sqlalchemy import JSON, TypeDecorator, Uuid
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    import wax.db.models as models

    class PgJsonLikeJSONB(TypeDecorator):
        impl = JSON
        cache_ok = True

        def get_col_spec(self, **kw):  # pragma: no cover
            return "JSON"

        def load_dialect_impl(self, dialect):
            if dialect.name == "postgresql":
                return dialect.type_descriptor(postgresql.JSONB())
            return dialect.type_descriptor(JSON())

    class PgUuidLikeUUID(TypeDecorator):
        impl = Uuid
        cache_ok = True

        def load_dialect_impl(self, dialect):
            if dialect.name == "postgresql":
                return dialect.type_descriptor(postgresql.UUID())
            return dialect.type_descriptor(Uuid())

    models.JSONB = PgJsonLikeJSONB
    models.UUID = PgUuidLikeUUID
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    class _Ctx:
        async def __aenter__(self_inner):
            async with engine.begin() as conn:
                await conn.run_sync(models.Base.metadata.create_all)
            Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            self_inner.s = Session()
            return self_inner.s

        async def __aexit__(self_inner, *a):
            await self_inner.s.close()
            await engine.dispose()

    return _Ctx()
