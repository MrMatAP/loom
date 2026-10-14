from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, Field, SecretStr, SerializationInfo, field_serializer


class DatabaseConfig(BaseModel):
    """Connection settings for the Postgres-backed registry database."""

    host: str = Field(default='localhost', description='Database host')
    port: int = Field(default=5432, description='Database port')
    database: str = Field(default='loom', description='Database name')
    username: str = Field(default='loom', description='Database username')
    password: SecretStr | None = Field(default=None, description='Database password')

    @field_serializer('password')
    def _serialize_password(
        self, value: SecretStr | None, info: SerializationInfo
    ) -> str | None:
        """Reveal the real secret only when explicitly requested via context."""
        if value is None:
            return None
        if info.context and info.context.get('reveal_secrets'):
            return value.get_secret_value()
        return '**********'

    @classmethod
    def env_values(cls, environ: Mapping[str, str]) -> dict[str, str]:
        """The `LOOM_DB_*` variables that are set, keyed by field name."""
        names = {
            'host': 'LOOM_DB_HOST',
            'port': 'LOOM_DB_PORT',
            'database': 'LOOM_DB_NAME',
            'username': 'LOOM_DB_USERNAME',
            'password': 'LOOM_DB_PASSWORD',
        }
        return {field: environ[var] for field, var in names.items() if environ.get(var)}

    def location_only(self) -> DatabaseConfig:
        """A copy carrying host/port/database but none of the credentials."""
        return DatabaseConfig(host=self.host, port=self.port, database=self.database)

    @property
    def dsn(self) -> str:
        """The SQLAlchemy connection string for this database."""
        auth = self.username
        if self.password is not None:
            auth = f'{self.username}:{self.password.get_secret_value()}'
        return f'postgresql+psycopg://{auth}@{self.host}:{self.port}/{self.database}'
