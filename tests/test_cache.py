import json
from datetime import datetime, timedelta, timezone

from reshelf.providers.cache import FileCache


def test_roundtrip_and_miss(tmp_path):
    cache = FileCache(tmp_path)
    assert cache.get("isbn:9780765382030") is None
    cache.put("isbn:9780765382030", {"title": "三体"})
    assert cache.get("isbn:9780765382030") == {"title": "三体"}


def test_expiry(tmp_path):
    cache = FileCache(tmp_path, ttl_days=30)
    cache.put("search:x", {"a": 1})
    path = next(tmp_path.glob("*.json"))
    rec = json.loads(path.read_text())
    rec["retrieved_at"] = (
        datetime.now(timezone.utc) - timedelta(days=31)
    ).isoformat()
    path.write_text(json.dumps(rec))
    assert cache.get("search:x") is None


def test_key_sanitization(tmp_path):
    cache = FileCache(tmp_path)
    cache.put("search:three body/problem:liu cixin", {"ok": True})
    assert cache.get("search:three body/problem:liu cixin") == {"ok": True}
    assert all("/" not in p.name for p in tmp_path.glob("*.json"))
