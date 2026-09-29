"""Preferences storage is mechanical key/value; the AI-facing surface is retired.

History: an old DEFAULT_PREFERENCES template (language/tone/emoji/message_length/
quiet_hours) and is_in_quiet_hours gate encoded application-defined
personalization policy — deleted. Then `state action: preferences` was also
retired so notebook/memory form a SINGLE personalization authority instead of
two competing stores. The Principal.preferences column remains as durable
storage only (no schema, no defaults); nothing in wax/ reads or writes it on
the runtime path. These tests lock all of that in.
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


def test_ai_facing_preferences_surface_is_retired():
    """No tutor code may read/write Principal.preferences via a directive —
    one personalization authority (notebook + memory). profile.py must not
    surface the retired column either."""
    tutor = pathlib.Path("wax/intelligence/tutor.py").read_text()
    assert "_preferences" not in tutor
    assert "preference_op" not in tutor
    # The state directive answers with a clear retirement notice instead.
    assert "preferences_retired" in tutor
    # profile.py must not touch the preferences column at all — prose in the
    # module docstring is allowed; executable code and comments are not.
    import io, tokenize
    profile_src = pathlib.Path("wax/domain/profile.py").read_text()
    offenders = []
    for tok in tokenize.generate_tokens(io.StringIO(profile_src).readline):
        if tok.type == tokenize.COMMENT and "preference" in tok.string.lower():
            offenders.append(tok.string)
        elif tok.type == tokenize.NAME and tok.string == "preferences":
            offenders.append(f"NAME token at line {tok.start[0]}")
    assert offenders == [], f"profile.py references preferences outside docstrings: {offenders}"


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
