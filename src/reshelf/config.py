from pathlib import Path

import yaml
from pydantic import BaseModel


class LibraryConfig(BaseModel):
    root: Path
    incoming: Path
    quarantine: Path
    convert_to_epub: bool = True  # convert Kindle formats to EPUB on commit


class DatabaseConfig(BaseModel):
    path: Path


class ScanConfig(BaseModel):
    recursive: bool = True
    formats: list[str] = ["epub", "pdf", "mobi", "azw", "azw3"]


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
    enabled: bool = True
    resolver_only: bool = True  # AI selects among candidates, never invents metadata
    model: str = "haiku"  # passed to `claude -p --model`; fastest/cheapest tier
    timeout_seconds: int = 180


class Config(BaseModel):
    library: LibraryConfig
    database: DatabaseConfig
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
