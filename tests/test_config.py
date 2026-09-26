from pathlib import Path

import pytest

from reshelf.config import AIConfig, Config, default_config, load_config, save_config


def test_default_roundtrip(tmp_path):
    cfg = default_config(tmp_path)
    save_config(cfg, tmp_path)
    loaded = load_config(tmp_path)
    assert loaded == cfg
    assert loaded.library.incoming == tmp_path.resolve() / "incoming"
    assert loaded.database.path == tmp_path.resolve() / "db" / "books.sqlite3"
    assert loaded.matching.auto_accept == 0.98
    assert loaded.matching.review_below == 0.90
    assert loaded.matching.ai_resolve_below == 0.75
    assert loaded.matching.unresolved_below == 0.50
    assert loaded.scan.formats == ["epub", "pdf", "mobi", "azw", "azw3"]
    assert loaded.providers.openlibrary.enabled is True
    assert loaded.cache.ttl_days == 30


def test_new_sections_have_defaults(tmp_path):
    cfg = default_config(tmp_path)
    assert cfg.metadata.layout == "hash"
    assert cfg.metadata.dir == Path("metadata")
    assert cfg.convert.dir == Path("derived")
    assert cfg.convert.timeout == 300
    assert cfg.web.host == "127.0.0.1"
    assert cfg.web.port == 8080
    assert cfg.write_back.library_file is False
    assert cfg.write_back.embed is False
    assert cfg.library.commit_mode == "copy"


def test_ai_is_off_unless_a_provider_is_configured(tmp_path):
    cfg = default_config(tmp_path)
    assert cfg.ai.provider is None
    assert cfg.ai.enabled is False


def test_ai_enabled_once_a_provider_is_set():
    assert AIConfig(provider="claude-cli").enabled is True


def test_api_key_falls_back_to_the_environment(monkeypatch):
    monkeypatch.delenv("RESHELF_AI_API_KEY", raising=False)
    assert AIConfig(provider="api").resolved_api_key is None
    monkeypatch.setenv("RESHELF_AI_API_KEY", "sk-test")
    assert AIConfig(provider="api").resolved_api_key == "sk-test"
    assert AIConfig(provider="api", api_key="explicit").resolved_api_key == "explicit"


def test_commit_mode_rejects_nonsense(tmp_path):
    cfg = default_config(tmp_path).model_dump(mode="json")
    cfg["library"]["commit_mode"] = "teleport"
    with pytest.raises(ValueError):
        Config.model_validate(cfg)
