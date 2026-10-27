"""Validate database configuration only when the ledger is used."""

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import URL

from deposition_toolkit.config import get_settings


class LedgerSettings(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    user: str = Field(min_length=1)
    password: SecretStr
    database: str = Field(min_length=1)

    @property
    def url(self) -> URL:
        # URL.create accepts unescaped credentials, including @, %, and /.
        return URL.create(
            "postgresql+psycopg",
            username=self.user,
            password=self.password.get_secret_value(),
            host=self.host,
            port=self.port,
            database=self.database,
        )


def get_ledger_settings() -> LedgerSettings:
    settings = get_settings()
    # Match PostgreSQL's default: use the login role as the database name.
    database = settings.postgres_database
    if not database or not database.strip():
        database = settings.postgres_user
    return LedgerSettings(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        database=database,
    )
