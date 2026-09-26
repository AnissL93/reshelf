import json
import re
import subprocess

import httpx
from pydantic import BaseModel, field_validator

from reshelf.ai.prompts import build_prompt
from reshelf.metadata.models import Candidate


class AIError(Exception):
    pass


class AIDecision(BaseModel):
    decision: int | None
    confidence: float = 0.0
    reasons: list[str] = []
    uncertainties: list[str] = []

    @field_validator("decision", mode="before")
    @classmethod
    def _coerce_decision(cls, v):
        if isinstance(v, str):
            if v.lower() in ("none", "null", ""):
                return None
            m = re.fullmatch(r"candidate[_\s]?(\d+)", v.strip(), re.IGNORECASE)
            if m:
                return int(m.group(1))
        return v


def parse_decision(raw: str, n_candidates: int) -> AIDecision:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise AIError(f"no JSON object in AI output: {raw[:120]!r}")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise AIError(f"invalid JSON from AI: {e}") from e
    try:
        d = AIDecision.model_validate(data)
    except ValueError as e:
        raise AIError(f"unexpected AI output shape: {e}") from e
    if d.decision is not None and not 0 <= d.decision < n_candidates:
        raise AIError(f"decision index {d.decision} out of range (n={n_candidates})")
    return d


class ClaudeCLIResolver:
    """Resolves ambiguous matches by asking Claude via the claude CLI."""

    def __init__(self, model: str = "opus", timeout: int = 180):
        self.model = model
        self.timeout = timeout

    def _ask(self, prompt: str) -> str:
        try:
            proc = subprocess.run(
                ["claude", "-p", "--model", self.model],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except FileNotFoundError as e:
            raise AIError("claude CLI not found on PATH") from e
        except subprocess.TimeoutExpired as e:
            raise AIError(f"claude CLI timed out after {self.timeout}s") from e
        if proc.returncode != 0:
            raise AIError(
                f"claude CLI failed: {(proc.stderr or proc.stdout).strip()[:200]}"
            )
        return proc.stdout

    def resolve(self, local: dict, candidates: list[Candidate]) -> AIDecision:
        prompt = build_prompt(local, candidates)
        return parse_decision(self._ask(prompt), len(candidates))


ANTHROPIC_API_BASE = "https://api.anthropic.com"


class APIResolver:
    """Resolves ambiguous matches via the Anthropic Messages API directly."""

    def __init__(
        self,
        model: str = "haiku",
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: int = 180,
        client: httpx.Client | None = None,
    ):
        if not api_key:
            raise AIError("no API key configured (set ai.api_key or RESHELF_AI_API_KEY)")
        self.model = model
        self.api_key = api_key
        self.base_url = base_url or ANTHROPIC_API_BASE
        self.timeout = timeout
        self._client = client

    def resolve(self, local: dict, candidates: list[Candidate]) -> AIDecision:
        prompt = build_prompt(local, candidates)
        client = self._client or httpx.Client()
        try:
            resp = client.post(
                f"{self.base_url}/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=self.timeout,
            )
        except httpx.TimeoutException as e:
            raise AIError(f"Anthropic API timed out after {self.timeout}s") from e
        except httpx.HTTPError as e:
            raise AIError(f"Anthropic API request failed: {e}") from e
        finally:
            if self._client is None:
                client.close()
        if resp.status_code >= 300:
            raise AIError(f"Anthropic API returned {resp.status_code}: {resp.text[:200]}")
        try:
            text = resp.json()["content"][0]["text"]
        except (ValueError, KeyError, IndexError) as e:
            raise AIError(f"unexpected Anthropic API response shape: {e}") from e
        return parse_decision(text, len(candidates))


def build_resolver(ai_cfg) -> "ClaudeCLIResolver | APIResolver":
    """Single AI entry point: build the resolver `ai.provider` selects."""
    if ai_cfg.provider == "claude-cli":
        return ClaudeCLIResolver(model=ai_cfg.model, timeout=ai_cfg.timeout_seconds)
    if ai_cfg.provider == "api":
        return APIResolver(
            model=ai_cfg.model,
            api_key=ai_cfg.resolved_api_key,
            base_url=ai_cfg.base_url,
            timeout=ai_cfg.timeout_seconds,
        )
    raise AIError(f"unknown or unset ai.provider: {ai_cfg.provider!r}")
