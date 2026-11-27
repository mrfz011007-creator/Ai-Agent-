import json

from scripts.evaluate_openrouter_models import grade_response, parse_json_object


def test_grade_response_checks_reasoning_code_and_tool_proposal():
    response = json.dumps({
        "reasoning": {"usable_items": 288},
        "coding": {
            "code": (
                "def sum_positive_evens(values):\n"
                "    return sum(value for value in values if value > 0 and value % 2 == 0)"
            )
        },
        "tool": {"name": "baca_file", "arguments": {"nama": "notes.txt"}},
    })
    result = grade_response(response)
    assert result["score"] == 3
    assert all(result["checks"].values())


def test_grade_response_rejects_wrong_tool_and_invalid_python():
    response = json.dumps({
        "reasoning": {"usable_items": 288},
        "coding": {"code": "def sum_positive_evens("},
        "tool": {"name": "lihat", "arguments": {}},
    })
    result = grade_response(response)
    assert result["score"] == 1
    assert result["checks"]["reasoning"] is True
    assert result["checks"]["coding"] is False
    assert result["checks"]["tool_proposal"] is False


def test_grade_response_does_not_accept_boolean_as_numeric_answer():
    result = grade_response(json.dumps({
        "reasoning": {"usable_items": True},
        "coding": {"code": "def sum_positive_evens(values): return 0"},
        "tool": {"name": "baca_file", "arguments": {"nama": "notes.txt"}},
    }))
    assert result["checks"]["reasoning"] is False


def test_parse_json_object_accepts_surrounding_prose():
    assert parse_json_object('result: {"ok": true} done') == {"ok": True}
