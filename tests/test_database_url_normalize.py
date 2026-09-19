
from wax.config.settings import Settings

def test_postgres_scheme():
    s = Settings(database_url="postgres://u:p@host:5432/db")
    assert s.database_url.startswith("postgresql+asyncpg://")

def test_postgresql_scheme():
    s = Settings(database_url="postgresql://u:p@host:5432/db")
    assert s.database_url.startswith("postgresql+asyncpg://")
    assert "+psycopg2" not in s.database_url

def test_already_asyncpg():
    s = Settings(database_url="postgresql+asyncpg://u:p@host:5432/db")
    assert s.database_url == "postgresql+asyncpg://u:p@host:5432/db"
