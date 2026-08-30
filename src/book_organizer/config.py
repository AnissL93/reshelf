from pathlib import Path

import yaml
from pydantic import BaseModel


class LibraryConfig(BaseModel):
    root: Path
    incoming: Path
    quarantine: Path


class DatabaseConfig(BaseModel):
    path: Path


class ScanConfig(BaseModel):
    recursive: bool = True
    formats: list[str] = ["epub", "pdf"]


class MatchingConfig(BaseModel):
    auto_accept: float = 0.98
    review_below: float = 0.90
    ai_resolve_below: float = 0.75
    unresolved_below: float = 0.50


class ProviderConfig(BaseModel):
    enabled: bool = True


class ProvidersConfig(BaseModel):
    openlibrary: ProviderConfig = ProviderConfig()


class CacheConfig(BaseModel):
    ttl_days: int = 30


class Config(BaseModel):
    library: LibraryConfig
    database: DatabaseConfig
    scan: ScanConfig = ScanConfig()
    matching: MatchingConfig = MatchingConfig()
    providers: ProvidersConfig = ProvidersConfig()
    cache: CacheConfig = CacheConfig()


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
