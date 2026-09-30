from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

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
        # html=True so client-side routes fall back to index.html. Mounted
        # last (at "/") so it never shadows the /api routes above.
        app.mount("/", StaticFiles(directory=SPA_DIR, html=True), name="spa")
    return app
