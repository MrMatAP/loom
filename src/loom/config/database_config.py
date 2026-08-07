from pydantic import Field, SecretStr, SerializationInfo, field_serializer

from .base import RootConfigAware


class DatabaseConfig(RootConfigAware):
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

    @property
    def dsn(self) -> str:
        """The SQLAlchemy connection string for this database."""
        auth = self.username
        if self.password is not None:
            auth = f'{self.username}:{self.password.get_secret_value()}'
        return f'postgresql+psycopg://{auth}@{self.host}:{self.port}/{self.database}'
