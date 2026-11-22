from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from biolink_backend.config.settings import Settings


def _base_kwargs() -> dict:
    return {
        "_env_file": None,
        "app_env": "test",
        "auth_secret_key": SecretStr("VALID_SECRET_KEY_AT_LEAST_32_CHARS_LONG"),
        "postgres_password": SecretStr("pg_secret"),
        "mongodb_password": SecretStr("mongo_secret"),
        "neo4j_password": SecretStr("neo4j_secret"),
        "cors_allowed_origins": ["http://127.0.0.1:3000"],
    }


def test_env_example_loads_cleanly() -> None:
    env_example = Path(__file__).resolve().parent.parent / ".env.example"
    assert env_example.exists()
    settings = Settings(_env_file=str(env_example))
    assert settings.app_name == "biolink-backend"
    assert settings.postgres_image == "postgres:18.7"
    assert settings.mongodb_image == "mongo:8.0.38"
    assert settings.neo4j_image == "neo4j:2026.10.2-community"
    budget = settings.memory_budget_summary()
    assert budget["containers_total_mb"] == 3072
    assert budget["reserved_headroom_mb"] == 1024


def test_secret_masking_in_dsns() -> None:
    settings = Settings(**_base_kwargs())
    assert "pg_secret" not in settings.postgres_masked_dsn
    assert "***" in settings.postgres_masked_dsn
    assert "mongo_secret" not in settings.mongodb_masked_uri
    assert "***" in settings.mongodb_masked_uri


@pytest.mark.parametrize("field_name,bad_image", [
    ("postgres_image", "postgres:latest"),
    ("mongodb_image", "mongo:latest"),
    ("neo4j_image", "neo4j:latest"),
    ("postgres_image", "postgres"),
])
def test_rejects_latest_or_untagged_images(field_name: str, bad_image: str) -> None:
    kwargs = _base_kwargs()
    kwargs[field_name] = bad_image
    with pytest.raises(ValidationError):
        Settings(**kwargs)


def test_rejects_wildcard_cors_and_parses_csv_origins() -> None:
    kwargs = _base_kwargs()
    kwargs["cors_allowed_origins"] = "http://127.0.0.1:3000, https://biolink.example.org"
    parsed = Settings(**kwargs)
    assert parsed.cors_allowed_origins == ["http://127.0.0.1:3000", "https://biolink.example.org"]

    bad_kwargs = _base_kwargs()
    bad_kwargs["cors_allowed_origins"] = ["*"]
    with pytest.raises(ValidationError):
        Settings(**bad_kwargs)


def test_rejects_short_auth_secret_and_empty_db_passwords() -> None:
    kwargs = _base_kwargs()
    kwargs["auth_secret_key"] = SecretStr("too-short")
    with pytest.raises(ValidationError):
        Settings(**kwargs)

    for pwd_field in ("postgres_password", "mongodb_password", "neo4j_password"):
        bad = _base_kwargs()
        bad[pwd_field] = SecretStr("   ")
        with pytest.raises(ValidationError):
            Settings(**bad)


def test_enforces_4gb_memory_budget_limits() -> None:
    for field_name, bad_val in (
        ("postgres_mem_limit_mb", 769),
        ("mongodb_mem_limit_mb", 769),
        ("neo4j_mem_limit_mb", 1025),
    ):
        bad = _base_kwargs()
        bad[field_name] = bad_val
        with pytest.raises(ValidationError):
            Settings(**bad)

    combined_bad = _base_kwargs()
    combined_bad["backend_mem_limit_mb"] = 350
    combined_bad["worker_mem_limit_mb"] = 200
    with pytest.raises(ValidationError):
        Settings(**combined_bad)


def test_production_mode_rejects_placeholder_secrets_and_insecure_cookies() -> None:
    kwargs = _base_kwargs()
    kwargs["app_env"] = "production"
    kwargs["postgres_password"] = SecretStr("CHANGE_ME_PG_SECRET")
    kwargs["session_cookie_secure"] = True
    with pytest.raises(ValidationError):
        Settings(**kwargs)

    kwargs_cookie = _base_kwargs()
    kwargs_cookie["app_env"] = "production"
    kwargs_cookie["session_cookie_secure"] = False
    with pytest.raises(ValidationError):
        Settings(**kwargs_cookie)
