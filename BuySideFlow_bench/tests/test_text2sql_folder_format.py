from pathlib import Path

from sweagent.text2sql.markdown import parse_text2sql_tasks
from sweagent.text2sql.provenance import reference_sidecar_files_from_tasks


def test_parse_folder_benchmark_collects_artifacts_and_code(tmp_path: Path):
    root = tmp_path / "bench"
    (root / "question").mkdir(parents=True)
    (root / "results" / "fund_001").mkdir(parents=True)
    (root / "results" / "fund_002").mkdir(parents=True)
    (root / "results" / "fund_003").mkdir(parents=True)
    (root / "results" / "fund_004").mkdir(parents=True)

    (root / "question" / "fund.jsonl").write_text(
        "\n".join([
            '{"instance_id":"fund_001","instruction":"table question"}',
            '{"instance_id":"fund_002","instruction":"chart question"}',
            '{"instance_id":"fund_003","instruction":"text question"}',
            '{"instance_id":"fund_004","instruction":"abstract-only question"}',
        ]),
        encoding="utf-8",
    )
    (root / "results" / "fund_001" / "refer.sql").write_text("select 1", encoding="utf-8")
    (root / "results" / "fund_001" / "result.csv").write_text("fund_code,value\n005847,1.2\n", encoding="utf-8")
    (root / "results" / "fund_001" / "candidate_cache.json").write_text("[1, 2, 3]", encoding="utf-8")
    (root / "results" / "fund_002" / "refer.py").write_text("print('chart')", encoding="utf-8")
    (root / "results" / "fund_002" / "result.csv").write_text("x,y\nA,1\n", encoding="utf-8")
    (root / "results" / "fund_002" / "picture.png").write_bytes(b"fakepng")
    (root / "results" / "fund_003" / "result.csv").write_text("x,y\nA,1\n", encoding="utf-8")
    (root / "results" / "fund_003" / "abstract.txt").write_text("summary", encoding="utf-8")
    (root / "results" / "fund_004" / "abstract.txt").write_text("summary only", encoding="utf-8")

    tasks = parse_text2sql_tasks(root)

    assert [task["id"] for task in tasks] == ["fund_001", "fund_002", "fund_003", "fund_004"]
    assert tasks[0]["evaluation_kind"] == "csv"
    assert tasks[0]["sql_code"] == "select 1"
    assert [item["kind"] for item in tasks[0]["reference_artifact_paths"]] == ["csv"]
    assert [item["name"] for item in tasks[0]["reference_sidecar_paths"]] == ["candidate_cache.json"]
    assert tasks[1]["evaluation_kind"] == "vision"
    assert {item["kind"] for item in tasks[1]["reference_artifact_paths"]} == {"csv", "image"}
    assert tasks[2]["evaluation_kind"] == "mixed"
    assert tasks[3]["evaluation_kind"] == "text_ai"
    assert [path.name for path in reference_sidecar_files_from_tasks(tasks)] == ["candidate_cache.json"]
