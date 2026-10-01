import pytest
from portfolio_os.config import Config
from portfolio_os.domain import RuleError

@pytest.mark.parametrize("url", [
    "postgresql+psycopg://user:sslmode=verify-full@db.example/postgres?sslmode=disable",
    "postgresql+psycopg://user:pw@db.example/postgres?sslmode=verify-full&sslmode=disable",
    "postgresql+psycopg://user:pw@db.example/postgres?note=sslmode=verify-full",
    "postgresql+psycopg://user:pw@db.example/postgres?sslmode=verify-full&host=other.example",
    "postgresql+psycopg://user:pw@db.example/postgres?sslmode=verify-full&service=other",
])
def test_rejects_tls_bypass(monkeypatch, tmp_path, url):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", url)
    with pytest.raises(RuleError):
        Config.load()

def test_verified_tls_url(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@db.example/postgres?sslmode=verify-full")
    assert Config.load().database_url.startswith("postgresql+psycopg://")
