"""Boot a throwaway Postgres for the test session if one isn't already configured.

If DATABASE_URL already points at a reachable Postgres, this does nothing. If
`pgserver` is installed (a self-contained Postgres), it starts one and points
DATABASE_URL at it. Otherwise the integration tests skip themselves.
"""
import os

_server = None


def pytest_configure(config):
    global _server
    if os.environ.get("DATABASE_URL"):
        return
    try:
        import pgserver
    except Exception:
        return
    try:
        _server = pgserver.get_server("/tmp/pgdata_test")
        try:
            _server.psql("CREATE DATABASE agentic;")
        except Exception:
            pass  # already exists
        uri = _server.get_uri(database="agentic").replace(
            "postgresql://", "postgresql+asyncpg://"
        )
        os.environ["DATABASE_URL"] = uri
        os.environ.setdefault("LLM_MODE", "mock")
    except Exception:
        _server = None


def pytest_unconfigure(config):
    global _server
    if _server is not None:
        try:
            _server.cleanup()
        except Exception:
            pass
        _server = None
