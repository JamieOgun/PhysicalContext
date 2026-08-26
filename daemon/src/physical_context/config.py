from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="PCL_",
        extra="ignore",
    )

    anthropic_api_key: SecretStr | None = None
    voyage_api_key: SecretStr | None = None
    host: str = "0.0.0.0"
    port: int = Field(default=8787, ge=1, le=65535)
    data_root: Path = Path("~/.pcl")
    local_caption: bool = False
    local_embed: bool = False

    @property
    def resolved_data_root(self) -> Path:
        return self.data_root.expanduser().resolve()

    @property
    def captures_dir(self) -> Path:
        return self.resolved_data_root / "captures"

    @property
    def database_path(self) -> Path:
        return self.resolved_data_root / "physical_context.db"
