from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from reshelf.web.api import actions, annotations, books, jobs, meta
from reshelf.web.deps import build_state

SPA_DIR = Path(__file__).parent / "static"


def create_app(root: Path) -> FastAPI:
    state = build_state(root)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state.runner.start()
        yield
        state.runner.stop()
        state.db.close()

    app = FastAPI(title="reshelf", lifespan=lifespan)
    app.state.reshelf = state
    app.include_router(meta.router, prefix="/api")
    app.include_router(books.router, prefix="/api")
    app.include_router(actions.router, prefix="/api")
    app.include_router(annotations.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    if SPA_DIR.exists():
        # StaticFiles(html=True) only serves index.html for *directory*
        # paths; an unmatched client route such as /read/<sha> would 404.
        # So: real files are served as-is, /api/* stays a JSON 404, and
        # anything else gets the SPA shell. Registered last, after /api.
        root = SPA_DIR.resolve()

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path == "api" or path.startswith("api/"):
                raise HTTPException(404, "Not Found")
            target = (root / path).resolve()
            if target.is_file() and target.is_relative_to(root):
                return FileResponse(target)
            return FileResponse(root / "index.html")
    return app
