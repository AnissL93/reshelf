from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from reshelf.config import Config
from reshelf.convert import converters
from reshelf.web.deps import AppState, get_state
from reshelf.writeback import EMBEDDABLE

router = APIRouter(tags=["meta"])

# Only these may be changed from the UI. Everything else needs config.yaml.
#
# metadata.layout is deliberately NOT here. Flipping it on a populated
# library makes every store.load(sha, path) miss: the grid keeps listing
# all of the books (book_index is layout-independent) while every detail
# view and PATCH 404s. Nothing migrates the existing metadata/*.json, and
# a later reindex would mix stale and fresh sidecars, because iter_all's
# rglob picks up both. It is a config.yaml decision, made once, before
# anything is scanned.
SETTABLE = (
    "library.commit_mode",
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


# What GET /settings returns in place of a stored ai.api_key, and what PUT
# treats as "leave it as it is". There is no auth on this app, so the key
# must not come back in plaintext just so a password field can be
# pre-filled - the UI only ever needs to know whether one is set.
SECRET_KEYS = ("ai.api_key",)
MASK = "********"


@router.get("/settings")
def get_settings(state: AppState = Depends(get_state)) -> dict:
    out = {}
    for key in SETTABLE:
        value = _get(state.cfg, key)
        if key in SECRET_KEYS:
            out[key] = MASK if value else None
            continue
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
    # The UI PUTs back everything GET handed it, mask included. Echoing
    # the mask means "unchanged", not "set my key to eight asterisks".
    payload = {
        k: v for k, v in payload.items() if not (k in SECRET_KEYS and v == MASK)
    }
    for key, value in payload.items():
        section, field = key.split(".", 1)
        data[section][field] = value
    try:
        new_cfg = Config.model_validate(data)
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False)) from e
    state.reload_config(new_cfg)
    return get_settings(state)
