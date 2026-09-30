from fastapi.testclient import TestClient

from reshelf.store.bootstrap import reindex
from reshelf.web import app as app_mod
from reshelf.web.app import create_app
from tests.test_api_metadata import SHA, build

ANCHOR = {"kind": "pdf-area", "page": 1, "rect": [0.1, 0.1, 0.2, 0.2]}


def test_spa_fallback_serves_shell_assets_and_json_api_404(tmp_path, monkeypatch):
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html>SPA</html>")
    (static / "assets" / "a.js").write_text("console.log(1)")
    monkeypatch.setattr(app_mod, "SPA_DIR", static)
    build(tmp_path / "lib")
    with TestClient(create_app(tmp_path / "lib")) as c:
        deep = c.get("/read/abc")
        assert deep.status_code == 200 and "SPA" in deep.text
        assert "SPA" in c.get("/jobs").text
        assert c.get("/assets/a.js").text == "console.log(1)"
        r = c.get("/api/nope")
        assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
        assert "root:" not in c.get("/..%2f..%2fetc/passwd").text


def test_annotations_survive_wiping_the_files_table(tmp_path):
    """Definition of done: rm db/books.sqlite3 && reshelf reindex leaves
    annotations reachable. reindex rebuilds book_index, not files."""
    build(tmp_path)
    with TestClient(create_app(tmp_path)) as c:
        ann = c.post(
            f"/api/books/{SHA}/annotations",
            json={"type": "highlight", "file_sha": SHA, "anchor": ANCHOR},
        ).json()
        st = c.app.state.reshelf
        st.db.conn.execute("DELETE FROM files")
        st.db.conn.commit()
        reindex(st.db, st.store, lambda *a: None)
        assert c.get(f"/api/books/{SHA}").status_code == 200
        listed = c.get(f"/api/books/{SHA}/annotations").json()
        assert [a["id"] for a in listed] == [ann["id"]]
        assert c.patch(
            f"/api/books/{SHA}/annotations/{ann['id']}", json={"note": "x"}
        ).status_code == 200
