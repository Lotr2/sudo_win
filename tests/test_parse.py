"""Forgiving parser: metadata JSON first, content (and code) after the delimiter, flat schema validation."""
import pytest

from core.parse import DELIM, ParseError, extract_json, parse_answer, parse_claims

SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}, "claims": {"type": "array", "items": {"type": "string"}}},
          "required": ["title", "claims"]}


@pytest.mark.parametrize("raw", [
    '```json\n{"title": "a", "claims": []}\n```',
    'Sure! {"title": "a", "claims": [],}',
    "{'title': 'a', 'claims': []}",
    '{"title": "a", "claims": [], "extra": true}',
])
def test_forgiving_json(raw):
    data, _ = parse_answer(raw, SCHEMA, require_content=False)
    assert data["title"] == "a"


def test_code_with_braces_lives_in_content_not_json():
    text = '{"title": "MVP", "claims": []}\n' + DELIM + '\n```python\nd = {"a": {"b": 1}}\n```'
    data, content = parse_answer(text, SCHEMA, require_content=True)
    assert data == {"title": "MVP", "claims": []} and 'd = {"a": {"b": 1}}' in content


def test_validation_error_names_the_field():
    with pytest.raises(ParseError, match="claims"):
        parse_answer('{"title": "a", "claims": "not a list"}', SCHEMA, require_content=False)


def test_missing_content_is_an_error_when_required():
    with pytest.raises(ParseError, match="CONTENT"):
        parse_answer('{"title": "a", "claims": []}', SCHEMA, require_content=True)


def test_no_json_is_an_error():
    with pytest.raises(ParseError):
        extract_json("I cannot help with that.")


def test_claim_strings():
    assert parse_claims(["Tutors pay for templates [source: https://x.org/a]", "Market is big [ASSUMPTION]", "Untagged"]) == [
        {"text": "Tutors pay for templates", "source_url": "https://x.org/a"},
        {"text": "Market is big", "source_url": None},
        {"text": "Untagged", "source_url": None}]
