import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel


class LibraryConfig(BaseModel):
    root: Path
    incoming: Path
    quarantine: Path
    commit_mode: Literal["copy", "move"] = "copy"


class DatabaseConfig(BaseModel):
    path: Path


class MetadataConfig(BaseModel):
    """Where per-book JSON sidecars live. Sidecars are the source of truth."""

    layout: Literal["hash", "sidecar", "library"] = "hash"
    dir: Path = Path("metadata")


class ConvertConfig(BaseModel):
    dir: Path = Path("derived")
    timeout: int = 300


class WebConfig(BaseModel):
    # No auth is implemented; do not default to 0.0.0.0.
    host: str = "127.0.0.1"
    port: int = 8080


class WriteBackConfig(BaseModel):
    library_file: bool = False
    embed: bool = False


class ScanConfig(BaseModel):
    recursive: bool = True
    # txt and djvu are here because Phase 1 gave them converters
    # (TXT->EPUB, DjVu->PDF), /capabilities advertises both and the UI
    # offers both as filters. Leaving them out of the default meant those
    # files could never enter the library in the first place.
    formats: list[str] = ["epub", "pdf", "mobi", "azw", "azw3", "txt", "djvu"]


class MatchingConfig(BaseModel):
    auto_accept: float = 0.98
    review_below: float = 0.90
    ai_resolve_below: float = 0.75
    unresolved_below: float = 0.50


class ProviderConfig(BaseModel):
    enabled: bool = True


class DoubanConfig(ProviderConfig):
    # Community key for the legacy v2 API; replace with your own if you have one.
    apikey: str = "0ac44ae016490db2204ce0a042db2916"


class ProvidersConfig(BaseModel):
    openlibrary: ProviderConfig = ProviderConfig()
    douban: DoubanConfig = DoubanConfig()


class CacheConfig(BaseModel):
    ttl_days: int = 30


class AIConfig(BaseModel):
    provider: Literal["claude-cli", "api"] | None = None
    model: str = "haiku"
    api_key: str | None = None
    base_url: str | None = None
    resolver_only: bool = True  # AI selects among candidates, never invents metadata
    timeout_seconds: int = 180

    @property
    def enabled(self) -> bool:
        return self.provider is not None

    @property
    def resolved_api_key(self) -> str | None:
        return self.api_key or os.environ.get("RESHELF_AI_API_KEY")


class Config(BaseModel):
    library: LibraryConfig
    database: DatabaseConfig
    metadata: MetadataConfig = MetadataConfig()
    convert: ConvertConfig = ConvertConfig()
    web: WebConfig = WebConfig()
    write_back: WriteBackConfig = WriteBackConfig()
    scan: ScanConfig = ScanConfig()
    matching: MatchingConfig = MatchingConfig()
    providers: ProvidersConfig = ProvidersConfig()
    cache: CacheConfig = CacheConfig()
    ai: AIConfig = AIConfig()


def default_config(root: Path) -> Config:
    root = Path(root).resolve()
    return Config(
        library=LibraryConfig(
            root=root,
            incoming=root / "incoming",
            quarantine=root / "quarantine",
        ),
        database=DatabaseConfig(path=root / "db" / "books.sqlite3"),
    )


def save_config(cfg: Config, root: Path) -> None:
    (Path(root) / "config.yaml").write_text(
        yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    )


def load_config(root: Path) -> Config:
    data = yaml.safe_load((Path(root) / "config.yaml").read_text())
    return Config.model_validate(data)
