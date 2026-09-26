from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from reshelf.config import Config
from reshelf.convert import converters
from reshelf.web.deps import AppState, get_state
from reshelf.writeback import EMBEDDABLE

router = APIRouter(tags=["meta"])

# Only these may be changed from the UI. Everything else needs config.yaml.
SETTABLE = (
    "library.commit_mode",
    "metadata.layout",
    "web.host",
    "web.port",
    "write_back.library_file",
    "write_back.embed",
    "ai.provider",
    "ai.model",
    "ai.api_key",
    "ai.base_url",
    "matching.auto_accept",
    "matching.review_below",
    "convert.timeout",
)


@router.get("/capabilities")
def capabilities(state: AppState = Depends(get_state)) -> dict:
    return {
        "ai": state.cfg.ai.enabled,
        "ai_provider": state.cfg.ai.provider,
        "commit_mode": state.cfg.library.commit_mode,
        "embeddable": sorted(EMBEDDABLE),
        "convert": dict(converters.TARGETS),
        "converters_available": converters.available(),
        "metadata_layout": state.cfg.metadata.layout,
    }


@router.get("/stats")
def stats(state: AppState = Depends(get_state)) -> dict:
    with state.db.lock:
        rows = state.db.conn.execute(
            "SELECT status, COUNT(*) AS n FROM files GROUP BY status"
        ).fetchall()
        total = state.db.conn.execute(
            "SELECT COUNT(DISTINCT sha256) FROM files WHERE sha256 IS NOT NULL"
        ).fetchone()[0]
    return {"total": total, "by_status": {r["status"]: r["n"] for r in rows}}


def _get(obj: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


@router.get("/settings")
def get_settings(state: AppState = Depends(get_state)) -> dict:
    out = {}
    for key in SETTABLE:
        value = _get(state.cfg, key)
        out[key] = str(value) if hasattr(value, "__fspath__") else value
    return out


@router.put("/settings")
def put_settings(
    payload: dict[str, Any], state: AppState = Depends(get_state)
) -> dict:
    unknown = set(payload) - set(SETTABLE)
    if unknown:
        raise HTTPException(422, f"not settable: {sorted(unknown)}")
    data = state.cfg.model_dump(mode="json")
    for key, value in payload.items():
        section, field = key.split(".", 1)
        data[section][field] = value
    try:
        new_cfg = Config.model_validate(data)
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False)) from e
    state.reload_config(new_cfg)
    return get_settings(state)
