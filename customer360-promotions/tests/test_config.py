"""Configuration tests for local shared-database and promotions-specific settings."""

from sqlalchemy.engine import make_url

from core.config import PromotionsSettings


def test_shared_database_environment_is_supported_for_local_development(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "DB_HOST=shared-postgres",
                "DB_PORT=5544",
                "DB_USER=core-user",
                "DB_PASSWORD=shared-local-password",
                "DB_NAME=customer360",
            ]
        )
    )

    settings = PromotionsSettings(_env_file=env_file)

    assert settings.db_host == "shared-postgres"
    assert settings.db_port == 5544
    assert settings.db_user == "core-user"
    assert settings.db_password == "shared-local-password"
    assert settings.db_name == "customer360"


def test_promotions_specific_database_settings_take_precedence(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DB_HOST=shared-postgres\nDB_PASSWORD=shared-local-password\n"
    )
    monkeypatch.setenv("c360_PROMOTION_DB_HOST", "promotion-postgres")
    monkeypatch.setenv("c360_PROMOTION_DB_PASSWORD", "promotion-local-password")

    settings = PromotionsSettings(_env_file=env_file)

    assert settings.db_host == "promotion-postgres"
    assert settings.db_password == "promotion-local-password"


def test_database_url_escapes_special_characters_in_credentials():
    settings = PromotionsSettings(
        _env_file=None,
        c360_PROMOTION_DB_USER="promotion-user",
        c360_PROMOTION_DB_PASSWORD="p@ss:/# word",
        c360_PROMOTION_DB_HOST="postgres.internal",
        c360_PROMOTION_DB_PORT=5432,
        c360_PROMOTION_DB_NAME="customer360",
    )

    parsed = make_url(settings.database_url)

    assert parsed.username == "promotion-user"
    assert parsed.password == "p@ss:/# word"
    assert parsed.host == "postgres.internal"
    assert parsed.database == "customer360"
