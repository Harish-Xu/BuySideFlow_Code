import json

from benchmark_paths import resolve_benchmark_from_run_dir


def test_resolve_benchmark_from_run_dir_prefers_instances_path(tmp_path):
    benchmark_dir = tmp_path / "data" / "1"
    benchmark_dir.mkdir(parents=True)

    run_dir = tmp_path / "trajectories" / "demo_run"
    run_dir.mkdir(parents=True)

    payload = {
        "instances": {
            "path": str(benchmark_dir),
        }
    }
    # run_batch.config.yaml is currently written as a JSON string payload.
    (run_dir / "run_batch.config.yaml").write_text(
        json.dumps(json.dumps(payload)),
        encoding="utf-8",
    )

    assert resolve_benchmark_from_run_dir(run_dir) == benchmark_dir


def test_resolve_benchmark_from_run_dir_supports_yaml_wrapped_json_string(tmp_path):
    benchmark_dir = tmp_path / "data" / "1"
    benchmark_dir.mkdir(parents=True)

    run_dir = tmp_path / "trajectories" / "demo_run"
    run_dir.mkdir(parents=True)

    payload = {
        "instances": {
            "path": str(benchmark_dir),
        }
    }
    (run_dir / "run_batch.config.yaml").write_text(
        json.dumps(json.dumps(payload), indent=2),
        encoding="utf-8",
    )

    assert resolve_benchmark_from_run_dir(run_dir) == benchmark_dir


def test_resolve_benchmark_from_run_dir_returns_none_for_missing_config(tmp_path):
    run_dir = tmp_path / "trajectories" / "demo_run"
    run_dir.mkdir(parents=True)

    assert resolve_benchmark_from_run_dir(run_dir) is None
