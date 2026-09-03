import json
import re
import subprocess

from pydantic import BaseModel, field_validator

from book_organizer.ai.prompts import build_prompt
from book_organizer.metadata.models import Candidate


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
