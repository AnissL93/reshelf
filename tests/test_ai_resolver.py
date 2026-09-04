import pytest

from reshelf.ai.prompts import build_prompt
from reshelf.ai.resolver import AIDecision, AIError, parse_decision
from reshelf.metadata.models import Author, Candidate, Edition, Work


def _cands():
    return [
        Candidate(
            provider="douban",
            provider_id="2567698",
            edition=Edition(
                work=Work(title="三体", authors=[Author(name="刘慈欣")]),
                isbn13="9787536692930",
                publisher="重庆出版社",
                publication_date="2008-1",
                language="zh",
            ),
        ),
        Candidate(
            provider="openlibrary",
            provider_id="/works/OL1W",
            edition=Edition(
                work=Work(title="The Three-Body Problem", authors=[Author(name="Liu Cixin")]),
                isbn13="9780765382030",
                language="en",
            ),
        ),
    ]


def test_build_prompt_contains_indexed_candidates():
    prompt = build_prompt(
        {"filename": "santi.epub", "title": "三体", "authors": ["刘慈欣"]},
        _cands(),
    )
    assert '"index": 0' in prompt and '"index": 1' in prompt
    assert "三体" in prompt and "The Three-Body Problem" in prompt
    assert "santi.epub" in prompt
    assert '"decision"' in prompt  # output contract is stated


def test_parse_plain_json():
    d = parse_decision(
        '{"decision": 1, "confidence": 0.9, "reasons": ["translated title"]}', 2
    )
    assert d.decision == 1 and d.confidence == 0.9


def test_parse_fenced_json_and_candidate_string():
    raw = 'Here you go:\n```json\n{"decision": "candidate_0", "confidence": 0.8, "reasons": []}\n```'
    d = parse_decision(raw, 2)
    assert d.decision == 0


def test_parse_null_decision():
    d = parse_decision('{"decision": null, "confidence": 0.2, "reasons": ["no match"]}', 2)
    assert d.decision is None


def test_parse_out_of_range_raises():
    with pytest.raises(AIError):
        parse_decision('{"decision": 5, "confidence": 0.9, "reasons": []}', 2)


def test_parse_garbage_raises():
    with pytest.raises(AIError):
        parse_decision("I think it is the first one.", 2)


def test_decision_model_defaults():
    d = AIDecision(decision=None)
    assert d.confidence == 0.0 and d.uncertainties == []
