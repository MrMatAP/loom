from pydantic import Field, SecretStr, computed_field

from .base import RootConfigAware


class DatabaseConfig(RootConfigAware):
    """Connection settings for the Postgres-backed registry database."""

    host: str = Field(default='localhost', description='Database host')
    port: int = Field(default=5432, description='Database port')
    database: str = Field(default='loom', description='Database name')
    username: str = Field(default='loom', description='Database username')
    password: SecretStr | None = Field(default=None, description='Database password')

    @computed_field
    @property
    def dsn(self) -> str:
        """The SQLAlchemy connection string for this database."""
        auth = self.username
        if self.password is not None:
            auth = f'{self.username}:{self.password.get_secret_value()}'
        return f'postgresql+psycopg://{auth}@{self.host}:{self.port}/{self.database}'
