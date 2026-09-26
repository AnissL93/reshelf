import warnings

import pytest

# Importing fastapi.testclient itself triggers Starlette's httpx2 nag, at
# collection time (before a filterwarnings mark on a test would apply) - so
# it's silenced here, before the import, rather than as a test mark.
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from reshelf.config import default_config, save_config
from reshelf.web.app import create_app

# capabilities() imports the `mobi` package, which warns about Python 3.13
# dropping imghdr - upstream noise unrelated to this test file's behaviour.
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def client(tmp_path):
    cfg = default_config(tmp_path)
    for sub in ("incoming", "library", "db", "metadata", "derived", "reports", "covers"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    save_config(cfg, tmp_path)
    app = create_app(tmp_path)
    with TestClient(app) as c:
        yield c


def test_capabilities_reports_ai_off_by_default(client):
    body = client.get("/api/capabilities").json()
    assert body["ai"] is False
    assert body["ai_provider"] is None


def test_capabilities_lists_embeddable_and_convertible_formats(client):
    body = client.get("/api/capabilities").json()
    assert set(body["embeddable"]) == {"epub", "pdf"}
    assert body["convert"]["txt"] == "epub"
    assert body["convert"]["azw3"] == "epub"
    assert body["convert"]["djvu"] == "pdf"
    assert "djvu" in body["converters_available"]


def test_capabilities_reports_commit_mode(client):
    assert client.get("/api/capabilities").json()["commit_mode"] == "copy"


def test_stats_returns_status_counts(client):
    body = client.get("/api/stats").json()
    assert body["total"] == 0
    assert isinstance(body["by_status"], dict)


def test_settings_round_trip(client):
    client.put("/api/settings", json={"library.commit_mode": "move"})
    assert client.get("/api/settings").json()["library.commit_mode"] == "move"
    assert client.get("/api/capabilities").json()["commit_mode"] == "move"


def test_settings_rejects_a_key_that_is_not_settable(client):
    r = client.put("/api/settings", json={"database.path": "/etc/passwd"})
    assert r.status_code == 422


def test_settings_rejects_an_invalid_value(client):
    r = client.put("/api/settings", json={"library.commit_mode": "teleport"})
    assert r.status_code == 422


def test_settings_persist_to_config_yaml(client, tmp_path):
    import yaml

    client.put("/api/settings", json={"web.port": 9999})
    saved = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert saved["web"]["port"] == 9999


def test_setting_an_ai_provider_turns_the_capability_on(client):
    client.put("/api/settings", json={"ai.provider": "claude-cli"})
    assert client.get("/api/capabilities").json()["ai"] is True


def test_settings_change_reaches_the_sidecar_store_and_runner(client):
    r = client.put("/api/settings", json={"library.commit_mode": "move"})
    assert r.status_code == 200
    from reshelf.web.deps import AppState

    state: AppState = client.app.state.reshelf
    assert state.store.cfg.library.commit_mode == "move"
    assert state.runner.cfg.library.commit_mode == "move"
