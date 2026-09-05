from reshelf.config import default_config, load_config, save_config


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
    assert loaded.library.convert_to_epub is True
    assert loaded.providers.openlibrary.enabled is True
    assert loaded.cache.ttl_days == 30
