import pandas as pd

from sweagent.text2sql.evaluator import compare_items


def test_compare_items_uses_reference_csv_artifact_and_preserves_codes(tmp_path):
    csv_path = tmp_path / "result.csv"
    csv_path.write_text("fund_code,value\n005847,1.2\n", encoding="utf-8")

    ref = {
        "id": "fund_001",
        "question": "table question",
        "mode": "python",
        "reference_artifact_paths": [{"kind": "csv", "path": str(csv_path), "name": "result.csv"}],
        "evaluation_kind": "csv",
    }
    gen = {
        "id": "fund_001",
        "question": "table question",
        "mode": "python",
        "python_code": "import pandas as pd\nresult = pd.DataFrame({'fund_code': ['005847'], 'value': [1.2]})",
        "result_vars": ["result"],
    }

    result = compare_items(ref, gen)

    assert result.passed is True
    assert result.score == 1.0


def test_compare_items_treats_blank_csv_and_missing_datetime_as_equal(tmp_path):
    csv_path = tmp_path / "result.csv"
    csv_path.write_text(
        "fund_code,start_date,end_date\n005847,2025-10-30,\n",
        encoding="utf-8",
    )

    ref = {
        "id": "fund_001",
        "question": "table question",
        "mode": "python",
        "reference_artifact_paths": [{"kind": "csv", "path": str(csv_path), "name": "result.csv"}],
        "evaluation_kind": "csv",
    }
    gen = {
        "id": "fund_001",
        "question": "table question",
        "mode": "python",
        "python_code": (
            "import pandas as pd\n"
            "result = pd.DataFrame({\n"
            "    'fund_code': ['005847.OF'],\n"
            "    'start_date': pd.to_datetime(['2025-10-30']),\n"
            "    'end_date': pd.to_datetime([None]),\n"
            "})\n"
        ),
        "result_vars": ["result"],
    }

    result = compare_items(ref, gen)

    assert result.passed is True
    assert result.score == 1.0


def test_compare_items_scores_visual_artifacts_with_deterministic_table(monkeypatch, tmp_path):
    csv_path = tmp_path / "result.csv"
    image_path = tmp_path / "picture.png"
    csv_path.write_text("x,y\nA,1\n", encoding="utf-8")
    image_path.write_bytes(b"reference-image")

    monkeypatch.setattr(
        "sweagent.text2sql.evaluator._judge_visual_checklist_ai",
        lambda **kwargs: {"chart": {"score": 1.0, "passed": True, "reason": "ok"}},
    )

    ref = {
        "id": "fund_002",
        "question": "chart question",
        "mode": "python",
        "reference_artifact_paths": [
            {"kind": "csv", "path": str(csv_path), "name": "result.csv"},
            {"kind": "image", "path": str(image_path), "name": "picture.png"},
        ],
        "evaluation_kind": "vision",
        "visual_checklist": [
            {"id": "chart", "weight": 4, "mandatory": True, "op": "visual_semantic_match", "expected": "same chart"},
        ],
    }
    gen = {
        "id": "fund_002",
        "question": "chart question",
        "mode": "python",
        "python_code": (
            "import pandas as pd\n"
            "table = pd.DataFrame({'x': ['A'], 'y': [1]})\n"
            "image = b'generated-image'\n"
        ),
        "result_vars": ["table", "image"],
    }

    result = compare_items(ref, gen)

    assert result.passed is True
    assert result.score == 1.0
    assert {item["id"] for item in result.score_details} == {"table_match", "chart"}
