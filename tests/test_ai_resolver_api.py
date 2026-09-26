import httpx
import pytest

from reshelf.ai.resolver import AIError, APIResolver, ClaudeCLIResolver, build_resolver
from reshelf.config import AIConfig
from reshelf.metadata.models import Author, Candidate, Edition, Work


def _cands():
    return [
        Candidate(
            provider="openlibrary",
            provider_id="1",
            edition=Edition(work=Work(title="Dune", authors=[Author(name="Frank Herbert")])),
        ),
    ]


def test_api_resolver_parses_a_good_response():
    def handler(request):
        assert request.headers["x-api-key"] == "sk-test"
        return httpx.Response(
            200,
            json={
                "content": [
                    {"text": '{"decision": 0, "confidence": 0.9, "reasons": ["match"]}'}
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resolver = APIResolver(model="claude-haiku", api_key="sk-test", client=client)
    decision = resolver.resolve({"title": "Dune"}, _cands())
    assert decision.decision == 0
    assert decision.confidence == 0.9


def test_api_resolver_raises_on_error_status():
    def handler(request):
        return httpx.Response(401, json={"error": "unauthorized"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resolver = APIResolver(model="claude-haiku", api_key="sk-test", client=client)
    with pytest.raises(AIError):
        resolver.resolve({"title": "Dune"}, _cands())


def test_api_resolver_requires_an_api_key():
    with pytest.raises(AIError):
        APIResolver(model="claude-haiku", api_key=None)


def test_build_resolver_selects_claude_cli():
    cfg = AIConfig(provider="claude-cli")
    assert isinstance(build_resolver(cfg), ClaudeCLIResolver)


def test_build_resolver_selects_api():
    cfg = AIConfig(provider="api", api_key="sk-test")
    assert isinstance(build_resolver(cfg), APIResolver)


def test_build_resolver_raises_when_provider_unset():
    cfg = AIConfig()
    with pytest.raises(AIError):
        build_resolver(cfg)
